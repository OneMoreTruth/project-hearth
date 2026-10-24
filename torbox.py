"""Small TorBox client and Windows DPAPI credential storage for Hearth."""
from __future__ import annotations

import ctypes
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.torbox.app/v1/api"


def _credential_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    path = root / "Project Hearth" / "torbox.cred"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _protect(raw: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise OSError("TorBox credentials can only be stored securely on Windows.")
    source_buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source = _Blob(len(raw), source_buffer)
    result = _Blob()
    crypt = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    crypt.restype = ctypes.c_int
    if decrypt:
        crypt.argtypes = [ctypes.POINTER(_Blob), ctypes.POINTER(ctypes.c_wchar_p), ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_Blob)]
        ok = crypt(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        crypt.argtypes = [ctypes.POINTER(_Blob), ctypes.c_wchar_p, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_Blob)]
        ok = crypt(ctypes.byref(source), "Project Hearth TorBox token", None, None, None, 1, ctypes.byref(result))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def save_token(token: str) -> None:
    _credential_path().write_bytes(_protect(token.encode("utf-8")))


def load_token() -> str | None:
    path = _credential_path()
    if not path.exists():
        return None
    try:
        return _protect(path.read_bytes(), decrypt=True).decode("utf-8")
    except Exception:
        return None


def clear_token() -> None:
    _credential_path().unlink(missing_ok=True)


def request(method: str, endpoint: str, token: str | None = None, params: dict | None = None, body: dict | None = None) -> dict:
    url = API + endpoint
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/json", "User-Agent": "ProjectHearth/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            raw = response.read(4_000_000)
    except urllib.error.HTTPError as exc:
        detail = exc.read(2000).decode("utf-8", "replace")
        try:
            parsed = json.loads(detail)
            detail = str(parsed.get("detail") or parsed.get("error") or parsed.get("message") or detail)
        except Exception:
            pass
        raise RuntimeError(f"TorBox returned HTTP {exc.code}: {detail[:300]}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not reach TorBox: {getattr(exc, 'reason', exc)}") from None
    try:
        parsed = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise RuntimeError("TorBox returned an unreadable response.") from None
    if isinstance(parsed, dict) and parsed.get("success") is False:
        raise RuntimeError(str(parsed.get("detail") or parsed.get("error") or parsed.get("message") or "TorBox request failed"))
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def start_device_auth() -> dict:
    return request("GET", "/user/auth/device/start", params={"app": "Project Hearth"})


def poll_device_auth(device_code: str) -> dict:
    return request("POST", "/user/auth/device/token", body={"device_code": device_code})


def list_torrents(token: str) -> list[dict]:
    result = request("GET", "/torrents/mylist", token=token, params={"limit": 100, "offset": 0})
    data = result.get("data", [])
    return data if isinstance(data, list) else []


def create_stream(token: str, torrent_id: int | str, file_id: int | str) -> str:
    result = request("GET", "/stream/createstream", token=token, params={"id": torrent_id, "file_id": file_id, "type": "torrent"})
    data = result.get("data", result)
    if isinstance(data, dict):
        for key in ("url", "stream_url", "download_url", "link"):
            url = data.get(key)
            if isinstance(url, str) and url.startswith("https://"):
                return url
        stream_token = data.get("file_token") or data.get("token")
        presigned = data.get("presigned_token")
        if stream_token and presigned:
            stream = request("GET", "/stream/getstreamdata", token=token, params={"token": stream_token, "presigned_token": presigned})
            payload = stream.get("data", stream)
            if isinstance(payload, dict):
                for key in ("url", "stream_url", "download_url", "link"):
                    url = payload.get(key)
                    if isinstance(url, str) and url.startswith("https://"):
                        return url
    raise RuntimeError("TorBox did not return a playable stream URL for this file.")


def resolve_hash(token: str, info_hash: str, title: str = "", file_index: int | None = None) -> str:
    """Ask TorBox to resolve a Stremio torrent stream, only if it is cached."""
    digest = re.sub(r"[^0-9a-fA-F]", "", info_hash)
    if len(digest) != 40:
        raise RuntimeError("This add-on stream does not contain a valid torrent hash.")
    magnet = f"magnet:?xt=urn:btih:{digest}&dn={urllib.parse.quote(title or 'Hearth stream')}"
    form = urllib.parse.urlencode({"magnet": magnet, "add_only_if_cached": "true", "allow_zip": "false"}).encode()
    req = urllib.request.Request(API + "/torrents/createtorrent", data=form, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = json.loads(response.read(2_000_000) or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read(1500).decode("utf-8", "replace")
        raise RuntimeError(f"TorBox could not resolve this stream (HTTP {exc.code}): {detail[:250]}") from None
    data = raw.get("data", raw) if isinstance(raw, dict) else {}
    torrent_id = data.get("torrent_id", data.get("id")) if isinstance(data, dict) else None
    if not torrent_id:
        # Duplicate cached items may be reported without an ID; look it up by hash.
        listed = list_torrents(token)
        item = next((x for x in listed if str(x.get("hash", "")).casefold() == digest.casefold()), None)
        torrent_id = item.get("id") if item else None
    if not torrent_id:
        raise RuntimeError(str(raw.get("detail") or "TorBox did not provide an account item for this cached stream."))
    if file_index is None:
        file_index = 0
    try:
        return create_stream(token, torrent_id, file_index)
    except RuntimeError:
        pass
    # TorBox's requestdl redirect is a short-lived CDN URL; the token is sent in
    # the Authorization header rather than embedding it in the returned URL.
    # TorBox's requestdl endpoint requires its API token as a query parameter.
    # The request is made server-side over HTTPS; only the resulting short-lived
    # CDN URL is returned to the player.
    params = urllib.parse.urlencode({"token": token, "torrent_id": torrent_id, "file_id": file_index, "redirect": "true"})
    request_url = API + "/torrents/requestdl?" + params
    req = urllib.request.Request(request_url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            final_url = response.geturl()
            if final_url.startswith("https://") and "api.torbox.app/v1/api/torrents/requestdl" not in final_url:
                return final_url
            payload = json.loads(response.read(1_000_000) or b"{}")
            payload = payload.get("data", payload) if isinstance(payload, dict) else {}
            if isinstance(payload, dict):
                for key in ("url", "download_url", "link"):
                    if isinstance(payload.get(key), str) and payload[key].startswith("https://"):
                        return payload[key]
    except urllib.error.HTTPError as exc:
        detail = exc.read(1500).decode("utf-8", "replace")
        raise RuntimeError(f"TorBox could not start this stream (HTTP {exc.code}): {detail[:250]}") from None
    raise RuntimeError("TorBox did not return a playable URL. The item may not be cached or available on your plan.")
