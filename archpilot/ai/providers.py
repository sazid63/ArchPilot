"""The optional AI layer: build a request, call a provider, hand back plain JSON operations.

Rules this module exists to enforce:

* **AI is optional.** With no provider configured, :func:`status` says so and every call
  returns a skipped result. Nothing in archpilot imports this module on a code path that is
  needed to model, measure, price, save or export.
* **No paid service is required.** The only supported transport is an OpenAI-compatible
  ``/chat/completions`` endpoint, which can equally be a free or self-hosted server
  (LM Studio, llama.cpp, Ollama's OpenAI shim). The app never ships a key.
* **The model's reply is data, never code.** The reply is parsed by
  :func:`archpilot.ai.ops.extract_operations` into operation dictionaries and validated
  against the fixed allow-list in :mod:`archpilot.ai.ops`. Nothing the model says is
  evaluated, imported or executed.
* **The key is a secret.** It lives in the encrypted store from
  :mod:`archpilot.security.secrets`, never in the database or the settings payload.

The HTTP call uses only the standard library, so enabling AI adds no dependency.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from typing import Any

from archpilot.ai import ops
from archpilot.security.secrets import delete_secret, get_secret, has_secret, set_secret

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
KIND_NONE = "none"
KIND_OPENAI_COMPATIBLE = "openai_compatible"

#: Where the API key is kept. Named, not positional, so the UI can offer to forget it.
API_KEY_SECRET_NAME = "ai_api_key"

#: Settings keys (non-secret parts of the configuration).
SETTING_KIND = "ai.kind"
SETTING_BASE_URL = "ai.base_url"
SETTING_MODEL = "ai.model"
SETTING_TIMEOUT = "ai.timeout_s"

DEFAULT_BASE_URL = "http://localhost:1234/v1"      # LM Studio's default, a local server
DEFAULT_TIMEOUT_S = 60
MAX_TIMEOUT_S = 300

#: Largest reply ArchPilot will read from the AI server. A hostile or misbehaving endpoint must
#: not be able to exhaust memory by streaming an endless response body.
MAX_RESPONSE_BYTES = 2_000_000

#: A deliberately small reply budget. Operations are short JSON objects; a model that needs
#: more than this to answer is doing something other than proposing design changes.
MAX_OUTPUT_TOKENS = 2000

SYSTEM_PROMPT = """\
You are the design assistant inside ArchPilot, a preliminary building-estimating tool.

You translate a user's plain-language request into structured design operations.

Reply with ONE JSON object and nothing else, in exactly this shape:

{"ops": [{"name": "<operation>", ...}]}

The only operations you may use, with their exact argument names:
  design_rectangular_building: width_m, depth_m, storeys, storey_height_m,
      wall_thickness_m, slab_thickness_m, doors_south, windows_per_wall,
      roof_type, units, design_region, project_name
  set_footprint: width_m, depth_m
  set_storeys: storeys
  set_storey_height: storey_height_m
  set_wall_thickness: wall_thickness_m
  set_slab_thickness: slab_thickness_m
  set_roof_type: roof_type
  set_units: units
  set_design_region: design_region
  set_project_name: project_name
  add_assumption: text
  add_open_question: text

Use those argument names exactly. An argument name that is not listed will be refused.
Metres are the unit used for every length.

Rules:
  * Only rectangular footprints are possible. If the user asks for any other shape, use
    add_open_question to say that a hand edit in the design view is needed.
  * All shapes beyond a rectangle must be raised as an open question, never guessed.
  * If the request is unclear or you would have to invent a structural detail, do not guess:
    use add_open_question instead.
  * Never invent a price, a rate, or a construction method.
  * Output JSON only. No prose, no markdown, no code fences.
