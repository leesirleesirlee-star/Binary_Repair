"""Server-side storage for the optional assistant provider key.

The key is configured from the local operator UI, never returned to the browser,
and never written to logs.  On Windows it is protected with DPAPI (current-user
scope).  On other platforms the fallback is a 0600 file and the status reports
that protection level honestly.

Environment variable ``BINARYREPAIR_ASSISTANT_KEY_FILE`` can point at a test or
deployment-specific file.  The provider environment variable still wins over
this store when both are present.
"""

from __future__ import annotations

import base64
import json
import ctypes
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

_KEY_ENV = "BINARYREPAIR_ASSISTANT_PROVIDER_KEY"
_COMPAT_ENVS = ("DEEPSEEK_API_KEY",)
_FILE_ENV = "BINARYREPAIR_ASSISTANT_KEY_FILE"
_MAGIC_DPAPI = b"BRKEY1\n"
_MAGIC_PLAIN = b"BRKEY0\n"
_MAGIC_CRED = b"BRKEY2\n"
_USERNAME_ENV = "BINARYREPAIR_ASSISTANT_USERNAME"
_MAX_KEY_LEN = 512
_MIN_KEY_LEN = 16
_MAX_USERNAME_LEN = 80
_SAVE_LOCK = threading.Lock()


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def key_path() -> Path:
    raw = (os.environ.get(_FILE_ENV) or "").strip()
    return Path(raw) if raw else _root() / "var" / "assistant.key"


def _env_key() -> str:
    for name in (_KEY_ENV, *_COMPAT_ENVS):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _is_windows() -> bool:
    return sys.platform.startswith("win")


# Windows DPAPI -----------------------------------------------------------------
if _is_windows():  # pragma: no cover - exercised on Windows
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
    _CRYPTPROTECT_UI_FORBIDDEN = 0x01

    # Explicit signatures are required on 64-bit Python; without them ctypes
    # truncates the DATA_BLOB pointers and CryptUnprotectData fails.
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    _kernel32.LocalFree.restype = wintypes.HLOCAL
    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]

    def _blob(data: bytes) -> tuple[_DATA_BLOB, ctypes.Array[ctypes.c_char]]:
        buffer = ctypes.create_string_buffer(data, len(data))
        return _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    def _protect(data: bytes) -> bytes:
        in_blob, _buffer = _blob(data)
        out_blob = _DATA_BLOB()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "BinaryRepair assistant API key",
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            _kernel32.LocalFree(out_blob.pbData)

    def _unprotect(data: bytes) -> bytes:
        in_blob, _buffer = _blob(data)
        out_blob = _DATA_BLOB()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            _kernel32.LocalFree(out_blob.pbData)

    def _protect_for_storage(key: str) -> tuple[bytes, str]:
        return _MAGIC_DPAPI + _protect(key.encode("utf-8")), "dpapi-user"

    def _unprotect_from_storage(raw: bytes) -> str:
        return _unprotect(raw[len(_MAGIC_DPAPI) :]).decode("utf-8")

else:  # pragma: no cover - CI may run here
    def _protect_for_storage(key: str) -> tuple[bytes, str]:
        return _MAGIC_PLAIN + key.encode("utf-8"), "mode-0600"

    def _unprotect_from_storage(raw: bytes) -> str:
        return raw[len(_MAGIC_PLAIN) :].decode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(name)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        if not path.is_file():
            raise OSError("key replacement did not produce a file")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _validate_key(key: str) -> None:
    if len(key) < _MIN_KEY_LEN:
        raise ValueError("API key is too short")
    if len(key) > _MAX_KEY_LEN:
        raise ValueError("API key is too long")
    if any(ch.isspace() for ch in key):
        raise ValueError("API key must not contain whitespace")
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in key):
        raise ValueError("API key must be printable ASCII")


def _validate_username(username: str) -> str:
    value = (username or "").strip()
    if not value:
        raise ValueError("username is empty")
    if len(value) > _MAX_USERNAME_LEN:
        raise ValueError("username is too long")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("username must not contain control characters")
    return value


