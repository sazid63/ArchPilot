"""Local secret storage for optional AI credentials.

Rules this module enforces:

* **No plaintext.** On Windows, values are wrapped with DPAPI (``CryptProtectData``) bound to
  the current user *and* machine, with an app-specific entropy so only ArchPilot unwraps them.
* **Never in the database.** Secrets live in their own file so sending someone a project
  database, a backup, or an exported BOQ cannot leak a key.
* **Never shown.** Callers get the value back only when they need to make a request; the UI
  only ever reports presence.

The file format is ``{"name": "<base64 wrapped blob>", ...}``. An absent or unreadable file
is treated as "no secrets", not as an error.
"""
from __future__ import annotations

import base64
import contextlib
import ctypes
import json
import os
import stat
import sys
from ctypes import wintypes
from pathlib import Path

from archpilot.branding import APP_NAME
from archpilot.core.paths import ensure_dirs, get_logger

log = get_logger("secrets")

SECRETS_FILENAME = "secrets.bin"
_MAX_LENGTH = 10_000

# App-specific entropy: even if another program runs as the same user, it cannot unwrap our
# blobs without knowing this.
_ENTROPY = (APP_NAME + "|v1").encode("utf-8")


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _is_windows() -> bool:
    return sys.platform == "win32"


def _protect_win32(data: bytes) -> bytes:
    src = ctypes.create_string_buffer(data)
    out = _DataBlob()
    ent = ctypes.create_string_buffer(_ENTROPY)
    ent_blob = _DataBlob(len(_ENTROPY), ctypes.cast(ent, ctypes.POINTER(ctypes.c_byte)))
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(_DataBlob(len(data), ctypes.cast(src, ctypes.POINTER(ctypes.c_byte)))),
        APP_NAME.encode("utf-16-le"),
        ctypes.byref(ent_blob), None, None, 0x1,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out))
    if not ok:
        raise OSError("Windows refused to encrypt the secret (CryptProtectData failed)")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _unprotect_win32(blob: bytes) -> bytes:
    src = ctypes.create_string_buffer(blob)
    out = _DataBlob()
    ent = ctypes.create_string_buffer(_ENTROPY)
    ent_blob = _DataBlob(len(_ENTROPY), ctypes.cast(ent, ctypes.POINTER(ctypes.c_byte)))
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(_DataBlob(len(blob), ctypes.cast(src, ctypes.POINTER(ctypes.c_byte)))),
        None, ctypes.byref(ent_blob), None, None, 0x1, ctypes.byref(out))
    if not ok:
        raise OSError("Windows refused to decrypt the secret (it may belong to "
                      "another user, machine or an older ArchPilot install)")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _fallback_key_path() -> Path:
    return ensure_dirs() / ".machine_key"


def _fallback_key() -> bytes:
    """Non-Windows fallback: a per-install random key kept beside the secrets file.

    Weaker than DPAPI (any process running as this user can read it), but it keeps the API
    working and never stores values in plaintext.
    """
    key_path = _fallback_key_path()
    if key_path.is_file():
        return key_path.read_bytes()
    key = os.urandom(32)
    key_path.write_bytes(key)
    with contextlib.suppress(OSError):
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
    return key


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


# Public API
def _wrap(value: str) -> bytes:
    raw = value.encode("utf-8")
    if _is_windows():
        return _protect_win32(raw)
    return _xor(raw, _fallback_key())


def _unwrap(blob: bytes) -> str:
    if _is_windows():
        return _unprotect_win32(blob).decode("utf-8")
    return _xor(blob, _fallback_key()).decode("utf-8")


def _secrets_path() -> Path:
    return ensure_dirs() / SECRETS_FILENAME


def _load() -> dict[str, str]:
    path = _secrets_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("secret store unreadable — treating as empty")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _store(data: dict[str, str]) -> None:
    path = _secrets_path()
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def set_secret(name: str, value: str) -> None:
    if not name:
        raise ValueError("secret name is required")
    if value is None or value == "":
        delete_secret(name)
        return
    if len(value) > _MAX_LENGTH:
        raise ValueError("secret value is too long")
    data = _load()
    data[name] = base64.b64encode(_wrap(value)).decode("ascii")
    _store(data)
    log.info("stored secret %s", name)


def get_secret(name: str) -> str | None:
    data = _load()
    if name not in data:
        return None
    try:
        return _unwrap(base64.b64decode(data[name]))
    except Exception:
        # Corrupt, or written by a different user/install. Do not crash the app.
        log.warning("could not read secret %s", name)
        return None


def has_secret(name: str) -> bool:
    data = _load()
    if name not in data:
        return False
    return _unwrap_safe(data[name]) is not None


def _unwrap_safe(b64: str) -> str | None:
    try:
        return _unwrap(base64.b64decode(b64))
    except Exception:
        return None


def delete_secret(name: str) -> bool:
    data = _load()
    if name in data:
        data.pop(name)
        _store(data)
        return True
    return False


def secret_names() -> list[str]:
    return sorted(_load())


def clear_all() -> int:
    data = _load()
    if not data:
        return 0
    _store({})
    log.info("cleared %d stored secret(s)", len(data))
    return len(data)
