"""The optional AI layer: configuration, provider calls, and proposal validation.

No test here touches the network. The HTTP call is exercised through an injected opener, so
these tests assert the *behaviour* of ArchPilot's side of the conversation: that a missing key,
an unreachable server, an HTTP error, a stale model name and a malformed reply are each turned
into a message a non-programmer can act on, and that model output is never treated as code.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from archpilot.ai import providers
from archpilot.ai.providers import (
    KIND_NONE,
    KIND_OPENAI_COMPATIBLE,
    ProviderConfig,
    build_prompt,
    complete,
    forget_api_key,
    load_config,
    propose,
    save_config,
    status,
)
from archpilot.security.secrets import get_secret, has_secret
from archpilot.spec.sample import sample_single_storey
from archpilot.storage.db import Database


@pytest.fixture()
def db(tmp_path: Path, isolated_home: Path) -> Database:
    database = Database(tmp_path / "projects.db")
    yield database
    database.close()


# ---------------------------------------------------------------------------
# A stand-in for urllib, so the tests are deterministic and offline
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self, size: int = -1) -> bytes:
        # Mirrors http.client.HTTPResponse.read, which accepts an optional byte count.
        if size is None or size < 0:
            return self._body
        return self._body[:size]

    def close(self) -> None:          # urlopen's response is a context manager *and* closable
        pass

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _Opener:
    """Records the request and returns a canned body, or raises a canned error."""

    def __init__(self, body: bytes = b"", error: Exception | None = None):
        self.body = body
        self.error = error
        self.request: urllib.request.Request | None = None
        self.timeout: int | None = None

    def __call__(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.body)


def _reply(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}
                      ).encode("utf-8")


def _local_config(**over) -> ProviderConfig:
    values = {"kind": KIND_OPENAI_COMPATIBLE, "base_url": "http://localhost:1234/v1",
              "model": "local-model", "timeout_s": 30}
    values.update(over)
    return ProviderConfig(**values)


def _remote_config(**over) -> ProviderConfig:
    values = {"kind": KIND_OPENAI_COMPATIBLE, "base_url": "https://api.example.com/v1",
              "model": "big-model", "timeout_s": 30}
    values.update(over)
    return ProviderConfig(**values)


# ---------------------------------------------------------------------------
# Default state: AI is off, and the app says so without scaring the user
# ---------------------------------------------------------------------------
def test_ai_is_off_by_default(db: Database) -> None:
    config = load_config(db)
    assert config.kind == KIND_NONE
    assert config.is_configured is False
    state = status(config)
    assert state.available is False
    assert "turned off" in state.detail
    assert "works fully without it" in state.detail


def test_a_skipped_call_is_not_an_error_the_user_has_to_act_on(db: Database) -> None:
    config = load_config(db)
    result = complete(config, "add a storey")
    assert result.skipped is True
    assert result.ok is False
    assert "turned off" in result.error


def test_propose_with_ai_off_returns_a_skipped_proposal(db: Database) -> None:
    proposal = propose(sample_single_storey(), "make it bigger", load_config(db))
    assert proposal.skipped is True
    assert proposal.operations == []


# ---------------------------------------------------------------------------
# Configuration round trip, and the key never lands in the database
# ---------------------------------------------------------------------------
def test_configuration_round_trips(db: Database) -> None:
    saved = save_config(db, _remote_config(), api_key="sk-secret-value")
    assert saved.model == "big-model"
    assert load_config(db) == saved


def test_the_api_key_is_not_written_to_the_database(db: Database) -> None:
    save_config(db, _remote_config(), api_key="sk-secret-value")
    assert has_secret(providers.API_KEY_SECRET_NAME) is True
    assert get_secret(providers.API_KEY_SECRET_NAME) == "sk-secret-value"
    blob = json.dumps(db.all_settings())
    assert "sk-secret-value" not in blob
    assert all("sk-secret" not in key for key in db.all_settings())


def test_forgetting_the_key_removes_it_from_the_secret_store(db: Database) -> None:
    save_config(db, _remote_config(), api_key="sk-secret-value")
    assert forget_api_key() is True
    assert has_secret(providers.API_KEY_SECRET_NAME) is False


def test_saving_without_a_key_argument_leaves_the_stored_key_alone(db: Database) -> None:
    save_config(db, _remote_config(), api_key="sk-keep-me")
    save_config(db, _remote_config(model="another-model"))
    assert get_secret(providers.API_KEY_SECRET_NAME) == "sk-keep-me"


def test_saving_an_empty_key_clears_it(db: Database) -> None:
    save_config(db, _remote_config(), api_key="sk-remove-me")
    save_config(db, _remote_config(), api_key="")
    assert has_secret(providers.API_KEY_SECRET_NAME) is False


def test_an_unsupported_provider_kind_is_refused(db: Database) -> None:
    with pytest.raises(ValueError, match="unsupported AI provider kind"):
        save_config(db, ProviderConfig(kind="openai_but_paid_only"))


def test_a_half_finished_provider_is_refused_rather_than_saved_broken(db: Database) -> None:
    with pytest.raises(ValueError, match="server address and a model"):
        save_config(db, ProviderConfig(kind=KIND_OPENAI_COMPATIBLE, base_url="http://x/v1"))
    with pytest.raises(ValueError, match="server address and a model"):
        save_config(db, ProviderConfig(kind=KIND_OPENAI_COMPATIBLE, model="m"))


def test_a_corrupt_stored_kind_falls_back_to_off(db: Database) -> None:
    db.set_setting(providers.SETTING_KIND, "something-else")
    assert load_config(db).kind == KIND_NONE


def test_a_silly_timeout_is_clamped_rather_than_honoured(db: Database) -> None:
    assert providers.clamps_timeout("0") == 5
    assert providers.clamps_timeout("99999") == providers.MAX_TIMEOUT_S
    assert providers.clamps_timeout("not a number") == providers.DEFAULT_TIMEOUT_S
    save_config(db, _local_config(timeout_s=0))
    assert load_config(db).timeout_s == 5


def test_the_endpoint_tolerates_a_host_only_or_a_full_url() -> None:
    assert _local_config(base_url="http://localhost:1234/v1").endpoint() == \
        "http://localhost:1234/v1/chat/completions"
    assert _local_config(base_url="http://localhost:1234/v1/").endpoint() == \
        "http://localhost:1234/v1/chat/completions"
    assert _local_config(
        base_url="http://localhost:1234/v1/chat/completions").endpoint() == \
        "http://localhost:1234/v1/chat/completions"


# ---------------------------------------------------------------------------
# Status: what exactly is missing
# ---------------------------------------------------------------------------
def test_a_local_server_needs_no_key(db: Database) -> None:
    state = status(_local_config())
    assert state.available is True
    assert state.endpoint.endswith("/chat/completions")


def test_a_remote_server_without_a_key_is_unavailable_and_says_why(db: Database) -> None:
    forget_api_key()
    state = status(_remote_config())
    assert state.available is False
    assert "No API key" in state.detail


def test_a_remote_server_with_a_key_is_available(db: Database) -> None:
    save_config(db, _remote_config(), api_key="sk-live")
    state = status(_remote_config())
    assert state.available is True


@pytest.mark.parametrize("missing,expected", [
    ("base_url", "No AI server address"),
    ("model", "No AI model name"),
])
def test_status_names_the_missing_setting(db: Database, missing: str, expected: str) -> None:
    state = status(_local_config(**{missing: ""}))
    assert state.available is False
    assert expected in state.detail


def test_loopback_and_private_addresses_count_as_local() -> None:
    for url in ("http://localhost/v1", "http://127.0.0.1:8080/v1", "http://192.168.1.9:1234/v1",
                "http://10.0.0.5/v1"):
        assert providers._looks_local(url) is True
    for url in ("https://api.openai.com/v1", "https://example.com/v1"):
        assert providers._looks_local(url) is False


# ---------------------------------------------------------------------------
# The HTTP call
# ---------------------------------------------------------------------------
def test_a_good_reply_is_returned_as_text() -> None:
    opener = _Opener(body=_reply('{"ops": []}'))
    result = complete(_local_config(), "hello", opener=opener)
    assert result.ok is True
    assert result.text == '{"ops": []}'


def test_the_request_carries_the_model_and_a_plain_json_body() -> None:
    opener = _Opener(body=_reply("ok"))
    complete(_local_config(model="llama-3"), "add a window", opener=opener)
    assert opener.request is not None
    assert opener.request.get_header("Content-type") == "application/json"
    body = json.loads(opener.request.data.decode("utf-8"))
    assert body["model"] == "llama-3"
    assert body["stream"] is False
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][-1]["content"] == "add a window"


def test_a_stored_key_is_sent_as_a_bearer_token(db: Database) -> None:
    save_config(db, _remote_config(), api_key="sk-live-token")
    opener = _Opener(body=_reply("ok"))
    complete(_remote_config(), "hello", opener=opener)
    assert opener.request.get_header("Authorization") == "Bearer sk-live-token"


def test_no_authorization_header_is_sent_when_there_is_no_key(db: Database) -> None:
    forget_api_key()
    opener = _Opener(body=_reply("ok"))
    complete(_local_config(), "hello", opener=opener)
    assert opener.request.get_header("Authorization") is None


def test_the_configured_timeout_is_passed_to_the_transport() -> None:
    opener = _Opener(body=_reply("ok"))
    complete(_local_config(timeout_s=17), "hello", opener=opener)
    assert opener.timeout == 17


def test_an_unreachable_server_gives_an_actionable_message() -> None:
    opener = _Opener(error=urllib.error.URLError(ConnectionRefusedError("connection refused")))
    result = complete(_local_config(), "hello", opener=opener)
    assert result.ok is False
    assert "Could not reach the AI server" in result.error
    assert "http://localhost:1234/v1/chat/completions" in result.error


def test_a_timeout_is_reported_as_a_timeout() -> None:
    opener = _Opener(error=TimeoutError())
    result = complete(_local_config(), "hello", opener=opener)
    assert result.ok is False
    assert "did not reply within" in result.error


@pytest.mark.parametrize("code,hint", [
    (401, "refused the key"),
    (403, "refused the key"),
    (404, "no such model"),
    (429, "rate-limiting"),
])
def test_common_http_errors_are_explained_plainly(code: int, hint: str) -> None:
    error = urllib.error.HTTPError("http://x/v1/chat/completions", code, "err", {}, None)
    result = complete(_local_config(), "hello", opener=_Opener(error=error))
    assert result.ok is False
    assert hint in result.error


def test_an_unexpected_http_error_still_reports_the_code() -> None:
    error = urllib.error.HTTPError("http://x", 500, "boom", {},
                                   _FakeResponse(b'{"error": "model crashed"}'))
    result = complete(_local_config(), "hello", opener=_Opener(error=error))
    assert "HTTP 500" in result.error
    assert "model crashed" in result.error


def test_a_reply_in_an_unexpected_shape_is_rejected_not_shown_empty() -> None:
    opener = _Opener(body=b'{"unexpected": true}')
    result = complete(_local_config(), "hello", opener=opener)
    assert result.ok is False
    assert "could not read" in result.error


def test_non_json_garbage_from_the_server_is_rejected() -> None:
    opener = _Opener(body=b"<html>proxy error</html>")
    result = complete(_local_config(), "hello", opener=opener)
    assert result.ok is False
    assert "could not read" in result.error


def test_an_oversized_reply_is_refused_rather_than_read_into_memory() -> None:
    """A hostile or broken server must not be able to exhaust memory with an endless body."""
    huge = b"x" * (providers.MAX_RESPONSE_BYTES + 1)
    result = complete(_local_config(), "hello", opener=_Opener(body=huge))
    assert result.ok is False
    assert "unexpectedly large" in result.error


def test_a_legacy_completions_reply_is_understood() -> None:
    opener = _Opener(body=json.dumps({"choices": [{"text": "legacy reply"}]}).encode())
    result = complete(_local_config(), "hello", opener=opener)
    assert result.ok is True
    assert result.text == "legacy reply"


# ---------------------------------------------------------------------------
# Reply text -> validated operations
# ---------------------------------------------------------------------------
def test_a_well_formed_reply_becomes_validated_operations() -> None:
    reply = json.dumps({"ops": [{"name": "set_storeys", "storeys": 3},
                                {"name": "set_wall_thickness", "wall_thickness_m": 0.3}]})
    proposal = propose(sample_single_storey(), "make it three storeys",
                      _local_config(), opener=_Opener(body=_reply(reply)))
    assert proposal.ok is True
    assert proposal.operations == [{"name": "set_storeys", "storeys": 3},
                                  {"name": "set_wall_thickness", "wall_thickness_m": 0.3}]
    assert proposal.rejected == []


def test_a_refused_suggestion_is_surfaced_even_when_others_are_accepted() -> None:
    """A user must never be left thinking a suggestion was applied when it was dropped."""
    reply = json.dumps({"ops": [
        {"name": "set_storeys", "storeys": 2},
        {"name": "set_wall_thickness", "thickness_m": 0.3},      # wrong argument name
    ]})
    proposal = propose(sample_single_storey(), "two storeys and thicker walls",
                       _local_config(), opener=_Opener(body=_reply(reply)))
    assert [op["name"] for op in proposal.operations] == ["set_storeys"]
    assert len(proposal.rejected) == 1
    assert "set_wall_thickness" in proposal.rejected[0]


def test_descriptive_text_around_the_json_is_tolerated() -> None:
    reply = ("Sure! Here you go:\n```json\n"
             '{"ops": [{"name": "set_storeys", "storeys": 2}]}\n```\nHope that helps.')
    proposal = propose(sample_single_storey(), "two storeys", _local_config(),
                      opener=_Opener(body=_reply(reply)))
    assert proposal.operations == [{"name": "set_storeys", "storeys": 2}]


def test_a_bare_operation_object_is_accepted() -> None:
    proposal = propose(sample_single_storey(), "two storeys", _local_config(),
                      opener=_Opener(body=_reply('{"name": "set_storeys", "storeys": 2}')))
    assert proposal.operations == [{"name": "set_storeys", "storeys": 2}]


def test_an_operation_outside_the_allow_list_is_refused() -> None:
    """The whole safety story: an op the app does not know is rejected, not attempted."""
    reply = json.dumps({"ops": [{"name": "run_python", "code": "import os; os.system('calc')"},
                                {"name": "set_storeys", "storeys": 2}]})
    proposal = propose(sample_single_storey(), "do something", _local_config(),
                      opener=_Opener(body=_reply(reply)))
    names = [op["name"] for op in proposal.operations]
    assert names == ["set_storeys"]
    assert "run_python" not in names


def test_a_reply_of_only_forbidden_operations_is_reported_as_a_refusal() -> None:
    reply = json.dumps({"ops": [{"name": "execute_shell", "cmd": "whoami"}]})
    proposal = propose(sample_single_storey(), "do something", _local_config(),
                      opener=_Opener(body=_reply(reply)))
    assert proposal.ok is False
    assert proposal.operations == []
    assert "does not allow" in proposal.error


def test_an_invalid_argument_is_refused_rather_than_clamped_silently() -> None:
    reply = json.dumps({"ops": [{"name": "set_storeys", "storeys": 9999}]})
    proposal = propose(sample_single_storey(), "a hundred storeys", _local_config(),
                      opener=_Opener(body=_reply(reply)))
    assert proposal.operations == []
    assert "does not allow" in proposal.error


def test_code_in_the_reply_is_never_executed() -> None:
    """A reply that is pure Python must be treated as unparseable text, nothing more."""
    marker = Path("should_never_be_created.txt")
    reply = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('pwned')"
    proposal = propose(sample_single_storey(), "do something", _local_config(),
                      opener=_Opener(body=_reply(reply)))
    assert proposal.operations == []
    assert proposal.ok is False
    assert marker.exists() is False
    assert "did not suggest any usable design changes" in proposal.error


def test_a_reply_with_no_operations_is_explained() -> None:
    proposal = propose(sample_single_storey(), "hello", _local_config(),
                      opener=_Opener(body=_reply("I am not sure what you mean.")))
    assert proposal.operations == []
    assert "did not suggest any usable design changes" in proposal.error


def test_an_empty_request_never_reaches_the_network() -> None:
    opener = _Opener(body=_reply("should not be called"))
    proposal = propose(sample_single_storey(), "   ", _local_config(), opener=opener)
    assert proposal.operations == []
    assert "Type what you would like" in proposal.error
    assert opener.request is None


def test_a_provider_failure_is_passed_through_as_the_reason() -> None:
    opener = _Opener(error=urllib.error.URLError(ConnectionRefusedError("refused")))
    proposal = propose(sample_single_storey(), "add a storey", _local_config(), opener=opener)
    assert proposal.operations == []
    assert "Could not reach the AI server" in proposal.error


def test_the_prompt_gives_the_model_the_current_design_as_data() -> None:
    prompt = build_prompt(sample_single_storey(), "add a storey")
    assert "Current design:" in prompt
    assert "add a storey" in prompt
    assert "Footprint:" in prompt            # from ops.describe_spec
    assert "Reply with the single JSON object" in prompt


def test_the_system_prompt_forbids_guessing_and_requires_json() -> None:
    assert "JSON only" in providers.SYSTEM_PROMPT
    assert "add_open_question" in providers.SYSTEM_PROMPT
    assert "rectangle" in providers.SYSTEM_PROMPT.lower()


def test_the_system_prompt_names_the_arguments_the_validator_accepts() -> None:
    """The prompt must not invent argument names: the validator would refuse everything."""
    from archpilot.ai import ops
    prompt = providers.SYSTEM_PROMPT
    for name, model in ops.ALLOWED_OPS.items():
        assert name in prompt, f"{name} is missing from the prompt"
        for field_name in model.model_fields:
            assert field_name in prompt, \
                f"{name}.{field_name} is missing from the prompt"


def test_the_prompt_matches_the_validator_for_a_real_suggestion() -> None:
    """End to end: a reply that follows the prompt's wording must be accepted."""
    from archpilot.ai.providers import SYSTEM_PROMPT
    reply = json.dumps({"ops": [
        {"name": "set_storey_height", "storey_height_m": 3.2},
        {"name": "set_slab_thickness", "slab_thickness_m": 0.2},
        {"name": "set_design_region", "design_region": "US"},
        {"name": "set_project_name", "project_name": "Test house"},
        {"name": "add_assumption", "text": "Flat ground assumed."},
    ]})
    proposal = propose(sample_single_storey(), "taller storeys please",
                       _local_config(), opener=_Opener(body=_reply(reply)))
    assert proposal.ok is True, proposal.error
    assert proposal.rejected == []
    assert len(proposal.operations) == 5
    assert "storey_height_m" in SYSTEM_PROMPT