"""


@dataclass(frozen=True)
class ProviderConfig:
    """Non-secret AI settings. The API key is stored separately."""

    kind: str = KIND_NONE
    base_url: str = ""
    model: str = ""
    timeout_s: int = DEFAULT_TIMEOUT_S

    @property
    def is_configured(self) -> bool:
        return self.kind == KIND_OPENAI_COMPATIBLE and bool(self.base_url and self.model)

    def endpoint(self) -> str:
        """The chat-completions URL, tolerating a user who typed the host only."""
        base = self.base_url.strip().rstrip("/")
        if not base:
            return ""
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def with_key_present(self) -> bool:
        return has_secret(API_KEY_SECRET_NAME)


@dataclass
class ProviderStatus:
    """Plain answer to 'can the assistant do anything right now?'."""

    available: bool
    kind: str = KIND_NONE
    model: str = ""
    endpoint: str = ""
    detail: str = ""

    @property
    def summary(self) -> str:
        if self.available:
            return f"AI assistant available ({self.model})."
        return self.detail or "AI assistant is not set up."


@dataclass
class ChatResult:
    """The outcome of one attempt to talk to a provider."""

    ok: bool = False
    text: str = ""
    error: str = ""
    skipped: bool = False

    @property
    def summary(self) -> str:
        if self.skipped:
            return self.error or "AI was skipped; everything else still works."
        if self.ok:
            return "AI replied."
        return self.error or "The AI did not reply."


@dataclass
class Proposal:
    """Validated operations the model asked for, ready for the user to accept or reject."""

    operations: list[dict[str, Any]]
    reply_text: str = ""
    error: str = ""
    skipped: bool = False
    #: Suggestions the app refused, with the reason. Kept so nothing is dropped silently.
    rejected: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.operations) and not self.error


# ---------------------------------------------------------------------------
# Reading and writing the configuration
# ---------------------------------------------------------------------------
def clamps_timeout(value: Any) -> int:
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S
    return max(5, min(MAX_TIMEOUT_S, seconds))


def load_config(db: Any) -> ProviderConfig:
    """Read the saved configuration. Never raises — bad values fall back to defaults."""
    kind = db.get_setting(SETTING_KIND, KIND_NONE) or KIND_NONE
    if kind not in (KIND_NONE, KIND_OPENAI_COMPATIBLE):
        kind = KIND_NONE
    return ProviderConfig(
        kind=kind,
        base_url=db.get_setting(SETTING_BASE_URL, ""),
        model=db.get_setting(SETTING_MODEL, ""),
        timeout_s=clamps_timeout(db.get_setting(SETTING_TIMEOUT, DEFAULT_TIMEOUT_S)),
    )


def save_config(db: Any, config: ProviderConfig, api_key: str | None = None) -> ProviderConfig:
    """Persist the configuration. ``api_key=None`` leaves any stored key untouched.

    Passing an empty string removes the stored key, which is how the UI turns AI off for good.
    """
    if config.kind not in (KIND_NONE, KIND_OPENAI_COMPATIBLE):
        raise ValueError(f"unsupported AI provider kind: {config.kind!r}")
    if config.kind == KIND_OPENAI_COMPATIBLE and not (config.base_url.strip()
                                                      and config.model.strip()):
        raise ValueError("an OpenAI-compatible provider needs both a server address and a model")

    clean = replace(config, base_url=config.base_url.strip(), model=config.model.strip(),
                    timeout_s=clamps_timeout(config.timeout_s))
    db.set_setting(SETTING_KIND, clean.kind)
    db.set_setting(SETTING_BASE_URL, clean.base_url)
    db.set_setting(SETTING_MODEL, clean.model)
    db.set_setting(SETTING_TIMEOUT, str(clean.timeout_s))

    if api_key is not None:
        if api_key.strip():
            set_secret(API_KEY_SECRET_NAME, api_key.strip())
        else:
            delete_secret(API_KEY_SECRET_NAME)
    return clean


def forget_api_key() -> bool:
    return delete_secret(API_KEY_SECRET_NAME)


def status(config: ProviderConfig) -> ProviderStatus:
    """Whether the assistant can be used, and if not, exactly what is missing."""
    if config.kind == KIND_NONE:
        return ProviderStatus(False, KIND_NONE,
                              detail="AI assistant is turned off. ArchPilot works fully "
                                     "without it — modelling, quantities, costs and exports "
                                     "are all local.")
    if not config.base_url:
        return ProviderStatus(False, config.kind, config.model,
                              detail="No AI server address is set.")
    if not config.model:
        return ProviderStatus(False, config.kind, "", config.endpoint(),
                              detail="No AI model name is set.")
    endpoint = config.endpoint()
    if not has_secret(API_KEY_SECRET_NAME) and not _looks_local(endpoint):
        return ProviderStatus(False, config.kind, config.model, endpoint,
                              detail="No API key is stored for this AI server. Add one, or "
                                     "point the address at a local server that needs none.")
    return ProviderStatus(True, config.kind, config.model, endpoint,
                          detail=f"AI assistant available ({config.model}).")


def _looks_local(url: str) -> bool:
    """True for addresses that are plainly on this machine, which usually need no key."""
    host = url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    return host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"} \
        or host.startswith(("192.168.", "10."))


# ---------------------------------------------------------------------------
# Talking to the provider
# ---------------------------------------------------------------------------
def _build_request_body(config: ProviderConfig, system: str, user: str) -> bytes:
    payload = {
        "model": config.model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.0,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "stream": False,
    }
    return json.dumps(payload).encode("utf-8")


def _reply_text(body: bytes) -> str:
    """Pull the assistant text out of an OpenAI-style response.

    Returns "" when the payload is not the shape we expect, so the caller reports an honest
    error instead of showing the user an empty reply.
    """
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(first.get("text"), str):        # legacy completions shape
                return first["text"]
    return ""


def complete(config: ProviderConfig, user_message: str, *,
             system: str = SYSTEM_PROMPT, opener: Any = None) -> ChatResult:
    """Send one message and return the raw reply text.

    Never raises and never returns code: a missing configuration, an unreachable server, a
    timeout, an HTTP error and a malformed reply are all reported as an unsuccessful result
    with a message a non-programmer can act on. ``opener`` is injectable for tests.
    """
    state = status(config)
    if not state.available:
        return ChatResult(skipped=True, error=state.detail)

    request = urllib.request.Request(
        state.endpoint,
        data=_build_request_body(config, system, user_message),
        headers={"Content-Type": "application/json", **_auth_headers()},
        method="POST",
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=config.timeout_s) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as e:
        return ChatResult(error=_http_error_message(e))
    except urllib.error.URLError as e:
        return ChatResult(error=f"Could not reach the AI server at {state.endpoint}: "
                                f"{_clean(e.reason)}")
    except TimeoutError:
        return ChatResult(error=f"The AI server did not reply within {config.timeout_s} s.")
    except OSError as e:
        return ChatResult(error=f"Could not reach the AI server: {_clean(e)}")

    if len(body) > MAX_RESPONSE_BYTES:
        return ChatResult(error="The AI server sent an unexpectedly large reply, so ArchPilot "
                                "ignored it. Check that the address points at an "
                                "OpenAI-compatible chat endpoint.")

    text = _reply_text(body)
    if not text.strip():
        return ChatResult(error="The AI server replied in a format ArchPilot could not read. "
                                "Check that the address points at an OpenAI-compatible "
                                "/chat/completions endpoint.")
    return ChatResult(ok=True, text=text)


def _auth_headers() -> dict[str, str]:
    key = get_secret(API_KEY_SECRET_NAME)
    return {"Authorization": f"Bearer {key}"} if key else {}


def _http_error_message(error: urllib.error.HTTPError) -> str:
    if error.code in (401, 403):
        return ("The AI server refused the key. Check the API key, or clear it if the server "
                "is local and needs none.")
    if error.code == 404:
        return ("The AI server has no such model or endpoint. Check the model name and that "
                "the address ends with /v1.")
    if error.code == 429:
        return "The AI server is rate-limiting requests. Wait a moment and try again."
    detail = _read_http_error_body(error)
    return f"The AI server returned HTTP {error.code}{': ' + detail if detail else '.'}"


def _read_http_error_body(error: urllib.error.HTTPError) -> str:
    try:
        return _clean(error.read(MAX_RESPONSE_BYTES).decode("utf-8", "replace"))[:300]
    except Exception:
        return ""


def _clean(value: Any) -> str:
    return " ".join(str(value).split())


# ---------------------------------------------------------------------------
# Request -> validated operations
# ---------------------------------------------------------------------------
def propose(spec: Any, user_message: str, config: ProviderConfig, *,
            system: str = SYSTEM_PROMPT, opener: Any = None) -> Proposal:
    """Ask the model for operations and validate them against the allow-list.

    The returned operations are plain dictionaries that have already been refused by
    :mod:`archpilot.ai.ops` if they are not allowed. They are still only a *proposal*: the
    caller decides whether to apply them, and applies them through
    :func:`archpilot.ai.ops.apply_operations`, never by executing model output.
    """
    if not user_message.strip():
        return Proposal([], error="Type what you would like the building to do.")

    prompt = build_prompt(spec, user_message)
    reply = complete(config, prompt, system=system, opener=opener)
    if reply.skipped:
        return Proposal([], error=reply.error, skipped=True)
    if not reply.ok:
        return Proposal([], error=reply.error)

    candidates, parse_error = ops.extract_operations(reply.text)
    if not candidates:
        return Proposal([], reply_text=reply.text,
                        error=_no_operations_message(parse_error))

    validated = ops.validate_operations(candidates)
    accepted = [op.validated.model_dump() for op in validated if op.is_valid and op.validated]
    rejected = [f"{op.name or 'unnamed'}: {op.reason}" for op in validated if not op.is_valid]

    if not accepted:
        return Proposal([], reply_text=reply.text, rejected=rejected,
                        error="The AI suggested changes ArchPilot does not allow: "
                              + "; ".join(rejected))
    return Proposal(accepted, reply_text=reply.text, rejected=rejected)


def _no_operations_message(parse_error: str) -> str:
    """A readable reason for an unusable reply, keeping the parser's detail available."""
    friendly = "The AI did not suggest any usable design changes."
    return f"{friendly} ({parse_error})" if parse_error else friendly


def build_prompt(spec: Any, user_message: str) -> str:
    """Current design plus the request, as data the model must answer about."""
    return (f"Current design:\n{ops.describe_spec(spec)}\n\n"
            f"User request:\n{user_message.strip()}\n\n"
            f"Reply with the single JSON object described in your instructions.")