def save_key(key: str, username: str = "") -> dict[str, Any]:
    key = (key or "").strip()
    if not key:
        raise ValueError("API key is empty")
    _validate_key(key)
    # Username is a display identity for this local credential; the key remains
    # the only secret. Both travel inside the protected payload.
    username = (username or "").strip()
    if username:
        username = _validate_username(username)
    payload_obj = {"username": username, "key": key}
    raw = json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload, protection = _protect_for_storage(raw.decode("utf-8"))
    if protection == "mode-0600":
        payload = _MAGIC_CRED + base64.b64encode(raw)
    else:
        payload = _MAGIC_CRED + payload[len(_MAGIC_DPAPI):]
    with _SAVE_LOCK:
        _atomic_write(key_path(), payload)
    return {"configured": True, "protection": protection, "username": username}


def _read_credential() -> tuple[str, str]:
    path = key_path()
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError, OSError):
        return "", ""
    try:
        if raw.startswith(_MAGIC_CRED):
            body = raw[len(_MAGIC_CRED):]
            if _is_windows():
                body = _unprotect(body)
            else:
                body = base64.b64decode(body)
            obj = json.loads(body.decode("utf-8"))
            if not isinstance(obj, dict):
                return "", ""
            username = obj.get("username") if isinstance(obj.get("username"), str) else ""
            key = obj.get("key") if isinstance(obj.get("key"), str) else ""
            return username, key.strip()
        if raw.startswith(_MAGIC_DPAPI):
            if not _is_windows():
                return "", ""
            return "", _unprotect_from_storage(raw).strip()
        if raw.startswith(_MAGIC_PLAIN):
            encoded = raw[len(_MAGIC_PLAIN):]
            return "", base64.b64decode(encoded).decode("utf-8").strip()
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return "", ""
    return "", ""


def load_key() -> str:
    return _read_credential()[1]


def load_username() -> str:
    return _read_credential()[0]


def clear_key() -> bool:
    path = key_path()
    with _SAVE_LOCK:
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False



_PROTECTED_NAMES = {"assistant.key", ".env", "session.token"}


def _normalized_secret_name(name: str) -> str:
    value = name.casefold()
    if "::" in value:
        value = value.split("::", 1)[0]
    return value.rstrip(" .")


def is_secret_file(path: Path) -> bool:
    """True for configured key files and literal secret files.

    The literal-name guard is intentionally independent of the environment so
    ``assistant.key`` / ``.env`` / ``session.token`` are always refused, even
    when the adapter runs with a different or missing key-file setting.
    ``samefile`` catches hardlinks and case/alias paths on Windows.
    """
    name = _normalized_secret_name(path.name)
    if name in _PROTECTED_NAMES:
        return True
    try:
        target = key_path()
        if path.resolve() == target.resolve():
            return True
        if path.exists() and target.exists() and os.path.samefile(path, target):
            return True
    except OSError:
        pass
    return bool(name) and name == _normalized_secret_name(key_path().name)

def mask_key(key: str) -> str:
    if not key:
        return ""
    return "*" * max(0, len(key) - 4) + key[-4:]


def key_status() -> dict[str, Any]:
    env_key = _env_key()
    stored_username, stored = _read_credential()
    key = env_key or stored
    username = ""
    if env_key:
        source = "environment"
        protection = "environment"
        username = (os.environ.get(_USERNAME_ENV) or "").strip()
    elif stored:
        source = "stored"
        protection = "dpapi-user" if _is_windows() else "mode-0600"
        username = stored_username
    else:
        source = None
        protection = None
    return {
        "configured": bool(key),
        "source": source,
        "username": username,
        "masked": mask_key(key),
        "protection": protection,
        "can_clear": bool(stored and not env_key),
        "mode": (os.environ.get("BINARYREPAIR_ASSISTANT_MODE") or "deterministic").strip().lower(),
        "model": (os.environ.get("BINARYREPAIR_ASSISTANT_MODEL") or "deepseek-chat").strip(),
    }
