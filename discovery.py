"""TMDB and Stremio-compatible discovery adapters used by the desktop host."""
from __future__ import annotations

import json
import base64
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import server

TMDB = "https://api.themoviedb.org/3"
TORBOX_SEARCH = "https://search-api.torbox.app"


def _json(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "ProjectHearth/1.0", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(5_000_000)
        return json.loads(raw or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", "replace")
        raise RuntimeError(f"Discovery service returned HTTP {exc.code}: {detail[:200]}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not reach discovery service: {getattr(exc, 'reason', exc)}") from None
    except json.JSONDecodeError:
        raise RuntimeError("Discovery service returned invalid JSON.") from None


def settings() -> dict:
    with server.db() as conn:
        rows = {r["key"]: r["value"] for r in conn.execute("SELECT key,value FROM preferences WHERE key IN ('tmdb_key_dpapi','stremio_addons_dpapi')")}
    addons = []
    if rows.get("stremio_addons_dpapi"):
        from torbox import _protect
        try:
            addons = json.loads(_protect(base64.b64decode(rows["stremio_addons_dpapi"]), decrypt=True).decode("utf-8"))
        except Exception:
            addons = []
    from torbox import load_token
    return {"tmdb_configured": bool(rows.get("tmdb_key_dpapi")), "torbox_connected": bool(load_token()), "addons": addons}


def save_settings(tmdb_key: str | None = None, addons: list[dict] | None = None) -> None:
    with server.db() as conn:
        if tmdb_key is not None:
            from torbox import _protect
            encrypted = base64.b64encode(_protect(tmdb_key.strip().encode("utf-8"))).decode("ascii") if tmdb_key.strip() else ""
            conn.execute("INSERT INTO preferences(key,value) VALUES('tmdb_key_dpapi',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (encrypted,))
        if addons is not None:
            from torbox import _protect
            clean = []
            for addon in addons:
                url = str(addon.get("manifest", "")).strip()
                if not url.startswith(("https://", "http://")):
                    continue
                clean.append({"name": str(addon.get("name", "Stremio add-on"))[:100], "manifest": url})
            encrypted_addons = base64.b64encode(_protect(json.dumps(clean).encode("utf-8"))).decode("ascii")
            conn.execute("INSERT INTO preferences(key,value) VALUES('stremio_addons_dpapi',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (encrypted_addons,))


def _tmdb_key() -> str:
    with server.db() as conn:
        row = conn.execute("SELECT value FROM preferences WHERE key='tmdb_key_dpapi'").fetchone()
    if not row or not row[0].strip():
        raise RuntimeError("Add your TMDB API key in Settings to search movies and TV shows.")
    from torbox import _protect
    try:
        return _protect(base64.b64decode(row[0]), decrypt=True).decode("utf-8")
    except Exception:
        raise RuntimeError("The saved TMDB key could not be decrypted for this Windows account. Please enter it again in Settings.") from None


def tmdb_search(query: str, kind: str = "movie") -> list[dict]:
    query = query.strip()
    if not query:
        return []
    kind = "tv" if kind.lower() in {"tv", "series", "show"} else "movie"
    params = urllib.parse.urlencode({"api_key": _tmdb_key(), "query": query, "include_adult": "false", "language": "en-GB"})
    result = _json(f"{TMDB}/search/{kind}?{params}")
    items = []
    for entry in result.get("results", []):
        title = entry.get("title") or entry.get("name") or "Untitled"
        items.append({
            "id": str(entry.get("id", "")), "type": kind, "title": title,
            "year": (entry.get("release_date") or entry.get("first_air_date") or "")[:4],
            "overview": entry.get("overview") or "", "poster": entry.get("poster_path"),
            "backdrop": entry.get("backdrop_path"), "rating": entry.get("vote_average"),
        })
    return items


def tmdb_home() -> dict:
    """Fetch browse rows used by the streaming-first home screen."""
    params = urllib.parse.urlencode({"api_key": _tmdb_key(), "language": "en-GB", "region": "GB"})
    def list_items(path: str) -> list[dict]:
        result = _json(f"{TMDB}/{path}?{params}")
        items = []
        for entry in result.get("results", []):
            if path == "trending/all/week" and entry.get("media_type") not in ("movie", "tv"):
                continue
            kind = "tv" if path.startswith("tv/") or entry.get("media_type") == "tv" else "movie"
            items.append({
                "id": str(entry.get("id", "")), "type": kind,
                "title": entry.get("title") or entry.get("name") or "Untitled",
                "year": (entry.get("release_date") or entry.get("first_air_date") or "")[:4],
                "overview": entry.get("overview") or "", "poster": entry.get("poster_path"),
                "backdrop": entry.get("backdrop_path"), "rating": entry.get("vote_average"),
            })
        return items
    trending = [item for item in list_items("trending/all/week") if item["type"] in ("movie", "tv")]
    new = list_items("movie/now_playing") + list_items("tv/on_the_air")
    return {"suggested": trending, "new": new[:20]}


def tmdb_tv_details(tmdb_id: str, season: int | None = None) -> dict:
    params = urllib.parse.urlencode({"api_key": _tmdb_key(), "language": "en-GB"})
    if season is None:
        data = _json(f"{TMDB}/tv/{urllib.parse.quote(str(tmdb_id), safe='')}?{params}")
        return {"seasons": [{"number": x.get("season_number"), "name": x.get("name"), "episodes": x.get("episode_count", 0)} for x in data.get("seasons", []) if isinstance(x, dict)]}
    data = _json(f"{TMDB}/tv/{urllib.parse.quote(str(tmdb_id), safe='')}/season/{int(season)}?{params}")
    return {"episodes": [{"number": x.get("episode_number"), "name": x.get("name"), "overview": x.get("overview"), "air_date": x.get("air_date")} for x in data.get("episodes", []) if isinstance(x, dict)]}


def _addon_manifest(url: str) -> dict:
    data = _json(url)
    if not isinstance(data, dict) or not data.get("id"):
        raise RuntimeError("That URL did not return a valid Stremio add-on manifest.")
    return data


def add_addon(manifest_url: str) -> dict:
    manifest_url = manifest_url.strip()
    if not manifest_url.startswith(("https://", "http://")):
        raise RuntimeError("Enter the full HTTP(S) Stremio manifest URL.")
    manifest = _addon_manifest(manifest_url)
    existing = settings()["addons"]
    if any(a["manifest"] == manifest_url for a in existing):
        raise RuntimeError("That add-on is already installed.")
    existing.append({"name": manifest.get("name", "Stremio add-on"), "manifest": manifest_url})
    save_settings(addons=existing)
    return {"name": manifest.get("name", "Stremio add-on"), "id": manifest.get("id")}


def add_debridio(api_key_or_manifest: str) -> dict:
    value = api_key_or_manifest.strip()
    if value.startswith(("https://", "http://")):
        candidates = [value]
    else:
        if not value:
            raise RuntimeError("Enter your Debridio API key or its generated Stremio manifest URL.")
        # Debridio install URLs commonly expose a base64 token path. Accept that
        # generated segment as-is, and also try encoding a raw API key.
        encoded_key = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")
        raw_segment = urllib.parse.quote(value, safe="=-_.")
        candidates = [
            f"https://addon.debridio.com/{raw_segment}/manifest.json",
            f"https://addon.debridio.com/{encoded_key}/manifest.json",
        ]
    manifest_url = candidates[0]
    manifest = None
    failures = []
    for candidate in dict.fromkeys(candidates):
        try:
            manifest = _addon_manifest(candidate)
            manifest_url = candidate
            break
        except Exception as exc:
            failures.append(str(exc))
    if manifest is None:
        raise RuntimeError(f"Could not validate the Debridio manifest. Check the API key or paste its generated install URL. {failures[-1] if failures else ''}")
    existing = settings()["addons"]
    if any(a["manifest"] == manifest_url for a in existing):
        raise RuntimeError("Debridio is already connected.")
    existing.append({"name": "Debridio", "manifest": manifest_url})
    save_settings(addons=existing)
    return {"name": "Debridio", "id": manifest.get("id")}


def remove_addon(manifest_url: str) -> None:
    save_settings(addons=[a for a in settings()["addons"] if a["manifest"] != manifest_url])


def _endpoint(manifest_url: str, manifest: dict, resource: str, media_type: str, media_id: str) -> str | None:
    resources = manifest.get("resources", [])
    resource_entries = [r for r in resources if r == resource or (isinstance(r, dict) and r.get("name") == resource and (not r.get("types") or media_type in r["types"]))] if isinstance(resources, list) else []
    if not resource_entries or (manifest.get("types") and media_type not in manifest["types"]):
        return None
    prefixes = manifest.get("idPrefixes") or []
    for entry in resource_entries:
        if isinstance(entry, dict): prefixes = entry.get("idPrefixes") or prefixes
    if prefixes and not any(str(media_id).startswith(str(prefix)) for prefix in prefixes):
        return None
    base = manifest.get("id", "")
    parsed = urllib.parse.urlsplit(manifest_url)
    path = parsed.path.rstrip("/")
    for suffix in ("/manifest.json", "/manifest"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    # Keep configured add-on query parameters (some add-ons put credentials there).
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, f"{path}/{resource}/{urllib.parse.quote(media_type, safe='')}/{urllib.parse.quote(media_id, safe=':')}.json", parsed.query, ""))


def title_sources(tmdb_id: str, media_type: str, imdb_id: str | None = None, season: int | None = None, episode: int | None = None) -> dict:
    """Collect TorBox indexed sources and streams returned by installed add-ons."""
    is_series = media_type in {"tv", "series"}
    media_type = "series" if is_series else "movie"
    source_id = f"tmdb:{tmdb_id}"
    torbox_data: list[dict] = []
    try:
        from torbox import load_token
        token = load_token()
        if not token:
            raise RuntimeError("Connect TorBox in Settings to search its indexed sources.")
        params = urllib.parse.urlencode({"media_type": media_type, "check_cache": "true"})
        raw = _json(f"{TORBOX_SEARCH}/torrents/{urllib.parse.quote(source_id, safe=':')}?{params}", headers={"Authorization": f"Bearer {token}"})
        data = raw.get("data", raw) if isinstance(raw, dict) else raw
        if isinstance(data, dict):
            data = data.get("torrents") or data.get("results") or data.get("items") or []
        if isinstance(data, list):
            torbox_data = [{"name": d.get("name") or d.get("title") or d.get("filename") or "TorBox source", "size": d.get("size"), "hash": d.get("hash") or d.get("info_hash") or d.get("infoHash"), "magnet": d.get("magnet") or d.get("magnet_link"), "source": "TorBox Search", "behaviorHints": d.get("behaviorHints", {})} for d in data if isinstance(d, dict)]
    except Exception as exc:
        torbox_error = str(exc)
    else:
        torbox_error = None

    if not imdb_id:
        try:
            params = urllib.parse.urlencode({"api_key": _tmdb_key()})
            tmdb_kind = "tv" if is_series else "movie"
            external = _json(f"{TMDB}/{tmdb_kind}/{urllib.parse.quote(str(tmdb_id), safe='')}/external_ids?{params}")
            imdb_id = external.get("imdb_id")
        except Exception:
            pass
    streams: list[dict] = []
    addon_errors = []
    for addon in settings()["addons"]:
        try:
            manifest = _addon_manifest(addon["manifest"])
            if is_series and (season is None or episode is None):
                continue
            prefixes = manifest.get("idPrefixes") or []
            resources = manifest.get("resources", [])
            for resource in resources if isinstance(resources, list) else []:
                if isinstance(resource, dict) and resource.get("name") == "stream" and (not resource.get("types") or media_type in resource["types"]):
                    prefixes = resource.get("idPrefixes") or prefixes
            candidates = [candidate for candidate in (imdb_id, source_id) if candidate]
            base_id = next((candidate for candidate in candidates if not prefixes or any(candidate.startswith(str(prefix)) for prefix in prefixes)), None)
            if not base_id:
                continue
            stream_id = f"{base_id}:{int(season)}:{int(episode)}" if is_series else base_id
            path = _endpoint(addon["manifest"], manifest, "stream", media_type, stream_id)
            if not path:
                continue
            result = _json(path, timeout=25)
            for item in (result.get("streams") or []):
                if isinstance(item, dict):
                    item["addon"] = manifest.get("name", addon["name"])
                    streams.append(item)
        except Exception as exc:
            addon_errors.append(f"{addon.get('name','Add-on')}: {exc}")
    return {"torbox": torbox_data, "streams": streams, "errors": [e for e in [torbox_error, *addon_errors] if e]}

