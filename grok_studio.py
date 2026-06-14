#!/usr/bin/env python3
"""Grok Studio: local-only web UI for Grok Imagine media workflows."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import errno
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


APP_NAME = "Grok Studio Lab"
API_BASE = "https://api.x.ai/v1"
DEFAULT_AUTH_FILE = "~/.grok/auth.json"
DEFAULT_IMAGE_MODEL = "grok-imagine-image"
DEFAULT_VIDEO_MODEL = "grok-imagine-video"
DEFAULT_ANALYZE_MODEL = "grok-4.3"
ANALYZE_MODELS = {"grok-4.3", "grok-4.20-0309-non-reasoning"}
DEFAULT_LIBRARY_FOLDER_PATH = str(Path.home() / "Documents" / "Grok Studio Lab Library")
IMAGE_IMPORT_EXTENSIONS = {".avif", ".bmp", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
VIDEO_IMPORT_EXTENSIONS = {".m4v", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}
TEXT_IMPORT_EXTENSIONS = {".text", ".txt"}
ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "studio_static"
DATA_DIR = Path(os.environ.get("GROK_STUDIO_DATA_DIR") or ROOT / "grok_studio_data_v6").expanduser().resolve()
MEDIA_DIR = DATA_DIR / "media"
META_DIR = DATA_DIR / "metadata"
TMP_DIR = DATA_DIR / "tmp"
DB_PATH = DATA_DIR / "library.json"
SETTINGS_PATH = DATA_DIR / "settings.json"
EXTERNAL_META_DIR_NAME = ".grok_studio"
ACCOUNTS_PATH = DATA_DIR / "accounts.json"
ACCOUNT_AUTH_DIR = DATA_DIR / "account_auth"
USAGE_CACHE_PATH = DATA_DIR / "usage.json"
MAX_BODY = 180 * 1024 * 1024
AUTH_REFRESH_SKEW = dt.timedelta(minutes=5)
USAGE_URL = os.environ.get("GROK_STUDIO_USAGE_URL", "https://grok.com/?_s=usage")
USAGE_CACHE_SECONDS = 45
_AUTH_LOCK = threading.RLock()
_SYSTEM_FONT_CACHE: list[str] | None = None


class StudioError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class JobCancelled(Exception):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def log_event(message: str) -> None:
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[Grok Studio {stamp}] {message}", flush=True)


_HTTPS_CONTEXT: ssl.SSLContext | None = None


def https_context() -> ssl.SSLContext:
    """Return an HTTPS context that works with python.org Python on macOS."""
    global _HTTPS_CONTEXT
    if _HTTPS_CONTEXT is not None:
        return _HTTPS_CONTEXT

    if os.environ.get("GROK_STUDIO_INSECURE_TLS") == "1":
        log_event("warning: TLS certificate verification is disabled by GROK_STUDIO_INSECURE_TLS=1")
        _HTTPS_CONTEXT = ssl._create_unverified_context()
        return _HTTPS_CONTEXT

    macos_pem = load_macos_certificates()
    if macos_pem:
        _HTTPS_CONTEXT = ssl.create_default_context(cadata=macos_pem)
        log_event("using macOS SystemRootCertificates keychain for HTTPS")
    else:
        _HTTPS_CONTEXT = ssl.create_default_context()
    return _HTTPS_CONTEXT


def load_macos_certificates() -> str | None:
    security = Path("/usr/bin/security")
    if not security.exists():
        return None
    keychains = [
        "/System/Library/Keychains/SystemRootCertificates.keychain",
        "/Library/Keychains/System.keychain",
    ]
    try:
        result = subprocess.run(
            [str(security), "find-certificate", "-a", "-p", *keychains],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0 and "BEGIN CERTIFICATE" in result.stdout:
        return result.stdout
    return None


def format_network_error(exc: urllib.error.URLError) -> str:
    reason = getattr(exc, "reason", exc)
    message = str(reason)
    if "CERTIFICATE_VERIFY_FAILED" in message:
        message += (
            "\nTLS certificate verification failed. Grok Studio tried the macOS "
            "system certificate keychain fallback. If it still fails, run the "
            "Python Install Certificates.command for your Python version, then "
            "restart Grok Studio."
        )
    return message


def parse_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return cleaned[:80] or fallback


def safe_file_stem(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", value.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:90] or fallback


def compact(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None and value != ""}


def ensure_dirs() -> None:
    STATIC_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    MEDIA_DIR.mkdir(exist_ok=True)
    META_DIR.mkdir(exist_ok=True)
    TMP_DIR.mkdir(exist_ok=True)
    ACCOUNT_AUTH_DIR.mkdir(exist_ok=True)


def read_settings() -> dict[str, Any]:
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_settings(settings: dict[str, Any]) -> None:
    ensure_dirs()
    temp = SETTINGS_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(SETTINGS_PATH)


def external_library_root() -> Path | None:
    root = read_settings().get("library_root")
    if not isinstance(root, str) or not root.strip():
        return None
    try:
        return Path(root).expanduser().resolve()
    except OSError:
        return None


def library_paths(root: Path | None = None) -> dict[str, Path]:
    if root is None:
        root = external_library_root()
    if root is None:
        return {
            "root": DATA_DIR,
            "db": DB_PATH,
            "image": MEDIA_DIR,
            "video": MEDIA_DIR,
            "upload": DATA_DIR / "Upload Image",
            "prompt": DATA_DIR / "prompts",
            "gallery": DATA_DIR / "Gallery",
            "metadata": META_DIR,
            "legacy": DATA_DIR,
        }
    meta = root / EXTERNAL_META_DIR_NAME
    return {
        "root": root,
        "db": meta / "library.json",
        "image": root / "Image",
        "video": root / "Video",
        "upload": root / "Upload Image",
        "prompt": root / "Prompt",
        "gallery": root / "Gallery",
        "metadata": meta / "metadata",
        "legacy": DATA_DIR,
    }


def ensure_library_paths(paths: dict[str, Path]) -> None:
    paths["root"].mkdir(parents=True, exist_ok=True)
    paths["image"].mkdir(parents=True, exist_ok=True)
    paths["video"].mkdir(parents=True, exist_ok=True)
    paths["upload"].mkdir(parents=True, exist_ok=True)
    paths["prompt"].mkdir(parents=True, exist_ok=True)
    paths["gallery"].mkdir(parents=True, exist_ok=True)
    paths["metadata"].mkdir(parents=True, exist_ok=True)
    paths["db"].parent.mkdir(parents=True, exist_ok=True)


def guess_ext(mime: str | None, fallback: str) -> str:
    if mime:
        ext = mimetypes.guess_extension(mime.split(";")[0].strip())
        if ext:
            return ".jpg" if ext == ".jpe" else ext
    return fallback


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise StudioError(f"Could not find a free filename near {path}", 500)


def next_image_edit_path(directory: Path, source_stem: str, ext: str) -> Path:
    base = re.sub(r"_edit\d+$", "", safe_file_stem(source_stem, "Image"), flags=re.IGNORECASE)
    for index in range(1, 1000):
        candidate = directory / f"{base}_edit{index:02d}{ext}"
        if not candidate.exists():
            return candidate
    raise StudioError(f"Could not find a free edit filename for {base}", 500)


def data_uri_to_bytes(value: str) -> tuple[bytes, str]:
    if not value.startswith("data:") or ";base64," not in value:
        raise StudioError("Expected a base64 data URI.")
    header, encoded = value.split(",", 1)
    mime = header[5:].split(";", 1)[0] or "application/octet-stream"
    try:
        return base64.b64decode(encoded), mime
    except ValueError as exc:
        raise StudioError("Invalid base64 data URI.") from exc


def system_font_families() -> list[str]:
    global _SYSTEM_FONT_CACHE
    if _SYSTEM_FONT_CACHE is not None:
        return list(_SYSTEM_FONT_CACHE)

    fonts: set[str] = {
        "Arial",
        "Apple SD Gothic Neo",
        "Helvetica",
        "Menlo",
        "Times New Roman",
    }
    if sys.platform == "darwin":
        script = (
            'ObjC.import("AppKit");'
            "JSON.stringify(ObjC.deepUnwrap($.NSFontManager.sharedFontManager.availableFontFamilies))"
        )
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-l", "JavaScript", "-e", script],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=8,
            )
            values = json.loads(result.stdout) if result.returncode == 0 else []
            if isinstance(values, list):
                fonts.update(str(value).strip() for value in values if str(value).strip())
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            pass

    _SYSTEM_FONT_CACHE = sorted(fonts, key=str.casefold)
    return list(_SYSTEM_FONT_CACHE)


def file_to_data_uri(path: Path, default_mime: str) -> str:
    if not path.is_file():
        raise StudioError(f"Local file is missing: {path}", 404)
    mime = mimetypes.guess_type(path.name)[0] or default_mime
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def find_auth_email(raw: Any) -> str | None:
    stack = [raw]
    seen = 0
    while stack and seen < 200:
        item = stack.pop()
        seen += 1
        if isinstance(item, dict):
            for key, value in item.items():
                key_text = str(key).lower()
                if "email" in key_text and isinstance(value, str) and "@" in value:
                    return value
                if isinstance(value, (dict, list)):
                    stack.append(value)
                elif isinstance(value, str) and "@" in value and re.search(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
                    return value
        elif isinstance(item, list):
            stack.extend(value for value in item if isinstance(value, (dict, list, str)))
        elif isinstance(item, str) and "@" in item and re.search(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", item):
            return item
    return None


def load_auth_summary(auth_file: str) -> dict[str, Any]:
    auth_path = Path(auth_file).expanduser()
    summary = {
        "auth_file": str(auth_path),
        "mode": "missing",
        "email": None,
        "expires_at": None,
        "active": False,
    }
    if not auth_path.exists():
        return summary
    try:
        raw = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        summary["mode"] = "unreadable"
        return summary

    fallback_email = find_auth_email(raw)
    values = raw.values() if isinstance(raw, dict) else []
    for value in values:
        if isinstance(value, dict) and isinstance(value.get("key"), str):
            expires = parse_time(value.get("expires_at"))
            summary.update(
                {
                    "mode": value.get("auth_mode") or "oauth",
                    "email": value.get("email") or fallback_email,
                    "expires_at": value.get("expires_at"),
                    "active": expires is None
                    or expires > dt.datetime.now(dt.timezone.utc),
                }
            )
            return summary
    summary["email"] = fallback_email
    return summary


def auth_candidates(raw: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        if isinstance(raw.get("key"), str):
            candidates.append(raw)
        for value in raw.values():
            if isinstance(value, dict) and isinstance(value.get("key"), str):
                candidates.append(value)
    return candidates


def token_needs_refresh(item: dict[str, Any], now: dt.datetime, force: bool = False) -> bool:
    if force:
        return True
    expires = parse_time(item.get("expires_at"))
    return expires is not None and expires <= now + AUTH_REFRESH_SKEW


def choose_auth_candidate(candidates: list[dict[str, Any]], force_refresh: bool = False) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    usable = [item for item in candidates if not token_needs_refresh(item, now, force_refresh)]
    return usable[0] if usable else candidates[0]


def refresh_oauth_token(auth_file: str, force: bool = False) -> str | None:
    auth_path = Path(auth_file).expanduser()
    if not auth_path.exists() or os.environ.get("XAI_API_KEY"):
        return None

    with _AUTH_LOCK:
        try:
            raw = json.loads(auth_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudioError(f"Could not read auth file: {exc}", 401) from exc

        candidates = auth_candidates(raw)
        if not candidates:
            return None

        item = choose_auth_candidate(candidates, force)
        if not token_needs_refresh(item, dt.datetime.now(dt.timezone.utc), force):
            return str(item["key"])

        refresh_token = item.get("refresh_token")
        issuer = item.get("oidc_issuer") or "https://auth.x.ai"
        client_id = item.get("oidc_client_id")
        if not isinstance(refresh_token, str) or not isinstance(client_id, str):
            if force:
                raise StudioError("OAuth token was rejected and cannot be refreshed. Run `grok login` again.", 401)
            return None

        token_endpoint = discover_oidc_token_endpoint(str(issuer))
        form = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            token_endpoint,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=https_context()) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise StudioError(
                f"OAuth refresh failed HTTP {exc.code}. Run `grok login` again.\n{body[:500]}",
                401,
            ) from exc
        except urllib.error.URLError as exc:
            raise StudioError(f"OAuth refresh network error: {format_network_error(exc)}", 502) from exc

        try:
            refreshed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise StudioError(f"OAuth refresh returned non-JSON response: {body[:500]}", 502) from exc

        access_token = refreshed.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise StudioError("OAuth refresh did not return an access token. Run `grok login` again.", 401)

        item["key"] = access_token
        if isinstance(refreshed.get("refresh_token"), str):
            item["refresh_token"] = refreshed["refresh_token"]
        expires_in = refreshed.get("expires_in")
        if isinstance(expires_in, (int, float)):
            expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=float(expires_in))
            item["expires_at"] = expires_at.isoformat().replace("+00:00", "Z")

        temp = auth_path.with_suffix(".json.tmp")
        try:
            temp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(temp, 0o600)
            temp.replace(auth_path)
        except OSError as exc:
            raise StudioError(f"Could not update refreshed OAuth token: {exc}", 500) from exc

        log_event("OAuth token refreshed from auth.json refresh_token")
        return access_token


def discover_oidc_token_endpoint(issuer: str) -> str:
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=https_context()) as response:
            body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise StudioError(f"OAuth discovery failed: {format_network_error(exc)}", 502) from exc
    try:
        config = json.loads(body)
    except json.JSONDecodeError as exc:
        raise StudioError(f"OAuth discovery returned non-JSON response: {body[:500]}", 502) from exc
    token_endpoint = config.get("token_endpoint")
    if not isinstance(token_endpoint, str):
        raise StudioError("OAuth discovery did not include a token endpoint.", 502)
    return token_endpoint


def load_api_key(auth_file: str) -> str:
    env_key = os.environ.get("XAI_API_KEY")
    if env_key:
        return env_key

    auth_path = Path(auth_file).expanduser()
    if not auth_path.exists():
        raise StudioError(
            f"No OAuth auth file found at {auth_path}. Run the Grok Build CLI login first.",
            401,
        )

    try:
        raw = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StudioError(f"Could not read auth file: {exc}", 401) from exc

    candidates = auth_candidates(raw)

    if not candidates:
        raise StudioError("No OAuth bearer key found in auth.json.", 401)

    refreshed = refresh_oauth_token(auth_file)
    if refreshed:
        return refreshed
    chosen = choose_auth_candidate(candidates)
    return chosen["key"]


def text_from_usage_body(body: str, content_type: str) -> str:
    if "json" in content_type.lower():
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            body = json.dumps(parsed, ensure_ascii=False)
    body = re.sub(r"<script\b[^>]*>(.*?)</script>", r" \1 ", body, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<style\b[^>]*>.*?</style>", " ", body, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    return re.sub(r"\s+", " ", body).strip()


def parse_usage_number(value: str) -> float | None:
    cleaned = re.sub(r"[^0-9.]+", "", value)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_usage_text(text: str) -> dict[str, Any]:
    used_percent: float | None = None
    fraction_label: str | None = None
    reset_label: str | None = None

    percent_patterns = [
        r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:사용|used)",
        r"(?:사용|used)[^0-9]{0,24}(\d{1,3}(?:\.\d+)?)\s*%",
        r"(?:credit|credits|usage|사용량|크레딧)[^%]{0,120}?(\d{1,3}(?:\.\d+)?)\s*%",
    ]
    for pattern in percent_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = parse_usage_number(match.group(1))
            if value is not None:
                used_percent = max(0.0, min(100.0, value))
                break

    fraction_match = re.search(r"([0-9][0-9,\s]{0,15})\s*/\s*([0-9][0-9,\s]{0,15})", text)
    if fraction_match:
        current = parse_usage_number(fraction_match.group(1))
        total = parse_usage_number(fraction_match.group(2))
        if current is not None and total and total > 0:
            fraction_label = f"{int(current):,} / {int(total):,}"
            if used_percent is None:
                start = max(0, fraction_match.start() - 80)
                end = min(len(text), fraction_match.end() + 80)
                nearby = text[start:end].lower()
                ratio = max(0.0, min(100.0, (current / total) * 100))
                if any(word in nearby for word in ["remaining", "left", "남은", "잔량"]):
                    used_percent = 100.0 - ratio
                else:
                    used_percent = ratio

    reset_patterns = [
        r"(\d{1,2}\s*월\s*\d{1,2}\s*일(?:에)?\s*재설정)",
        r"((?:resets?|reset)[^.!?<>{}]{0,60})",
    ]
    for pattern in reset_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            reset_label = match.group(1).strip(" ·,")
            break

    if used_percent is None:
        return {
            "ok": False,
            "used_percent": None,
            "remaining_percent": None,
            "fraction": fraction_label,
            "reset": reset_label,
            "message": "Usage not found",
        }

    used = int(round(used_percent))
    remaining = max(0, min(100, 100 - used))
    detail_parts = [f"{used}% used"]
    if fraction_label:
        detail_parts.append(fraction_label)
    if reset_label:
        detail_parts.append(reset_label)
    return {
        "ok": True,
        "used_percent": used,
        "remaining_percent": remaining,
        "fraction": fraction_label,
        "reset": reset_label,
        "message": " · ".join(detail_parts),
    }


def read_usage_snapshot(email: str | None = None) -> dict[str, Any] | None:
    if not USAGE_CACHE_PATH.exists():
        return None
    try:
        raw = json.loads(USAGE_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or not raw.get("ok"):
        return None
    if email and raw.get("email") and str(raw.get("email")).lower() != email.lower():
        return None
    raw["cached"] = True
    raw["manual"] = bool(raw.get("manual"))
    return raw


def write_usage_snapshot(usage: dict[str, Any]) -> None:
    if not usage.get("ok"):
        return
    ensure_dirs()
    temp = USAGE_CACHE_PATH.with_suffix(".json.tmp")
    payload = dict(usage)
    payload.pop("cached", None)
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(USAGE_CACHE_PATH)
    except OSError as exc:
        log_event(f"could not save usage snapshot: {exc}")


def usage_from_text(text: str, email: str | None = None, source: str = "manual") -> dict[str, Any]:
    parsed = parse_usage_text(text)
    if not parsed.get("ok"):
        raise StudioError("Could not find usage percent in the provided text.", 400)
    parsed.update(
        {
            "email": email,
            "checked_at": utc_now(),
            "source": source,
            "manual": source == "manual",
        }
    )
    return parsed


def fetch_account_usage(auth_file: str, timeout: float) -> dict[str, Any]:
    auth = load_auth_summary(auth_file)
    result: dict[str, Any] = {
        "ok": False,
        "email": auth.get("email"),
        "checked_at": utc_now(),
        "source": USAGE_URL,
        "used_percent": None,
        "remaining_percent": None,
        "fraction": None,
        "reset": None,
        "message": "Usage unavailable",
    }
    try:
        token = load_api_key(auth_file)
    except StudioError as exc:
        result["message"] = exc.message
        return result

    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            "Authorization": f"Bearer {token}",
            "User-Agent": "Grok Studio Lab local usage checker",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=min(max(5, timeout), 12), context=https_context()) as response:
            body = response.read(3_000_000).decode("utf-8", errors="replace")
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        body = exc.read(2000).decode("utf-8", errors="replace")
        result["message"] = f"Usage request HTTP {exc.code}"
        if body:
            parsed = parse_usage_text(text_from_usage_body(body, exc.headers.get("Content-Type", "")))
            result.update(parsed)
        return result
    except urllib.error.URLError as exc:
        result["message"] = f"Usage network error: {format_network_error(exc)}"
        return result

    parsed = parse_usage_text(text_from_usage_body(body, content_type))
    result.update(parsed)
    if result["ok"]:
        result["source"] = USAGE_URL
        result["manual"] = False
    if not result["ok"] and "login" in body[:5000].lower():
        result["message"] = "Usage page needs a Grok web login"
    return result


def account_id_for_identity(email: str | None, auth_file: str) -> str:
    key = (email or str(Path(auth_file).expanduser())).strip().lower()
    return uuid.uuid5(uuid.NAMESPACE_URL, f"grok-studio-account:{key}").hex


def read_accounts_file() -> dict[str, Any]:
    if not ACCOUNTS_PATH.exists():
        return {"active_id": "", "accounts": []}
    try:
        raw = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"active_id": "", "accounts": []}
    if not isinstance(raw, dict):
        return {"active_id": "", "accounts": []}
    accounts = raw.get("accounts")
    if not isinstance(accounts, list):
        accounts = []
    return {
        "active_id": str(raw.get("active_id") or ""),
        "accounts": [item for item in accounts if isinstance(item, dict)],
    }


def write_accounts_file(data: dict[str, Any]) -> None:
    ensure_dirs()
    temp = ACCOUNTS_PATH.with_suffix(".json.tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(ACCOUNTS_PATH)
    except OSError as exc:
        raise StudioError(f"Could not save accounts: {exc}", 500) from exc


def snapshot_auth_file(auth_file: str, label: str | None = None) -> dict[str, Any]:
    source = Path(auth_file).expanduser()
    if not source.exists():
        raise StudioError(f"Auth file not found: {source}", 404)
    summary = load_auth_summary(str(source))
    email = summary.get("email") if isinstance(summary.get("email"), str) else None
    account_id = account_id_for_identity(email, str(source))
    stem = safe_name(email or label or source.stem or account_id[:8], f"account-{account_id[:8]}")
    target = ACCOUNT_AUTH_DIR / f"{stem}-{account_id[:8]}.json"
    try:
        shutil.copyfile(source, target)
        os.chmod(target, 0o600)
    except OSError as exc:
        raise StudioError(f"Could not save account auth copy: {exc}", 500) from exc
    return account_record(str(target), label, source_auth_file=str(source), account_id=account_id)


def install_account_auth(saved_auth_file: str, cli_auth_file: str) -> str:
    source = Path(saved_auth_file).expanduser()
    target = Path(cli_auth_file).expanduser()
    if not source.exists():
        raise StudioError(f"Saved account auth file not found: {source}", 404)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            temp = target.with_suffix(".json.tmp")
            shutil.copyfile(source, temp)
            os.chmod(temp, 0o600)
            temp.replace(target)
        else:
            os.chmod(target, 0o600)
    except OSError as exc:
        raise StudioError(f"Could not update Grok CLI auth file: {exc}", 500) from exc
    return str(target)


def account_record(auth_file: str, label: str | None = None, source_auth_file: str | None = None, account_id: str | None = None) -> dict[str, Any]:
    path = str(Path(auth_file).expanduser())
    summary = load_auth_summary(path)
    email = summary.get("email")
    display = email if isinstance(email, str) and email else (label or Path(path).parent.name or "Grok account")
    return {
        "id": account_id or account_id_for_identity(email if isinstance(email, str) else None, path),
        "label": display,
        "email": email,
        "auth_file": path,
        "source_auth_file": source_auth_file or path,
        "exists": Path(path).exists(),
        "mode": summary.get("mode"),
        "active": bool(summary.get("active")),
    }


def merge_account_records(saved: list[dict[str, Any]], current_auth_file: str) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    current = account_record(current_auth_file)
    records[current["id"]] = current
    for item in saved:
        auth_file = item.get("auth_file")
        if not isinstance(auth_file, str) or not auth_file:
            continue
        source_auth_file = item.get("source_auth_file") if isinstance(item.get("source_auth_file"), str) else auth_file
        record = account_record(auth_file, str(item.get("label") or "") or None, source_auth_file=source_auth_file)
        records[record["id"]] = record
    return sorted(records.values(), key=lambda record: str(record.get("label") or record.get("email") or "").lower())


def saved_account_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "label": record.get("label") or record.get("email") or "Grok account",
        "email": record.get("email"),
        "auth_file": record["auth_file"],
        "source_auth_file": record.get("source_auth_file") or record["auth_file"],
        "created_at": utc_now(),
    }


def upsert_saved_account(saved: list[dict[str, Any]], record: dict[str, Any]) -> list[dict[str, Any]]:
    payload = saved_account_payload(record)
    kept = [
        item for item in saved
        if isinstance(item, dict)
        and item.get("id") != payload["id"]
        and item.get("auth_file") != payload["auth_file"]
    ]
    kept.append(payload)
    return kept


class XaiClient:
    def __init__(self, auth_file: str, base_url: str, timeout: float) -> None:
        self.auth_file = auth_file
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._request(method, path, payload, retried=False)

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None, retried: bool = False
    ) -> dict[str, Any]:
        data = None
        headers = {"Authorization": f"Bearer {load_api_key(self.auth_file)}"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=https_context()) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in {401, 403} and not retried and not os.environ.get("XAI_API_KEY"):
                log_event(f"OAuth token rejected with HTTP {exc.code}; refreshing and retrying once")
                refresh_oauth_token(self.auth_file, force=True)
                return self._request(method, path, payload, retried=True)
            try:
                parsed = json.loads(body)
                body = json.dumps(parsed, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                pass
            raise StudioError(f"xAI API error HTTP {exc.code}:\n{body}", exc.code) from exc
        except urllib.error.URLError as exc:
            raise StudioError(f"Network error: {format_network_error(exc)}", 502) from exc

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise StudioError(f"API returned non-JSON response: {body[:500]}", 502) from exc

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, payload)

    def get(self, path: str) -> dict[str, Any]:
        return self.request("GET", path)


class Library:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        ensure_dirs()
        self.reload_paths()
        if not self.db_path.exists():
            self._write(self.empty_data())

    def empty_data(self) -> dict[str, Any]:
        return {
            "version": 3,
            "categories": ["Inbox", "Image", "Video", "Prompt", "Finals"],
            "gallery_folders": [],
            "gallery_sort": "",
            "items": [],
        }

    def reload_paths(self) -> None:
        paths = library_paths()
        ensure_library_paths(paths)
        self.root = paths["root"]
        self.db_path = paths["db"]
        self.image_dir = paths["image"]
        self.video_dir = paths["video"]
        self.upload_dir = paths["upload"]
        self.prompt_dir = paths["prompt"]
        self.gallery_dir = paths["gallery"]
        self.metadata_dir = paths["metadata"]
        self.using_external_root = self.root.resolve() != DATA_DIR.resolve()

    def set_root(self, root_text: str) -> dict[str, Any]:
        root_text = str(root_text or "").strip()
        if not root_text:
            settings = read_settings()
            settings.pop("library_root", None)
            write_settings(settings)
        else:
            root = Path(root_text).expanduser().resolve()
            paths = library_paths(root)
            ensure_library_paths(paths)
            settings = read_settings()
            settings["library_root"] = str(root)
            write_settings(settings)
        with self.lock:
            self.reload_paths()
            if not self.db_path.exists():
                self._write(self.empty_data())
        return self.info()

    def info(self) -> dict[str, Any]:
        self.reload_paths()
        return {
            "root": str(self.root),
            "image_dir": str(self.image_dir),
            "video_dir": str(self.video_dir),
            "upload_dir": str(self.upload_dir),
            "prompt_dir": str(self.prompt_dir),
            "gallery_dir": str(self.gallery_dir),
            "external": self.using_external_root,
            "default_folder_path": DEFAULT_LIBRARY_FOLDER_PATH,
        }

    def _read(self) -> dict[str, Any]:
        with self.lock:
            self.reload_paths()
            try:
                data = json.loads(self.db_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = self.empty_data()
            return data if isinstance(data, dict) else self.empty_data()

    def _write(self, data: dict[str, Any]) -> None:
        with self.lock:
            self.reload_paths()
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.db_path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.db_path)

    def media_url(self, path: Path) -> str:
        path = path.resolve()
        roots = [MEDIA_DIR.resolve(), self.root.resolve()]
        for root in roots:
            try:
                rel = path.relative_to(root)
                return "/media/" + urllib.parse.quote(str(rel).replace(os.sep, "/"))
            except ValueError:
                continue
        return media_url(path)

    def state(self) -> dict[str, Any]:
        data = self._read()
        changed = self.dedupe_imported_items(data)
        if self.sync_disk_files(data):
            changed = True
        if changed:
            self._write(data)
        items = sorted(data.get("items", []), key=lambda item: item.get("created_at", ""), reverse=True)
        return {
            "categories": data.get("categories", []),
            "gallery_folders": data.get("gallery_folders", []),
            "gallery_sort": str(data.get("gallery_sort") or ""),
            "items": items,
            "library": self.info(),
        }

    def item_file_identity(self, item: dict[str, Any]) -> str | None:
        file_path = item.get("file")
        if isinstance(file_path, str) and file_path:
            try:
                return "file:" + str(Path(file_path).expanduser().resolve())
            except OSError:
                return "file:" + file_path
        local_url = item.get("local_url")
        if isinstance(local_url, str) and local_url:
            return "url:" + urllib.parse.unquote(local_url)
        return None

    def is_imported_item(self, item: dict[str, Any]) -> bool:
        metadata = item.get("metadata")
        return item.get("mode") == "import" or (
            isinstance(metadata, dict) and metadata.get("imported") is True
        )

    def dedupe_imported_items(self, data: dict[str, Any]) -> int:
        items = data.setdefault("items", [])
        kept: list[dict[str, Any]] = []
        identity_indexes: dict[str, int] = {}
        removed = 0
        for item in items:
            if not isinstance(item, dict):
                removed += 1
                continue
            identity = self.item_file_identity(item)
            existing_index = identity_indexes.get(identity) if identity else None
            if existing_index is None:
                if identity:
                    identity_indexes[identity] = len(kept)
                kept.append(item)
                continue
            existing = kept[existing_index]
            existing_imported = self.is_imported_item(existing)
            current_imported = self.is_imported_item(item)
            if not existing_imported and not current_imported:
                kept.append(item)
                continue
            if existing_imported and not current_imported:
                kept[existing_index] = item
            removed += 1
        if removed:
            data["items"] = kept
            log_event(f"removed {removed} duplicate auto-imported library item(s)")
        return removed

    def sync_disk_files(self, data: dict[str, Any]) -> int:
        categories = data.setdefault("categories", [])
        items = data.setdefault("items", [])
        existing_files = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            identity = self.item_file_identity(item)
            if identity:
                existing_files.add(identity)

        added = 0
        for path in self.import_candidates():
            try:
                resolved = path.resolve()
                stat = resolved.stat()
            except OSError:
                continue
            resolved_text = str(resolved)
            identity = "file:" + resolved_text
            if identity in existing_files:
                continue
            item = self.disk_file_item(resolved, stat.st_mtime)
            if not item:
                continue
            category = item.get("category") or "Inbox"
            if category not in categories:
                categories.append(category)
            items.append(item)
            existing_files.add(identity)
            added += 1
        if added:
            log_event(f"auto-imported {added} library file(s)")
        return added

    def import_candidates(self) -> list[Path]:
        roots: list[Path] = []
        for path in (self.image_dir, self.video_dir, self.prompt_dir):
            if path not in roots:
                roots.append(path)
        if self.root not in roots:
            roots.append(self.root)
        if not self.using_external_root and MEDIA_DIR not in roots:
            roots.append(MEDIA_DIR)
        candidates: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            if not root.exists() or not root.is_dir():
                continue
            if root.name == EXTERNAL_META_DIR_NAME:
                continue
            try:
                entries = sorted(root.iterdir(), key=lambda item: item.name.lower())
            except OSError:
                continue
            for path in entries:
                if not path.is_file() or path.name.startswith("."):
                    continue
                try:
                    key = str(path.resolve())
                except OSError:
                    key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(path)
        return candidates

    def disk_file_item(self, path: Path, mtime: float) -> dict[str, Any] | None:
        ext = path.suffix.lower()
        mime = mimetypes.guess_type(path.name)[0] or ""
        created_at = dt.datetime.fromtimestamp(mtime, dt.timezone.utc).isoformat().replace("+00:00", "Z")
        title = path.stem.replace("-", " ").replace("_", " ").strip() or path.name
        base = {
            "id": uuid.uuid4().hex,
            "title": title,
            "prompt": title,
            "tags": [],
            "created_at": created_at,
            "file": str(path),
            "mime": mime or "application/octet-stream",
            "metadata": {
                "library_root": str(self.root),
                "imported": True,
            },
        }
        if ext in IMAGE_IMPORT_EXTENSIONS or mime.startswith("image/"):
            return {
                **base,
                "type": "image",
                "mode": "import",
                "category": "Image",
                "local_url": self.media_url(path),
            }
        if ext in VIDEO_IMPORT_EXTENSIONS or mime.startswith("video/"):
            return {
                **base,
                "type": "video",
                "mode": "import",
                "category": "Video",
                "local_url": self.media_url(path),
            }
        if ext in TEXT_IMPORT_EXTENSIONS or mime == "text/plain":
            try:
                prompt = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                prompt = ""
            if not prompt:
                prompt = title
            return {
                **base,
                "type": "prompt",
                "mode": "import",
                "category": "Prompt",
                "prompt": prompt[:200000],
                "local_url": None,
                "mime": "text/plain",
            }
        return None

    def add_category(self, name: str) -> list[str]:
        name = name.strip()
        if not name:
            raise StudioError("Category cannot be empty.")
        data = self._read()
        categories = data.setdefault("categories", [])
        if name not in categories:
            categories.append(name)
            categories.sort(key=str.lower)
            self._write(data)
        return categories

    def add_gallery_folder(self, name: str, parent_id: str | None = None) -> dict[str, Any]:
        name = re.sub(r"\s+", " ", str(name or "").strip())
        if not name:
            raise StudioError("Folder name cannot be empty.")
        if len(name) > 80:
            raise StudioError("Folder name is too long.")
        parent_id = str(parent_id or "").strip() or None
        data = self._read()
        folders = data.setdefault("gallery_folders", [])
        if parent_id:
            parent = next((folder for folder in folders if folder.get("id") == parent_id), None)
            if not parent:
                raise StudioError("Parent folder not found.", 404)
            if parent.get("parent_id"):
                raise StudioError("Gallery supports two folder levels.")
        siblings = [folder for folder in folders if (folder.get("parent_id") or None) == parent_id]
        if any(str(folder.get("name") or "").casefold() == name.casefold() for folder in siblings):
            raise StudioError("A folder with this name already exists.")
        directory_name = safe_file_stem(name, "Folder")
        if parent_id:
            parent_path = self.gallery_folder_path(parent_id, data)
            directory = unique_path(parent_path / directory_name)
        else:
            directory = unique_path(self.gallery_dir / directory_name)
        directory.mkdir(parents=True, exist_ok=False)
        folder = {
            "id": uuid.uuid4().hex,
            "name": name,
            "parent_id": parent_id,
            "directory_name": directory.name,
            "created_at": utc_now(),
            "order": len(siblings),
            "grid_slot": len(siblings) if parent_id else None,
        }
        if parent_id:
            for kind in ("Image", "Video", "Prompt", "Upload Image"):
                (directory / kind).mkdir(parents=True, exist_ok=True)
        folders.append(folder)
        self._write(data)
        return folder

    def update_gallery_folder_layout(self, folders: Any, sort_mode: Any = None) -> dict[str, Any]:
        if not isinstance(folders, list):
            raise StudioError("Folder layout is invalid.")
        data = self._read()
        by_id = {
            str(folder.get("id") or ""): folder
            for folder in data.get("gallery_folders", [])
            if isinstance(folder, dict) and folder.get("id")
        }
        updated: list[str] = []
        for entry in folders:
            if not isinstance(entry, dict):
                continue
            folder_id = str(entry.get("id") or "")
            folder = by_id.get(folder_id)
            if not folder:
                continue
            if "order" in entry:
                folder["order"] = max(0, int(entry.get("order") or 0))
            if "grid_slot" in entry and folder.get("parent_id"):
                folder["grid_slot"] = max(0, int(entry.get("grid_slot") or 0))
            updated.append(folder_id)
        if sort_mode is not None:
            normalized_sort = str(sort_mode or "")
            data["gallery_sort"] = normalized_sort if normalized_sort in {"abc", "ko"} else ""
        self._write(data)
        return {
            "updated": updated,
            "gallery_folders": data.get("gallery_folders", []),
            "gallery_sort": str(data.get("gallery_sort") or ""),
        }

    def gallery_folder(self, folder_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        data = data or self._read()
        folder = next(
            (candidate for candidate in data.get("gallery_folders", []) if candidate.get("id") == folder_id),
            None,
        )
        if not folder:
            raise StudioError("Gallery folder not found.", 404)
        return folder

    def gallery_folder_path(self, folder_id: str, data: dict[str, Any] | None = None) -> Path:
        data = data or self._read()
        folder = self.gallery_folder(folder_id, data)
        directory_name = safe_file_stem(str(folder.get("directory_name") or folder.get("name") or ""), "Folder")
        parent_id = str(folder.get("parent_id") or "").strip()
        if not parent_id:
            return self.gallery_dir / directory_name
        return self.gallery_folder_path(parent_id, data) / directory_name

    def gallery_output_dir(self, folder_id: Any, kind: str) -> Path:
        folder_id = str(folder_id or "").strip()
        if not folder_id:
            return {
                "Image": self.image_dir,
                "Video": self.video_dir,
                "Prompt": self.prompt_dir,
                "Upload Image": self.upload_dir,
            }[kind]
        data = self._read()
        folder = self.gallery_folder(folder_id, data)
        if not folder.get("parent_id"):
            raise StudioError("Select a second-level Gallery folder.")
        output = self.gallery_folder_path(folder_id, data) / kind
        output.mkdir(parents=True, exist_ok=True)
        return output

    def ensure_gallery_upload_dirs(self, data: dict[str, Any] | None = None) -> None:
        data = data or self._read()
        for folder in data.get("gallery_folders", []):
            if not isinstance(folder, dict) or not folder.get("parent_id"):
                continue
            (self.gallery_folder_path(str(folder.get("id")), data) / "Upload Image").mkdir(
                parents=True,
                exist_ok=True,
            )

    def upload_image_locations(self, data: dict[str, Any] | None = None) -> list[tuple[Path, str]]:
        data = data or self._read()
        self.ensure_gallery_upload_dirs(data)
        locations = [(self.upload_dir, "")]
        for folder in data.get("gallery_folders", []):
            if not isinstance(folder, dict) or not folder.get("parent_id"):
                continue
            folder_id = str(folder.get("id") or "")
            if folder_id:
                locations.append((self.gallery_folder_path(folder_id, data) / "Upload Image", folder_id))
        return locations

    def local_media_path(self, value: Any) -> Path | None:
        if isinstance(value, dict):
            value = value.get("url")
        if not isinstance(value, str) or not value.startswith("/media/"):
            return None
        try:
            return resolve_media_path(value)
        except StudioError:
            return None

    def move_related_upload_images(
        self,
        data: dict[str, Any],
        items: list[dict[str, Any]],
        related_ids: set[str],
        folder_id: str,
    ) -> dict[str, str]:
        upload_roots = [path.resolve() for path, _ in self.upload_image_locations(data)]
        target_dir = self.gallery_output_dir(folder_id, "Upload Image")
        candidates: set[Path] = set()

        def add(value: Any) -> None:
            path = self.local_media_path(value)
            if path is None:
                return
            resolved = path.resolve()
            if any(resolved.parent == root for root in upload_roots):
                candidates.add(resolved)

        for item in items:
            if str(item.get("id") or "") not in related_ids:
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            add(item.get("local_url"))
            add(metadata.get("start_image"))
            for field in ("source_images", "reference_images"):
                values = metadata.get(field)
                if isinstance(values, list):
                    for value in values:
                        add(value)

        replacements: dict[str, str] = {}
        for source in candidates:
            try:
                if source.parent.resolve() == target_dir.resolve():
                    continue
            except OSError:
                continue
            old_url = self.media_url(source)
            target = unique_path(target_dir / source.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            replacements[old_url] = self.media_url(target)
        return replacements

    def move_selected_upload_images(
        self,
        data: dict[str, Any],
        selected_ids: set[str],
        folder_id: str,
    ) -> dict[str, str]:
        wanted = {
            item_id.removeprefix("upload-card:")
            for item_id in selected_ids
            if item_id.startswith("upload-card:") or item_id.startswith("upload:")
        }
        if not wanted:
            return {}
        target_dir = self.gallery_output_dir(folder_id, "Upload Image")
        replacements: dict[str, str] = {}
        for upload_dir, source_folder_id in self.upload_image_locations(data):
            for source in upload_dir.iterdir():
                if not source.is_file():
                    continue
                upload_id = f"upload:{source_folder_id}:{source.name}" if source_folder_id else f"upload:{source.name}"
                if upload_id not in wanted:
                    continue
                try:
                    if source.parent.resolve() == target_dir.resolve():
                        continue
                except OSError:
                    continue
                old_url = self.media_url(source)
                target = unique_path(target_dir / source.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                replacements[old_url] = self.media_url(target)
        return replacements

    def replace_item_media_references(self, items: list[dict[str, Any]], replacements: dict[str, str]) -> None:
        if not replacements:
            return

        def replace(value: Any) -> Any:
            if isinstance(value, str):
                return replacements.get(value, value)
            if isinstance(value, dict) and isinstance(value.get("url"), str):
                return {**value, "url": replacements.get(value["url"], value["url"])}
            return value

        for item in items:
            if isinstance(item.get("local_url"), str):
                item["local_url"] = replacements.get(item["local_url"], item["local_url"])
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if "start_image" in metadata:
                metadata["start_image"] = replace(metadata.get("start_image"))
            for field in ("source_images", "reference_images"):
                values = metadata.get(field)
                if isinstance(values, list):
                    metadata[field] = [replace(value) for value in values]

    def media_reference_keys(self, item: dict[str, Any]) -> set[str]:
        keys: set[str] = set()

        def add(value: Any) -> None:
            if isinstance(value, dict):
                value = value.get("url")
            if not isinstance(value, str) or not value:
                return
            bare = value.split("?", 1)[0]
            keys.add(bare)
            keys.add(urllib.parse.unquote(bare))

        add(item.get("local_url"))
        add(item.get("remote_url"))
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        add(metadata.get("start_image"))
        for field in ("source_images", "reference_images"):
            values = metadata.get(field)
            if isinstance(values, list):
                for value in values:
                    add(value)
        return {key for key in keys if key}

    def related_media_components(
        self,
        items: list[dict[str, Any]],
        seed_ids: set[str],
    ) -> list[set[str]]:
        components: list[set[str]] = []
        assigned: set[str] = set()
        for seed_id in seed_ids:
            if seed_id in assigned:
                continue
            ids = {seed_id}
            group_ids: set[str] = set()
            url_keys: set[str] = set()
            changed = True
            while changed:
                changed = False
                for item in items:
                    item_id = str(item.get("id") or "")
                    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                    group_id = str(metadata.get("group_id") or "")
                    parent_id = str(metadata.get("parent_id") or "")
                    item_url_keys = self.media_reference_keys(item)
                    related = (
                        item_id in ids
                        or (group_id and group_id in group_ids)
                        or (parent_id and (parent_id in ids or parent_id in group_ids))
                        or bool(item_url_keys & url_keys)
                    )
                    if not related:
                        continue
                    before = (len(ids), len(group_ids), len(url_keys))
                    if item_id:
                        ids.add(item_id)
                    if group_id:
                        group_ids.add(group_id)
                    if parent_id:
                        ids.add(parent_id)
                    url_keys.update(item_url_keys)
                    if before != (len(ids), len(group_ids), len(url_keys)):
                        changed = True
            component = {
                str(item.get("id") or "")
                for item in items
                if str(item.get("id") or "") in ids
            }
            component.discard("")
            if component:
                components.append(component)
                assigned.update(component)
        return components

    def move_items_to_gallery(self, ids: list[str], folder_id: str) -> dict[str, Any]:
        wanted = set(ids)
        if not wanted:
            raise StudioError("No items selected.")
        data = self._read()
        folder = self.gallery_folder(folder_id, data)
        if not folder.get("parent_id"):
            raise StudioError("Select a second-level Gallery folder.")
        items = [item for item in data.get("items", []) if isinstance(item, dict)]
        components = self.related_media_components(items, wanted)
        related_ids = set().union(*components) if components else set(wanted)
        replacements = self.move_selected_upload_images(data, wanted, folder_id)
        replacements.update(self.move_related_upload_images(data, items, related_ids, folder_id))
        self.replace_item_media_references(items, replacements)
        component_group_ids: dict[str, str] = {}
        for component in components:
            existing_group_ids = [
                str(item.get("metadata", {}).get("group_id") or "")
                for item in items
                if item.get("id") in component and isinstance(item.get("metadata"), dict)
            ]
            canonical_group_id = next((group_id for group_id in existing_group_ids if group_id), "")
            if not canonical_group_id:
                canonical_group_id = next((item_id for item_id in ids if item_id in component), "")
            if not canonical_group_id:
                canonical_group_id = next(iter(component))
            for item_id in component:
                component_group_ids[item_id] = canonical_group_id
        moved: list[str] = []
        for item in items:
            if item.get("id") not in related_ids:
                continue
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                item["metadata"] = metadata
            kind = {"image": "Image", "video": "Video", "prompt": "Prompt"}.get(str(item.get("type") or ""))
            if not kind:
                continue
            canonical_group_id = component_group_ids.get(str(item.get("id") or ""), "")
            item_changed = (
                str(metadata.get("gallery_folder_id") or "") != folder_id
                or bool(canonical_group_id and metadata.get("group_id") != canonical_group_id)
            )
            if canonical_group_id:
                metadata["group_id"] = canonical_group_id
            file_path = item.get("file")
            if isinstance(file_path, str) and file_path:
                source = Path(file_path).expanduser()
                if source.exists() and source.is_file():
                    target_dir = self.gallery_output_dir(folder_id, kind)
                    try:
                        already_in_target = source.resolve().parent == target_dir.resolve()
                    except OSError:
                        already_in_target = False
                    if not already_in_target:
                        target = unique_path(target_dir / source.name)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(source), str(target))
                        item["file"] = str(target)
                        if item.get("local_url"):
                            item["local_url"] = self.media_url(target)
                        item_changed = True
            metadata["gallery_folder_id"] = folder_id
            item["updated_at"] = utc_now()
            if item_changed:
                moved.append(str(item.get("id")))
        if not moved and not replacements:
            raise StudioError("The selected items are already in this Gallery folder.")
        self._write(data)
        return {
            "moved": moved,
            "count": len(moved) + len(replacements),
            "folder_id": folder_id,
        }

    def delete_gallery_folder(self, folder_id: str) -> dict[str, Any]:
        data = self._read()
        folder = self.gallery_folder(folder_id, data)
        child_ids = {
            str(candidate.get("id"))
            for candidate in data.get("gallery_folders", [])
            if candidate.get("parent_id") == folder_id and candidate.get("id")
        }
        folder_ids = {folder_id, *child_ids}
        deleted_items: list[str] = []
        kept_items: list[dict[str, Any]] = []
        for item in data.get("items", []):
            metadata = item.get("metadata")
            item_folder_id = str(metadata.get("gallery_folder_id") or "") if isinstance(metadata, dict) else ""
            if item_folder_id not in folder_ids:
                kept_items.append(item)
                continue
            delete_item_files(item)
            deleted_items.append(str(item.get("id") or ""))
        folder_path = self.gallery_folder_path(folder_id, data)
        try:
            if folder_path.exists() and folder_path.is_dir():
                shutil.rmtree(folder_path)
        except OSError as exc:
            raise StudioError(f"Could not delete Gallery folder: {exc}", 500) from exc
        data["items"] = kept_items
        data["gallery_folders"] = [
            candidate
            for candidate in data.get("gallery_folders", [])
            if candidate.get("id") not in folder_ids
        ]
        self._write(data)
        return {
            "deleted_folder_ids": sorted(folder_ids),
            "deleted_item_ids": [item_id for item_id in deleted_items if item_id],
            "count": len(deleted_items),
        }

    def rename_gallery_folder(self, folder_id: str, name: str) -> dict[str, Any]:
        name = re.sub(r"\s+", " ", str(name or "").strip())
        if not name:
            raise StudioError("Folder name cannot be empty.")
        if len(name) > 80:
            raise StudioError("Folder name is too long.")
        data = self._read()
        folder = self.gallery_folder(folder_id, data)
        parent_id = str(folder.get("parent_id") or "").strip() or None
        siblings = [
            candidate
            for candidate in data.get("gallery_folders", [])
            if (candidate.get("parent_id") or None) == parent_id and candidate.get("id") != folder_id
        ]
        if any(str(candidate.get("name") or "").casefold() == name.casefold() for candidate in siblings):
            raise StudioError("A folder with this name already exists.")

        old_path = self.gallery_folder_path(folder_id, data)
        target = old_path.parent / safe_file_stem(name, "Folder")
        if target != old_path:
            if target.exists():
                try:
                    same_folder = old_path.exists() and target.samefile(old_path)
                except OSError:
                    same_folder = False
                if same_folder:
                    temporary = unique_path(old_path.parent / f".rename-{uuid.uuid4().hex[:8]}")
                    old_path.rename(temporary)
                    temporary.rename(target)
                else:
                    target = unique_path(target)
                    old_path.rename(target)
            elif old_path.exists():
                old_path.rename(target)
            else:
                target.mkdir(parents=True, exist_ok=True)

        affected_folder_ids = {folder_id}
        if not parent_id:
            affected_folder_ids.update(
                str(candidate.get("id"))
                for candidate in data.get("gallery_folders", [])
                if candidate.get("parent_id") == folder_id and candidate.get("id")
            )
        old_root = old_path.resolve(strict=False)
        new_root = target.resolve(strict=False)
        for item in data.get("items", []):
            metadata = item.get("metadata")
            item_folder_id = str(metadata.get("gallery_folder_id") or "") if isinstance(metadata, dict) else ""
            if item_folder_id not in affected_folder_ids:
                continue
            file_changed = False
            for key in ("file", "metadata_file"):
                value = item.get(key)
                if not isinstance(value, str) or not value:
                    continue
                try:
                    relative = Path(value).expanduser().resolve(strict=False).relative_to(old_root)
                except (OSError, ValueError):
                    continue
                item[key] = str(new_root / relative)
                if key == "file":
                    file_changed = True
            if file_changed and item.get("local_url"):
                item["local_url"] = self.media_url(Path(str(item["file"])))
            item["updated_at"] = utc_now()

        folder["name"] = name
        folder["directory_name"] = target.name
        folder["updated_at"] = utc_now()
        self._write(data)
        return folder

    def add_item(self, item: dict[str, Any]) -> dict[str, Any]:
        data = self._read()
        identity = self.item_file_identity(item)
        if identity:
            data["items"] = [
                existing
                for existing in data.get("items", [])
                if not (
                    isinstance(existing, dict)
                    and self.is_imported_item(existing)
                    and self.item_file_identity(existing) == identity
                )
            ]
        self.dedupe_imported_items(data)
        category = item.get("category") or "Inbox"
        if category not in data.setdefault("categories", []):
            data["categories"].append(category)
        data["items"].append(item)
        self._write(data)
        return item

    def get_item(self, item_id: str) -> dict[str, Any]:
        data = self._read()
        for item in data.get("items", []):
            if item.get("id") == item_id:
                return item
        raise StudioError("Item not found.", 404)

    def update_item(self, item_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        data = self._read()
        for item in data.get("items", []):
            if item.get("id") == item_id:
                for key in ("category", "tags", "title"):
                    if key in patch:
                        item[key] = patch[key]
                if item.get("type") == "prompt" and "prompt" in patch:
                    prompt = str(patch.get("prompt") or "").strip()
                    if not prompt:
                        raise StudioError("Prompt content is empty.")
                    item["prompt"] = prompt
                    file_path = item.get("file")
                    if file_path:
                        Path(file_path).write_text(prompt, encoding="utf-8")
                if item.get("type") == "prompt" and "translation" in patch:
                    item["translation"] = str(patch.get("translation") or "").strip()
                item["updated_at"] = utc_now()
                if item.get("category") not in data.setdefault("categories", []):
                    data["categories"].append(item["category"])
                self._write(data)
                return item
        raise StudioError("Item not found.", 404)

    def delete_items(self, ids: list[str]) -> dict[str, Any]:
        wanted = set(ids)
        if not wanted:
            raise StudioError("No items selected.")
        data = self._read()
        kept = []
        deleted = []
        for item in data.get("items", []):
            if item.get("id") not in wanted:
                kept.append(item)
                continue
            delete_item_files(item)
            deleted.append(item.get("id"))
        data["items"] = kept
        self._write(data)
        return {"deleted": deleted, "count": len(deleted)}


class JobRegistry:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}

    def create(self, kind: str, prompt: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        job = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "prompt": prompt,
            "status": "queued",
            "progress": 0,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "request_id": None,
            "item": None,
            "error": None,
            "context": context if isinstance(context, dict) else {},
        }
        with self.lock:
            self.jobs[job["id"]] = job
        return job

    def update(self, job_id: str, **patch: Any) -> dict[str, Any]:
        with self.lock:
            job = self.jobs[job_id]
            job.update(patch)
            job["updated_at"] = utc_now()
            return dict(job)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            if job_id not in self.jobs:
                raise StudioError("Job not found.", 404)
            job = self.jobs[job_id]
            if job.get("status") in {"done", "failed", "cancelled"}:
                return dict(job)
            job["cancel_requested"] = True
            job["status"] = "cancelled"
            job["error"] = "Cancelled locally. The remote xAI request may still finish server-side."
            job["updated_at"] = utc_now()
            return dict(job)

    def dismiss(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            if job_id not in self.jobs:
                raise StudioError("Job not found.", 404)
            job = self.jobs[job_id]
            if job.get("status") not in {"done", "failed", "cancelled"}:
                job["cancel_requested"] = True
                job["status"] = "cancelled"
                job["error"] = "Cancelled locally. The remote xAI request may still finish server-side."
                job["updated_at"] = utc_now()
                return dict(job)
            return dict(self.jobs.pop(job_id))

    def is_cancelled(self, job_id: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            return bool(job and (job.get("cancel_requested") or job.get("status") == "cancelled"))

    def get(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            if job_id not in self.jobs:
                raise StudioError("Job not found.", 404)
            return dict(self.jobs[job_id])

    def all(self) -> list[dict[str, Any]]:
        with self.lock:
            return sorted(self.jobs.values(), key=lambda item: item["created_at"], reverse=True)[:24]

    def has_active(self) -> bool:
        with self.lock:
            return any(
                job.get("status") not in {"done", "failed", "cancelled"}
                for job in self.jobs.values()
            )


class StudioApp:
    def __init__(self, auth_file: str, base_url: str, timeout: float) -> None:
        self.cli_auth_file = str(Path(auth_file).expanduser())
        self.auth_file = self.cli_auth_file
        self.client = XaiClient(self.auth_file, base_url, timeout)
        self.timeout = timeout
        self.library = Library()
        self.jobs = JobRegistry()
        self.last_heartbeat = time.monotonic()
        self.shutdown_lock = threading.RLock()
        self.shutdown_token: str | None = None
        self.usage_lock = threading.RLock()
        self.usage_cache: dict[str, Any] | None = None
        self.usage_checked_at = 0.0
        self.sync_current_cli_account()

    def sync_current_cli_account(self) -> None:
        summary = load_auth_summary(self.cli_auth_file)
        email = summary.get("email")
        if not isinstance(email, str) or not email:
            return
        try:
            record = snapshot_auth_file(self.cli_auth_file)
            data = read_accounts_file()
            saved = [item for item in data.get("accounts", []) if isinstance(item, dict)]
            saved = upsert_saved_account(saved, record)
            write_accounts_file({"active_id": record["id"], "accounts": saved})
            self.auth_file = self.cli_auth_file
            self.client.auth_file = self.cli_auth_file
        except StudioError as exc:
            log_event(f"account sync skipped: {exc.message}")

    def state(self) -> dict[str, Any]:
        self.sync_current_cli_account()
        library_state = self.library.state()
        return {
            "app": APP_NAME,
            "auth": load_auth_summary(self.auth_file),
            "cli_auth_file": self.cli_auth_file,
            "data_dir": str(DATA_DIR),
            "library": library_state.get("library") or self.library.info(),
            "categories": library_state["categories"],
            "gallery_folders": library_state["gallery_folders"],
            "gallery_sort": library_state.get("gallery_sort") or "",
            "items": library_state["items"],
            "uploads": self.list_uploaded_images(),
            "jobs": self.jobs.all(),
        }

    def list_uploaded_images(self) -> list[dict[str, Any]]:
        self.library.reload_paths()
        data = self.library._read()
        uploads: list[dict[str, Any]] = []
        for upload_dir, folder_id in self.library.upload_image_locations(data):
            for path in upload_dir.iterdir():
                if not path.is_file():
                    continue
                mime = mimetypes.guess_type(path.name)[0] or ""
                if not mime.startswith("image/"):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                upload_id = f"upload:{folder_id}:{path.name}" if folder_id else f"upload:{path.name}"
                uploads.append(
                    {
                        "id": upload_id,
                        "type": "upload-image",
                        "title": path.stem,
                        "name": path.name,
                        "created_at": dt.datetime.fromtimestamp(
                            stat.st_mtime,
                            dt.timezone.utc,
                        ).isoformat().replace("+00:00", "Z"),
                        "local_url": self.library.media_url(path),
                        "file": str(path),
                        "gallery_folder_id": folder_id,
                        "mime": mime,
                        "size": stat.st_size,
                    }
                )
        uploads.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return uploads[:120]

    def save_uploaded_images(self, payload: dict[str, Any]) -> dict[str, Any]:
        images = payload.get("images")
        names = payload.get("names") if isinstance(payload.get("names"), list) else []
        if not isinstance(images, list) or not images:
            raise StudioError("No upload images were provided.")
        if len(images) > 24:
            raise StudioError("Upload up to 24 images at a time.")
        saved: list[dict[str, Any]] = []
        self.library.reload_paths()
        gallery_folder_id = str(payload.get("gallery_folder_id") or "").strip()
        upload_dir = self.library.gallery_output_dir(gallery_folder_id, "Upload Image")
        for index, value in enumerate(images):
            if not isinstance(value, str):
                continue
            media_bytes, mime = data_uri_to_bytes(value)
            if not mime.startswith("image/"):
                raise StudioError("Only image uploads are supported.")
            name = str(names[index] if index < len(names) else "").strip()
            source_stem = safe_file_stem(Path(name).stem if name else "Source Image", "Source Image")
            ext = guess_ext(mime, Path(name).suffix if name else ".png")
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            path = unique_path(upload_dir / f"{stamp}-{source_stem}{ext}")
            path.write_bytes(media_bytes)
            upload_id = f"upload:{gallery_folder_id}:{path.name}" if gallery_folder_id else f"upload:{path.name}"
            saved.append(
                {
                    "id": upload_id,
                    "type": "upload-image",
                    "title": path.stem,
                    "name": path.name,
                    "created_at": utc_now(),
                    "local_url": self.library.media_url(path),
                    "file": str(path),
                    "gallery_folder_id": gallery_folder_id,
                    "mime": mime,
                    "size": len(media_bytes),
                }
            )
        return {"saved": saved, "uploads": self.list_uploaded_images()}

    def save_image_edit(self, payload: dict[str, Any]) -> dict[str, Any]:
        item_id = require_text(payload, "item_id")
        image_data = require_text(payload, "image")
        if item_id.startswith("upload-card:"):
            upload_id = item_id.removeprefix("upload-card:")
            upload = next(
                (candidate for candidate in self.list_uploaded_images() if candidate.get("id") == upload_id),
                None,
            )
            if not upload:
                raise StudioError("Uploaded image not found.", 404)
            source = {
                **upload,
                "id": item_id,
                "type": "image",
                "mode": "upload",
                "prompt": "",
                "tags": [],
                "metadata": {"gallery_folder_id": upload.get("gallery_folder_id") or None},
            }
        else:
            source = self.library.get_item(item_id)
        if source.get("type") not in {"image", "video"}:
            raise StudioError("Only Library images and video source images can be edited.")
        source_url = str(payload.get("source_url") or "").strip()
        if not source_url and source.get("type") == "image":
            source_url = str(source.get("local_url") or "").strip()
        if not source_url:
            raise StudioError("This video does not have a source image to edit.")

        media_bytes, mime = data_uri_to_bytes(image_data)
        if mime not in {"image/png", "image/jpeg"}:
            raise StudioError("Edited images must be PNG or JPEG.")

        self.library.reload_paths()
        source_url_path = urllib.parse.unquote(urllib.parse.urlparse(source_url).path)
        source_stem = Path(source_url_path).stem or Path(str(source.get("file") or source.get("title") or "Image")).stem
        ext = ".jpg" if mime == "image/jpeg" else ".png"
        path = next_image_edit_path(self.library.image_dir, source_stem, ext)
        path.write_bytes(media_bytes)

        source_metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        item_id_new = uuid.uuid4().hex
        item = {
            "id": item_id_new,
            "type": "image",
            "mode": "basic-image-edit",
            "title": path.stem,
            "prompt": str(source.get("prompt") or ""),
            "category": "Image",
            "tags": normalize_tags(source.get("tags")),
            "created_at": utc_now(),
            "local_url": self.library.media_url(path),
            "file": str(path),
            "mime": mime,
            "metadata": {
                "group_id": str(source_metadata.get("group_id") or source.get("id") or item_id_new),
                "parent_id": source.get("id"),
                "gallery_folder_id": None,
                "editor": "TOAST UI Image Editor",
                "source_images": [{"url": source_url}],
            },
        }
        return {"item": self.library.add_item(item)}

    def delete_uploaded_image(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_id = str(payload.get("id") or "").strip()
        raw_file = str(payload.get("file") or "").strip()
        if raw_id.startswith("upload:"):
            name = raw_id.removeprefix("upload:")
        elif raw_file:
            name = Path(raw_file).name
        else:
            name = raw_id
        if not name:
            raise StudioError("Upload image id is required.")

        self.library.reload_paths()
        target = Path(raw_file).expanduser().resolve() if raw_file else (self.library.upload_dir / name).resolve()
        roots = [path.resolve() for path, _ in self.library.upload_image_locations()]
        if not any(target.parent == root for root in roots):
            raise StudioError("Invalid upload image path.", 400)
        if target.exists():
            if not target.is_file():
                raise StudioError("Upload image is not a file.", 400)
            target.unlink()
        return {"ok": True, "uploads": self.list_uploaded_images()}

    def account_usage(self, force: bool = False) -> dict[str, Any]:
        auth = load_auth_summary(self.auth_file)
        email = auth.get("email") if isinstance(auth.get("email"), str) else None
        with self.usage_lock:
            if (
                not force
                and self.usage_cache is not None
                and time.monotonic() - self.usage_checked_at < USAGE_CACHE_SECONDS
            ):
                cached = dict(self.usage_cache)
                cached["cached"] = True
                return cached
        usage = fetch_account_usage(self.auth_file, self.timeout)
        if usage.get("ok"):
            write_usage_snapshot(usage)
        else:
            snapshot = read_usage_snapshot(email)
            if snapshot is not None:
                snapshot["message"] = snapshot.get("message") or "Last saved usage"
                usage = snapshot
        with self.usage_lock:
            self.usage_cache = dict(usage)
            self.usage_checked_at = time.monotonic()
        usage["cached"] = False
        return usage

    def import_account_usage(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = require_text(payload, "text")
        auth = load_auth_summary(self.auth_file)
        email = auth.get("email") if isinstance(auth.get("email"), str) else None
        usage = usage_from_text(text, email=email, source="manual")
        write_usage_snapshot(usage)
        with self.usage_lock:
            self.usage_cache = dict(usage)
            self.usage_checked_at = time.monotonic()
        usage["cached"] = False
        return usage

    def set_library_folder(self, payload: dict[str, Any]) -> dict[str, Any]:
        root = str(payload.get("path") or "").strip()
        info = self.library.set_root(root)
        log_event(f"library folder set to {info['root']}")
        return {"ok": True, "library": info, "state": self.state()}

    def choose_library_folder(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = str(payload.get("current") or self.library.root or "").strip()
        selected = choose_library_folder(current)
        if selected is None:
            return {"ok": True, "cancelled": True, "library": self.library.info(), "state": self.state()}
        info = self.library.set_root(selected)
        log_event(f"library folder set to {info['root']}")
        return {"ok": True, "cancelled": False, "library": info, "state": self.state()}

    def accounts(self) -> dict[str, Any]:
        self.sync_current_cli_account()
        data = read_accounts_file()
        current = account_record(self.auth_file)
        active_id = data.get("active_id") or current["id"]
        records = merge_account_records(data.get("accounts", []), self.auth_file)
        for record in records:
            record["selected"] = record["id"] == active_id or record["auth_file"] == str(Path(self.auth_file).expanduser())
        return {"accounts": records, "active_id": active_id, "cli_auth_file": self.cli_auth_file}

    def register_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        auth_file = str(payload.get("auth_file") or self.auth_file).strip()
        label = str(payload.get("label") or "").strip() or None
        if not auth_file:
            raise StudioError("Auth file path is required.")
        path = Path(auth_file).expanduser()
        if not path.exists():
            raise StudioError(f"Auth file not found: {path}", 404)
        data = read_accounts_file()
        saved = [item for item in data.get("accounts", []) if isinstance(item, dict)]
        record = snapshot_auth_file(str(path), label)
        record_email = record.get("email")
        if (
            label
            and "@" in label
            and isinstance(record_email, str)
            and record_email.lower() != label.lower()
        ):
            raise StudioError(
                f"Auth file belongs to {record_email}, not {label}. Log in as {label} first, then register again.",
                400,
            )
        saved = upsert_saved_account(saved, record)
        data = {"active_id": record["id"], "accounts": saved}
        write_accounts_file(data)
        installed = install_account_auth(record["auth_file"], self.cli_auth_file)
        self.auth_file = installed
        self.client.auth_file = installed
        with self.usage_lock:
            self.usage_cache = None
            self.usage_checked_at = 0.0
        log_event(f"CLI auth switched to registered account {record.get('email') or record.get('label') or record['id']}")
        return self.accounts()

    def set_active_account(self, account_id: str) -> dict[str, Any]:
        data = read_accounts_file()
        records = merge_account_records(data.get("accounts", []), self.auth_file)
        selected = next((record for record in records if record["id"] == account_id), None)
        if selected is None:
            raise StudioError("Account not found.", 404)
        if not selected["exists"]:
            raise StudioError(f"Auth file not found: {selected['auth_file']}", 404)
        installed = install_account_auth(selected["auth_file"], self.cli_auth_file)
        self.auth_file = installed
        self.client.auth_file = installed
        with self.usage_lock:
            self.usage_cache = None
            self.usage_checked_at = 0.0
        saved = [item for item in data.get("accounts", []) if isinstance(item, dict)]
        saved = upsert_saved_account(saved, selected)
        data = {"active_id": selected["id"], "accounts": saved}
        write_accounts_file(data)
        log_event(f"CLI auth switched to account {selected.get('email') or selected.get('label') or selected['id']}")
        return self.accounts()

    def heartbeat(self) -> dict[str, Any]:
        with self.shutdown_lock:
            self.last_heartbeat = time.monotonic()
        return {"ok": True}

    def request_shutdown(self, server: ThreadingHTTPServer, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.jobs.has_active():
            log_event("shutdown skipped: active job is running")
            return {"ok": False, "active_jobs": True}
        immediate = isinstance(payload, dict) and payload.get("event") == "restart-cleanup"
        token = uuid.uuid4().hex
        with self.shutdown_lock:
            self.shutdown_token = token
        threading.Thread(target=self._shutdown_if_idle, args=(server, token, immediate), daemon=True).start()
        return {"ok": True, "delay": 0.3 if immediate else 8}

    def _shutdown_if_idle(self, server: ThreadingHTTPServer, token: str, immediate: bool = False) -> None:
        time.sleep(0.3 if immediate else 8)
        with self.shutdown_lock:
            if self.shutdown_token != token:
                return
            idle_for = time.monotonic() - self.last_heartbeat
        if not immediate and idle_for < 6:
            log_event("shutdown cancelled: browser heartbeat resumed")
            return
        if self.jobs.has_active():
            log_event("shutdown cancelled: active job is running")
            return
        log_event("restart cleanup requested; shutting down local server" if immediate else "browser tab closed; shutting down local server")
        server.shutdown()

    def save_prompt(self, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = require_text(payload, "prompt")
        translation = str(payload.get("translation") or "").strip()
        title = str(payload.get("title") or "").strip() or safe_name(prompt[:48], "Prompt").replace("-", " ")
        stem = safe_file_stem(title, "Prompt")
        prompt_dir = self.library.gallery_output_dir(payload.get("gallery_folder_id"), "Prompt")
        path = unique_path(prompt_dir / f"{stem}.txt")
        path.write_text(prompt, encoding="utf-8")
        tags = normalize_tags(payload.get("tags"))
        item = {
            "id": uuid.uuid4().hex,
            "type": "prompt",
            "mode": payload.get("mode") or "note",
            "title": title,
            "prompt": prompt,
            "translation": translation,
            "category": payload.get("category") or "Prompt",
            "tags": tags,
            "created_at": utc_now(),
            "file": str(path),
            "local_url": None,
            "mime": "text/plain",
            "metadata": {
                "library_root": str(self.library.root),
                "gallery_folder_id": payload.get("gallery_folder_id"),
            },
        }
        return self.library.add_item(item)

    def analyze_image(self, payload: dict[str, Any]) -> dict[str, Any]:
        image = require_text(payload, "image")
        model = str(payload.get("model") or DEFAULT_ANALYZE_MODEL).strip()
        if model not in ANALYZE_MODELS:
            raise StudioError("Unsupported Analyze model.")
        request = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Analyze this image and write a detailed, reusable image-generation prompt in English. "
                                "Then translate that prompt naturally into Korean. Describe visible subjects, setting, "
                                "composition, lighting, colors, camera perspective, and style without inventing hidden "
                                "facts. Return exactly this plain-text structure with no markdown fences:\n"
                                "English\n\n<English prompt>\n\nKorean\n\n<Korean translation>"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": image_reference(image),
                        },
                    ],
                }
            ],
        }
        result = self.client.post("/chat/completions", request)
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise StudioError("Analyze response did not contain a result.", 502)
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            content = "\n".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict) and part.get("text")
            )
        if not isinstance(content, str) or not content.strip():
            raise StudioError("Analyze response did not contain text.", 502)
        english, korean = parse_analyze_result(content)
        return {"english": english, "korean": korean, "model": model}

    def translate_prompt(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = require_text(payload, "text")
        target_language = str(payload.get("target_language") or "Korean").strip()
        if target_language not in {"Korean", "English"}:
            raise StudioError("Unsupported translation language.")
        target_code = "ko" if target_language == "Korean" else "en"
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=auto&tl={target_code}&dt=t"
        )
        request = urllib.request.Request(
            url,
            data=urllib.parse.urlencode({"q": text}).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                "User-Agent": "Grok Studio Lab local translator",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=min(max(5, self.timeout), 30),
                context=https_context(),
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read(1000).decode("utf-8", errors="replace")
            raise StudioError(f"Google translation HTTP {exc.code}: {detail}", 502) from exc
        except urllib.error.URLError as exc:
            raise StudioError(f"Google translation network error: {format_network_error(exc)}", 502) from exc
        except json.JSONDecodeError as exc:
            raise StudioError("Google translation returned an invalid response.", 502) from exc
        segments = result[0] if isinstance(result, list) and result and isinstance(result[0], list) else []
        translation = "".join(
            str(segment[0])
            for segment in segments
            if isinstance(segment, list) and segment and isinstance(segment[0], str)
        ).strip()
        if not translation:
            raise StudioError("Google translation response did not contain text.", 502)
        return {
            "translation": translation,
            "target_language": target_language,
            "provider": "Google Translate",
        }

    def start_image(self, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = require_text(payload, "prompt")
        images = payload.get("images") or []
        if images and (not isinstance(images, list) or len(images) > 3):
            raise StudioError("Image editing accepts 1 to 3 source images.")
        mode = "image-edit" if images else "image-generate"
        job = self.jobs.create(mode, prompt, job_context(payload, mode))
        log_event(
            "queued "
            f"{mode} job={job['id']} prompt_chars={len(prompt)} "
            f"sources={len(images) if isinstance(images, list) else 0} "
            f"count={payload.get('n') or 1}"
        )
        thread = threading.Thread(
            target=self._run_image_job,
            args=(job["id"], payload, mode),
            daemon=True,
        )
        thread.start()
        return job

    def _run_image_job(self, job_id: str, payload: dict[str, Any], mode: str) -> None:
        try:
            self.jobs.update(job_id, status="submitting", progress=1)
            result = self.generate_image(payload, mode, job_id)
            self.jobs.update(job_id, status="done", progress=100, items=result.get("items", []))
        except JobCancelled:
            log_event(f"cancelled image job={job_id}")
            self.jobs.update(
                job_id,
                status="cancelled",
                error="Cancelled locally. The remote xAI request may still finish server-side.",
            )
        except Exception as exc:
            log_event(f"failed image job={job_id}: {exc}")
            self.jobs.update(job_id, status="failed", error=str(exc), progress=0)

    def generate_image(
        self,
        payload: dict[str, Any],
        mode: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        prompt = require_text(payload, "prompt")
        images = payload.get("images") or []
        if images and (not isinstance(images, list) or len(images) > 3):
            raise StudioError("Image editing accepts 1 to 3 source images.")
        mode = mode or ("image-edit" if images else "image-generate")
        request = compact(
            {
                "model": payload.get("model") or DEFAULT_IMAGE_MODEL,
                "prompt": prompt,
                "n": int(payload.get("n") or 1),
                "aspect_ratio": payload.get("aspect_ratio"),
                "resolution": payload.get("resolution"),
                "response_format": "b64_json",
            }
        )
        if images:
            refs = []
            for image in images:
                ref = image_reference(str(image))
                ref["type"] = "image_url"
                refs.append(ref)
            request["image" if len(refs) == 1 else "images"] = refs[0] if len(refs) == 1 else refs
            endpoint = "/images/edits"
        else:
            endpoint = "/images/generations"

        if job_id:
            self.raise_if_cancelled(job_id)
            self.jobs.update(job_id, status="processing", progress=5)
        result = self.client.post(endpoint, request)
        if job_id:
            self.raise_if_cancelled(job_id)
            self.jobs.update(job_id, status="saving", progress=90)
        saved = []
        for index, entry in enumerate(result.get("data", []), start=1):
            if not isinstance(entry, dict):
                continue
            item = self._save_image_result(entry, payload, prompt, mode, index, result)
            saved.append(item)
        return {"items": saved, "raw_count": len(result.get("data", []))}

    def _save_image_result(
        self,
        entry: dict[str, Any],
        payload: dict[str, Any],
        prompt: str,
        mode: str,
        index: int,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        item_id = uuid.uuid4().hex
        stem = f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{mode}-{index}-{item_id[:8]}"

        if isinstance(entry.get("b64_json"), str):
            media_bytes = base64.b64decode(entry["b64_json"])
            ext = guess_ext(entry.get("mime_type"), ".jpg")
            mime = entry.get("mime_type") or mimetypes.guess_type("x" + ext)[0] or "image/jpeg"
        elif isinstance(entry.get("url"), str):
            media_bytes, mime = self.download(entry["url"])
            ext = guess_ext(mime, ".jpg")
        else:
            raise StudioError("Image response did not contain a usable image.")

        image_dir = self.library.gallery_output_dir(payload.get("gallery_folder_id"), "Image")
        path = unique_path(image_dir / f"{stem}{ext}")
        path.write_bytes(media_bytes)
        metadata_path = self.write_metadata(item_id, result)
        group_id = str(payload.get("group_id") or payload.get("parent_id") or item_id)
        item = {
            "id": item_id,
            "type": "image",
            "mode": mode,
            "title": safe_name(prompt[:48], "Image").replace("-", " "),
            "prompt": prompt,
            "category": payload.get("category") or "Image",
            "tags": normalize_tags(payload.get("tags")),
            "created_at": utc_now(),
            "local_url": self.library.media_url(path),
            "file": str(path),
            "mime": mime,
            "metadata_file": str(metadata_path),
            "metadata": {
                "group_id": group_id,
                "parent_id": payload.get("parent_id"),
                "gallery_folder_id": payload.get("gallery_folder_id"),
                "model": payload.get("model") or DEFAULT_IMAGE_MODEL,
                "aspect_ratio": payload.get("aspect_ratio"),
                "resolution": payload.get("resolution"),
                "source_images": source_references_metadata(payload.get("images") or []),
            },
        }
        return self.library.add_item(item)

    def start_video(self, payload: dict[str, Any], mode: str) -> dict[str, Any]:
        prompt = require_text(payload, "prompt")
        job = self.jobs.create(mode, prompt, job_context(payload, mode))
        log_event(
            "queued "
            f"{mode} job={job['id']} prompt_chars={len(prompt)} "
            f"image={'yes' if payload.get('image') else 'no'} "
            f"refs={len(payload.get('reference_images') or [])} "
            f"duration={payload.get('duration') or 'default'}"
        )
        thread = threading.Thread(
            target=self._run_video_job,
            args=(job["id"], payload, mode),
            daemon=True,
        )
        thread.start()
        return job

    def _run_video_job(self, job_id: str, payload: dict[str, Any], mode: str) -> None:
        try:
            self.jobs.update(job_id, status="submitting", progress=1)
            prompt = require_text(payload, "prompt")
            request = self._video_payload(payload, mode)
            self.raise_if_cancelled(job_id)
            endpoint = {
                "video-generate": "/videos/generations",
                "video-extend": "/videos/extensions",
                "video-edit": "/videos/edits",
            }[mode]
            log_event(f"submitting {mode} job={job_id} endpoint={endpoint}")
            initial = self.client.post(endpoint, request)
            self.raise_if_cancelled(job_id)
            request_id = initial.get("request_id")
            if not isinstance(request_id, str):
                raise StudioError(f"Video response did not include request_id: {initial}", 502)
            log_event(f"xAI accepted job={job_id} request_id={request_id}")
            self.jobs.update(job_id, status="processing", progress=5, request_id=request_id)

            result = self.poll_video(job_id, request_id)
            self.raise_if_cancelled(job_id)
            item = self._save_video_result(result, payload, prompt, mode, request_id)
            log_event(f"saved video job={job_id} file={item.get('file')}")
            self.jobs.update(job_id, status="done", progress=100, item=item)
        except JobCancelled:
            log_event(f"cancelled video job={job_id}")
            self.jobs.update(
                job_id,
                status="cancelled",
                error="Cancelled locally. The remote xAI request may still finish server-side.",
            )
        except Exception as exc:
            log_event(f"failed video job={job_id}: {exc}")
            self.jobs.update(job_id, status="failed", error=str(exc), progress=0)

    def _video_payload(self, payload: dict[str, Any], mode: str) -> dict[str, Any]:
        prompt = require_text(payload, "prompt")
        request = compact(
            {
                "model": payload.get("model") or DEFAULT_VIDEO_MODEL,
                "prompt": prompt,
                "duration": int(payload["duration"]) if payload.get("duration") else None,
                "aspect_ratio": payload.get("aspect_ratio") if mode == "video-generate" else None,
                "resolution": payload.get("resolution") if mode == "video-generate" else None,
            }
        )

        if mode == "video-generate":
            image = payload.get("image")
            refs = payload.get("reference_images") or []
            if image and refs:
                raise StudioError("Use a start image or reference images, not both.")
            if image:
                request["image"] = image_reference(str(image))
            if refs:
                if not isinstance(refs, list) or len(refs) > 7:
                    raise StudioError("Reference-to-video accepts up to 7 images.")
                request["reference_images"] = [image_reference(str(ref)) for ref in refs]
        else:
            request["video"] = self.video_reference(payload)
        return request

    def video_reference(self, payload: dict[str, Any]) -> dict[str, str]:
        selected_id = payload.get("source_item_id")
        source_video = payload.get("video")
        trim_end = parse_trim_end(payload.get("source_trim_end"))
        trim_quality = trim_quality_settings(payload.get("source_trim_quality"))
        if selected_id:
            item = self.library.get_item(str(selected_id))
            file_path = item.get("file")
            if trim_end and isinstance(file_path, str):
                return {"url": trim_video_to_data_uri(Path(file_path), trim_end, trim_quality)}
            remote_url = item.get("remote_url")
            if isinstance(remote_url, str) and remote_url.startswith("http"):
                return {"url": remote_url}
            if isinstance(file_path, str):
                return {"url": file_to_data_uri(Path(file_path), "video/mp4")}
        if isinstance(source_video, str) and source_video:
            if trim_end:
                return {"url": trim_data_uri_video(source_video, trim_end, trim_quality)}
            return {"url": source_video}
        raise StudioError("Select a local video or upload a source video.")

    def poll_video(self, job_id: str, request_id: str) -> dict[str, Any]:
        started = time.monotonic()
        while True:
            self.raise_if_cancelled(job_id)
            result = self.client.get(f"/videos/{urllib.parse.quote(request_id)}")
            status = result.get("status")
            progress = result.get("progress")
            if isinstance(progress, int):
                self.jobs.update(job_id, status=status or "processing", progress=progress)
            if status == "done":
                return result
            if status in {"failed", "expired", "cancelled"}:
                raise StudioError(json.dumps(result, ensure_ascii=False, indent=2), 502)
            if time.monotonic() - started > 900:
                raise StudioError(f"Timed out waiting for video request {request_id}", 504)
            time.sleep(5)

    def raise_if_cancelled(self, job_id: str) -> None:
        if self.jobs.is_cancelled(job_id):
            raise JobCancelled()

    def _save_video_result(
        self,
        result: dict[str, Any],
        payload: dict[str, Any],
        prompt: str,
        mode: str,
        request_id: str,
    ) -> dict[str, Any]:
        video = result.get("video") if isinstance(result.get("video"), dict) else {}
        url = video.get("url") if isinstance(video, dict) else None
        if not isinstance(url, str):
            raise StudioError("Video response did not contain a video URL.", 502)

        media_bytes, mime = self.download(url)
        item_id = uuid.uuid4().hex
        stem = f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{mode}-{item_id[:8]}"
        video_dir = self.library.gallery_output_dir(payload.get("gallery_folder_id"), "Video")
        path = unique_path(video_dir / f"{stem}{guess_ext(mime, '.mp4')}")
        path.write_bytes(media_bytes)
        metadata_path = self.write_metadata(item_id, result)
        group_id = str(payload.get("group_id") or payload.get("parent_id") or item_id)
        item = {
            "id": item_id,
            "type": "video",
            "mode": mode,
            "title": "Video",
            "prompt": prompt,
            "category": payload.get("category") or "Video",
            "tags": normalize_tags(payload.get("tags")),
            "created_at": utc_now(),
            "local_url": self.library.media_url(path),
            "file": str(path),
            "mime": mime,
            "remote_url": url,
            "request_id": request_id,
            "metadata_file": str(metadata_path),
            "metadata": {
                "group_id": group_id,
                "parent_id": payload.get("parent_id"),
                "gallery_folder_id": payload.get("gallery_folder_id"),
                "model": payload.get("model") or DEFAULT_VIDEO_MODEL,
                "duration": video.get("duration") or payload.get("duration"),
                "aspect_ratio": payload.get("aspect_ratio"),
                "resolution": payload.get("resolution"),
                "start_image": source_reference_metadata(payload.get("image")),
                "reference_images": source_references_metadata(payload.get("reference_images") or []),
            },
        }
        return self.library.add_item(item)

    def download(self, url: str) -> tuple[bytes, str]:
        req = urllib.request.Request(url, headers={"User-Agent": "grok-studio/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=https_context()) as response:
                return response.read(), response.headers.get("Content-Type") or "application/octet-stream"
        except urllib.error.URLError as exc:
            raise StudioError(f"Could not download xAI media URL: {format_network_error(exc)}", 502) from exc

    def write_metadata(self, item_id: str, result: dict[str, Any]) -> Path:
        path = self.library.metadata_dir / f"{item_id}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def require_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise StudioError(f"{key} is required.")
    return value.strip()


def parse_analyze_result(value: str) -> tuple[str, str]:
    text = value.strip().replace("\r\n", "\n")
    text = re.sub(r"^```(?:text|markdown)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"(?im)^\s*[*#_`]*English\s*:?\s*[*#_`]*\s*$", "English", text)
    text = re.sub(r"(?im)^\s*[*#_`]*Korean\s*:?\s*[*#_`]*\s*$", "Korean", text)
    match = re.search(r"(?is)^\s*English\s+(.*?)\s+Korean\s+(.*?)\s*$", text)
    if not match:
        return text.strip(), ""
    return match.group(1).strip(), match.group(2).strip()


def normalize_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[,#]", value)
    elif isinstance(value, list):
        parts = [str(part) for part in value]
    else:
        parts = []
    tags = []
    for part in parts:
        tag = part.strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:12]


def summarize_detail(detail: dict[str, Any]) -> str:
    parts = []
    for key in ("promptLength", "imageFiles", "startImageFiles", "referenceImageFiles", "sourceVideoFiles"):
        value = detail.get(key)
        if isinstance(value, list):
            total = sum(item.get("size", 0) for item in value if isinstance(item, dict))
            parts.append(f"{key}={len(value)}/{total}B")
        elif value is not None:
            parts.append(f"{key}={value}")
    return " ".join(parts) or "-"


def delete_item_files(item: dict[str, Any]) -> None:
    file_path = item.get("file")
    metadata_path = item.get("metadata_file")
    if isinstance(file_path, str):
        for root in deletion_roots():
            safe_unlink(file_path, root)
    if isinstance(metadata_path, str):
        for root in deletion_roots():
            safe_unlink(metadata_path, root)


def deletion_roots() -> list[Path]:
    roots = [DATA_DIR, MEDIA_DIR, META_DIR, TMP_DIR]
    external = external_library_root()
    if external is not None:
        roots.append(external)
        roots.append(external / EXTERNAL_META_DIR_NAME)
    return roots


def image_reference(value: str) -> dict[str, str]:
    if value.startswith("/media/"):
        return {"url": file_to_data_uri(resolve_media_path(value), "image/png")}
    return {"url": value}


def source_reference_metadata(value: Any) -> dict[str, str] | None:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("/media/") or value.startswith("http://") or value.startswith("https://"):
        return {"url": value}
    return None


def source_references_metadata(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list):
        return []
    return [ref for ref in (source_reference_metadata(value) for value in values) if ref]


def job_context(payload: dict[str, Any], mode: str) -> dict[str, Any]:
    return compact(
        {
            "mode": mode,
            "group_id": payload.get("group_id"),
            "parent_id": payload.get("parent_id"),
            "preview_url": payload.get("preview_url"),
            "preview_type": payload.get("preview_type"),
            "gallery_folder_id": payload.get("gallery_folder_id"),
        }
    )


def parse_trim_end(value: Any) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not (0.25 < seconds < 60 * 60):
        return None
    return round(seconds, 3)


def trim_quality_settings(value: Any) -> dict[str, str]:
    quality = str(value or "high").strip().lower()
    crf_by_quality = {
        "high": "16",
        "medium": "18",
        "low": "20",
    }
    return {
        "quality": quality if quality in crf_by_quality else "high",
        "crf": crf_by_quality.get(quality, "16"),
        "preset": "medium",
    }


def ffmpeg_binary() -> str:
    configured = os.environ.get("GROK_STUDIO_FFMPEG")
    if configured:
        path = Path(configured).expanduser()
        if path.exists():
            return str(path)
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        path = Path(candidate)
        if path.exists():
            return str(path)
    if os.name == "nt":
        message = "Extending from a paused point requires ffmpeg. Install ffmpeg and add it to Windows PATH."
    else:
        message = "Extending from a paused point requires ffmpeg. Install it with Homebrew: brew install ffmpeg"
    raise StudioError(message, 500)


def trim_data_uri_video(value: str, end_seconds: float, quality: dict[str, str]) -> str:
    media_bytes, mime = data_uri_to_bytes(value)
    source = unique_path(TMP_DIR / f"source-{uuid.uuid4().hex[:8]}{guess_ext(mime, '.mp4')}")
    source.write_bytes(media_bytes)
    try:
        return trim_video_to_data_uri(source, end_seconds, quality)
    finally:
        safe_unlink(str(source), TMP_DIR)


def trim_video_to_data_uri(path: Path, end_seconds: float, quality: dict[str, str]) -> str:
    if not path.is_file():
        raise StudioError(f"Local file is missing: {path}", 404)
    log_event(
        "trimming source video "
        f"end={end_seconds:.3f}s quality={quality['quality']} crf={quality['crf']} preset={quality['preset']}"
    )
    output = unique_path(TMP_DIR / f"trim-{uuid.uuid4().hex[:8]}.mp4")
    command = [
        ffmpeg_binary(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-t",
        f"{end_seconds:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        quality["preset"],
        "-crf",
        quality["crf"],
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise StudioError(f"Could not trim source video: {exc}", 500) from exc
    if result.returncode != 0:
        safe_unlink(str(output), TMP_DIR)
        detail = result.stderr.strip() or "ffmpeg failed"
        raise StudioError(f"Could not trim source video: {detail}", 500)
    try:
        return file_to_data_uri(output, "video/mp4")
    finally:
        safe_unlink(str(output), TMP_DIR)


def safe_unlink(path_text: str, root: Path) -> None:
    try:
        path = Path(path_text).expanduser().resolve()
        root_resolved = root.resolve()
    except OSError:
        return
    if path != root_resolved and root_resolved not in path.parents:
        log_event(f"refused to delete outside {root_resolved}: {path}")
        return
    try:
        if path.is_file():
            path.unlink()
            log_event(f"deleted local file {path}")
    except OSError as exc:
        log_event(f"could not delete {path}: {exc}")


def media_url(path: Path) -> str:
    resolved = path.resolve()
    for root in media_roots():
        try:
            rel = resolved.relative_to(root.resolve())
            return "/media/" + urllib.parse.quote(str(rel).replace(os.sep, "/"))
        except ValueError:
            continue
    raise StudioError(f"Media path is outside the library: {path}", 500)


def resolve_media_path(url_path: str) -> Path:
    rel = urllib.parse.unquote(url_path.removeprefix("/media/"))
    for root in media_roots():
        candidate = (root / rel).resolve()
        root_resolved = root.resolve()
        if root_resolved not in candidate.parents and candidate != root_resolved:
            continue
        if candidate.is_file():
            return candidate
    raise StudioError("Media not found.", 404)


def media_roots() -> list[Path]:
    roots = [MEDIA_DIR, DATA_DIR]
    external = external_library_root()
    if external is not None:
        roots.extend([external, external / "Image", external / "Video", external / "Upload Image"])
    return roots


def open_media_folder() -> dict[str, Any]:
    ensure_dirs()
    root = external_library_root() or MEDIA_DIR
    try:
        if os.name == "nt":
            os.startfile(str(root))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(root)])
        else:
            subprocess.Popen(["xdg-open", str(root)])
    except OSError as exc:
        raise StudioError(f"Could not open media folder: {exc}", 500) from exc
    return {"ok": True, "path": str(root)}


def applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def choose_folder_in_windows_explorer(current: str | None = None) -> str | None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        raise StudioError("Windows PowerShell is required to select a library folder.", 500)
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$dialog.Description = 'Select a Library folder for Grok Studio Lab'; "
        "$dialog.ShowNewFolderButton = $true; "
        "$current = $env:GROK_STUDIO_PICKER_CURRENT; "
        "if ($current -and (Test-Path -LiteralPath $current -PathType Container)) "
        "{ $dialog.SelectedPath = $current }; "
        "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
        "{ [Console]::Write($dialog.SelectedPath) }"
    )
    env = os.environ.copy()
    env["GROK_STUDIO_PICKER_CURRENT"] = str(current or "")
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-STA", "-Command", script],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise StudioError(f"Could not open Windows folder picker: {exc}", 500) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "Windows folder picker failed"
        raise StudioError(f"Could not choose library folder: {detail}", 500)
    selected = result.stdout.strip()
    return selected or None


def choose_folder_in_finder(current: str | None = None) -> str | None:
    prompt = "Select a Library folder for Grok Studio Lab"
    current_path = Path(current or "").expanduser() if current else None
    if current_path and current_path.is_dir():
        script = (
            f'set chosenFolder to choose folder with prompt {applescript_string(prompt)} '
            f'default location POSIX file {applescript_string(str(current_path))}\n'
            "return POSIX path of chosenFolder"
        )
    else:
        script = (
            f"set chosenFolder to choose folder with prompt {applescript_string(prompt)}\n"
            "return POSIX path of chosenFolder"
        )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise StudioError(f"Could not open Finder folder picker: {exc}", 500) from exc
    if result.returncode == 0:
        selected = result.stdout.strip()
        return selected or None
    if "User canceled" in result.stderr or "(-128)" in result.stderr:
        return None
    detail = result.stderr.strip() or result.stdout.strip() or "Finder folder picker failed"
    raise StudioError(f"Could not choose library folder: {detail}", 500)


def choose_library_folder(current: str | None = None) -> str | None:
    if os.name == "nt":
        return choose_folder_in_windows_explorer(current)
    if sys.platform == "darwin":
        return choose_folder_in_finder(current)
    raise StudioError("Native folder selection is supported on Windows and macOS.", 500)


def make_handler(app: StudioApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "GrokStudio/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def do_GET(self) -> None:
            try:
                self.route_get()
            except StudioError as exc:
                self.send_json({"error": exc.message}, exc.status)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)

        def do_POST(self) -> None:
            try:
                self.route_post()
            except StudioError as exc:
                self.send_json({"error": exc.message}, exc.status)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)

        def route_get(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path in {"/", "/index.html"}:
                self.send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            elif path == "/editor.html":
                self.send_file(STATIC_DIR / "editor.html", "text/html; charset=utf-8")
            elif path == "/assets/app.css":
                self.send_file(STATIC_DIR / "app.css", "text/css; charset=utf-8")
            elif path == "/assets/app.js":
                self.send_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
            elif path.startswith("/assets/"):
                asset_name = urllib.parse.unquote(path.removeprefix("/assets/"))
                if not asset_name or "\\" in asset_name:
                    raise StudioError("Asset not found.", 404)
                asset_path = (STATIC_DIR / asset_name).resolve()
                static_root = STATIC_DIR.resolve()
                if static_root not in asset_path.parents and asset_path != static_root:
                    raise StudioError("Asset not found.", 404)
                self.send_file(asset_path, mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream")
            elif path == "/api/state":
                self.send_json(app.state())
            elif path == "/api/system-fonts":
                self.send_json({"fonts": system_font_families()})
            elif path == "/api/account-usage":
                self.send_json({"usage": app.account_usage(False)})
            elif path == "/api/accounts":
                self.send_json(app.accounts())
            elif path == "/api/jobs":
                self.send_json({"jobs": app.jobs.all()})
            elif path.startswith("/api/jobs/"):
                self.send_json({"job": app.jobs.get(path.rsplit("/", 1)[-1])})
            elif path.startswith("/media/"):
                media_path = resolve_media_path(path)
                self.send_file(media_path, mimetypes.guess_type(media_path.name)[0] or "application/octet-stream")
            elif path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            else:
                raise StudioError("Not found.", 404)

        def route_post(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            length = int(self.headers.get("Content-Length") or 0)
            log_event(f"POST {path} body={length} bytes")
            payload = self.read_json()
            if path == "/api/client-event":
                event = payload.get("event") or "unknown"
                detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
                log_event(f"client event={event} mode={payload.get('mode')} detail={summarize_detail(detail)}")
                self.send_json({"ok": True})
            elif path == "/api/heartbeat":
                self.send_json(app.heartbeat())
            elif path == "/api/shutdown":
                self.send_json(app.request_shutdown(self.server, payload))
            elif path == "/api/categories":
                categories = app.library.add_category(require_text(payload, "name"))
                self.send_json({"categories": categories})
            elif path == "/api/gallery/folders":
                folder = app.library.add_gallery_folder(
                    require_text(payload, "name"),
                    str(payload.get("parent_id") or "").strip() or None,
                )
                self.send_json({"folder": folder, "state": app.state()})
            elif path == "/api/gallery/folders/delete":
                self.send_json(app.library.delete_gallery_folder(require_text(payload, "folder_id")))
            elif path == "/api/gallery/folders/rename":
                folder = app.library.rename_gallery_folder(
                    require_text(payload, "folder_id"),
                    require_text(payload, "name"),
                )
                self.send_json({"folder": folder, "state": app.state()})
            elif path == "/api/gallery/folders/layout":
                self.send_json(app.library.update_gallery_folder_layout(
                    payload.get("folders"),
                    payload.get("sort_mode") if "sort_mode" in payload else None,
                ))
            elif path == "/api/prompts":
                self.send_json({"item": app.save_prompt(payload)})
            elif path == "/api/analyze":
                self.send_json(app.analyze_image(payload))
            elif path == "/api/translate":
                self.send_json(app.translate_prompt(payload))
            elif path == "/api/uploads/images":
                self.send_json(app.save_uploaded_images(payload))
            elif path == "/api/image-editor/save":
                self.send_json(app.save_image_edit(payload))
            elif path == "/api/uploads/images/delete":
                self.send_json(app.delete_uploaded_image(payload))
            elif path == "/api/image":
                self.send_json({"job": app.start_image(payload)})
            elif path == "/api/video":
                self.send_json({"job": app.start_video(payload, "video-generate")})
            elif path == "/api/video/extend":
                self.send_json({"job": app.start_video(payload, "video-extend")})
            elif path == "/api/video/edit":
                self.send_json({"job": app.start_video(payload, "video-edit")})
            elif path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job_id = path.split("/")[3]
                self.send_json({"job": app.jobs.cancel(job_id)})
            elif path.startswith("/api/jobs/") and path.endswith("/dismiss"):
                job_id = path.split("/")[3]
                self.send_json({"job": app.jobs.dismiss(job_id)})
            elif path == "/api/items/delete":
                ids = payload.get("ids")
                if not isinstance(ids, list):
                    raise StudioError("ids must be a list.")
                self.send_json(app.library.delete_items([str(item_id) for item_id in ids]))
            elif path == "/api/items/move-to-gallery":
                ids = payload.get("ids")
                if not isinstance(ids, list):
                    raise StudioError("ids must be a list.")
                self.send_json(app.library.move_items_to_gallery(
                    [str(item_id) for item_id in ids],
                    require_text(payload, "folder_id"),
                ))
            elif path == "/api/open-media-folder":
                self.send_json(open_media_folder())
            elif path == "/api/library-folder":
                self.send_json(app.set_library_folder(payload))
            elif path == "/api/choose-library-folder":
                self.send_json(app.choose_library_folder(payload))
            elif path == "/api/account-usage/refresh":
                self.send_json({"usage": app.account_usage(True)})
            elif path == "/api/account-usage/import":
                self.send_json({"usage": app.import_account_usage(payload)})
            elif path == "/api/accounts/register":
                self.send_json(app.register_account(payload))
            elif path == "/api/accounts/select":
                account_id = require_text(payload, "id")
                self.send_json(app.set_active_account(account_id))
            elif path.startswith("/api/items/") and path.endswith("/update"):
                item_id = path.split("/")[3]
                self.send_json({"item": app.library.update_item(item_id, payload)})
            else:
                raise StudioError("Not found.", 404)

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise StudioError("Request body is too large.", 413)
            raw = self.rfile.read(length)
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise StudioError("Invalid JSON body.") from exc
            if not isinstance(data, dict):
                raise StudioError("JSON body must be an object.")
            return data

        def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_file(self, path: Path, content_type: str) -> None:
            if not path.is_file():
                raise StudioError("Not found.", 404)
            size = path.stat().st_size
            range_header = self.headers.get("Range")
            if range_header and range_header.startswith("bytes="):
                start_text, _, end_text = range_header.removeprefix("bytes=").partition("-")
                start = int(start_text or 0)
                end = int(end_text) if end_text else size - 1
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with path.open("rb") as file:
                    file.seek(start)
                    self.wfile.write(file.read(length))
                return

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with path.open("rb") as file:
                while True:
                    chunk = file.read(1024 * 512)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

    return Handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Grok Studio as a local web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--auth-file", default=DEFAULT_AUTH_FILE)
    parser.add_argument("--base-url", default=API_BASE)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--open", action="store_true", help="Open the browser after starting.")
    parser.add_argument("--check", action="store_true", help="Print local config and exit.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ensure_dirs()
    app = StudioApp(args.auth_file, args.base_url, args.timeout)
    if args.check:
        print(json.dumps(app.state(), ensure_ascii=False, indent=2))
        return 0

    url = f"http://{args.host}:{args.port}"
    try:
        server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE and getattr(exc, "winerror", None) != 10048:
            raise
        log_event(f"port {args.port} is busy; asking previous local server to shut down")
        request_previous_shutdown(url)
        time.sleep(1.6)
        server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    print(f"{APP_NAME} running at {url}")
    print(f"Local library: {app.library.info()['root']}")
    if args.host != "127.0.0.1":
        print("Warning: non-local host binding can expose the studio on your network.")
    if args.open:
        token = secrets.token_hex(2)
        threading.Timer(0.4, lambda: webbrowser.open(url + f"/?t={token}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Grok Studio.")
    finally:
        server.server_close()
    return 0


def request_previous_shutdown(url: str) -> None:
    payload = json.dumps({"event": "restart-cleanup", "at": utc_now()}).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + "/api/shutdown",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=1.2).read()
    except Exception as exc:
        log_event(f"previous server shutdown request did not complete: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
