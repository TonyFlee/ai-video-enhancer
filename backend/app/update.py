"""Update checks against GitHub Releases - offline-first and never blocking.

Design rules (the app must work with no network at all):

* ``check_for_update`` returns ``None`` on *any* failure (no repo, rate limit,
  offline, weird payload) - the UI then simply shows no banner.
* Results are cached in-process so a UI reload does not hammer the GitHub API
  (unauthenticated: 60 requests/hour per IP).
* The download runs in a background thread; the API only reads progress from a
  small module-level state dict, mirroring the job pattern.

"Update now" downloads the setup exe (verifying the SHA-256 published in the
release notes when present) and then launches it as a normal, visible Windows
installer - the familiar wizard flow the user already knows. The installer
closes the running app itself (PrepareToInstall taskkill) and the Finish page
offers "Launch AI Video Enhancer", so the app comes back updated.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib import request as urlrequest

from .config import STORAGE_DIR, UPDATE_REPO, AVE_VERSION

logger = logging.getLogger("app.update")

GITHUB_API = os.environ.get(
    "AVE_UPDATE_API", "https://api.github.com/repos/{repo}/releases/latest"
)
ASSET_PATTERN = re.compile(r"AI-Video-Enhancer-Setup-.*\.exe$", re.IGNORECASE)
SHA256_PATTERN = re.compile(r"SHA-?256[:=]\s*([0-9a-fA-F]{64})")
CHECK_TTL_SECONDS = 6 * 3600.0    # success: re-check twice a day
FAILURE_TTL_SECONDS = 10 * 60.0   # failure: retry soon, but not on every reload
DOWNLOAD_CHUNK = 1024 * 1024
# A setup bigger than this is not a plausible build of this app; refuse it
# rather than filling the user's disk on a bogus/compromised release.
MAX_INSTALLER_BYTES = 4 * 1024**3
SAFE_INSTALLER_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def parse_version(text: str) -> tuple[int, int, int]:
    """'v1.2.3' -> (1, 2, 3). Tolerant: missing/odd parts become 0."""
    cleaned = (text or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in cleaned.split(".")[:3]:
        match = re.match(r"\d+", chunk)
        parts.append(int(match.group()) if match else 0)
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def is_newer(candidate: str, current: str) -> bool:
    """True when version ``candidate`` is strictly newer than ``current``."""
    return parse_version(candidate) > parse_version(current)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_safe_installer_url(url: str) -> bool:
    """Only authenticated HTTPS URLs ending in .exe may ever be downloaded."""
    lowered = (url or "").strip().lower()
    if not lowered.startswith("https://"):
        return False
    return lowered.split("?", 1)[0].endswith(".exe")


def _extract_sha256(body: str) -> str | None:
    match = SHA256_PATTERN.search(body or "")
    return match.group(1).lower() if match else None


def _strip_sha_line(body: str) -> str:
    """The SHA-256 line is metadata for the updater, not prose for the user."""
    return re.sub(rf"^\s*SHA-?256[:=]\s*[0-9a-fA-F]{{64}}\s*$", "", body or "", flags=re.MULTILINE).strip()


@dataclass
class UpdateInfo:
    tag: str
    version: str
    url: str            # asset download URL
    size_bytes: int
    notes: str          # release body, SHA line removed
    published_at: str
    sha256: str | None  # published hash, when the release notes carry one


@dataclass
class DownloadState:
    state: str = "idle"            # idle | downloading | ready | error
    tag: str | None = None
    received: int = 0
    total: int = 0
    path: str | None = None
    sha256_ok: bool | None = None  # None = nothing published to verify against
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        percent = (self.received / self.total * 100.0) if self.total else None
        data["percent"] = round(percent, 1) if percent is not None else None
        return data


# ------------------------------------------------------------------- check
_CHECK_CACHE: tuple[float, UpdateInfo | None] | None = None


def check_for_update(force: bool = False) -> UpdateInfo | None:
    """Latest release, or None when there is none / we cannot tell.

    None is the normal offline answer - callers hide the banner and move on.
    """
    global _CHECK_CACHE

    now = time.monotonic()
    if not force and _CHECK_CACHE is not None:
        cached_at, cached = _CHECK_CACHE
        ttl = CHECK_TTL_SECONDS if cached is not None else FAILURE_TTL_SECONDS
        if now - cached_at < ttl:
            return cached

    info: UpdateInfo | None = None
    try:
        req = urlrequest.Request(
            GITHUB_API.format(repo=UPDATE_REPO),
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"AI-Video-Enhancer/{AVE_VERSION}",
            },
        )
        with urlrequest.urlopen(req, timeout=8) as response:  # noqa: S310 - fixed HTTPS host
            payload = json_loads(response.read())
        info = _parse_release(payload)
    except Exception as exc:  # noqa: BLE001 - offline, rate limited, odd payload...
        logger.info("Update check unavailable: %r", exc)
        info = None

    _CHECK_CACHE = (now, info)
    return info


def json_loads(raw: bytes) -> Any:
    import json

    return json.loads(raw.decode("utf-8"))


def _parse_release(payload: Any) -> UpdateInfo | None:
    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name") or "")
    if not tag or not is_newer(tag, AVE_VERSION):
        return None

    assets = payload.get("assets") or []
    asset = next(
        (a for a in assets if isinstance(a, dict) and ASSET_PATTERN.match(str(a.get("name") or ""))),
        None,
    )
    if not asset:
        logger.info("Release %s has no installer asset - ignoring it", tag)
        return None

    url = str(asset.get("browser_download_url") or "")
    if not _is_safe_installer_url(url):
        logger.warning("Release %s installer URL is not a safe HTTPS .exe: %r", tag, url)
        return None

    size_bytes = _safe_int(asset.get("size"), 0)
    if size_bytes > MAX_INSTALLER_BYTES:
        logger.warning("Release %s installer is implausibly large (%d bytes)", tag, size_bytes)
        return None

    body = str(payload.get("body") or "")
    return UpdateInfo(
        tag=tag,
        version=".".join(str(part) for part in parse_version(tag)),
        url=url,
        size_bytes=size_bytes,
        notes=_strip_sha_line(body),
        published_at=str(payload.get("published_at") or ""),
        sha256=_extract_sha256(body),
    )


# ---------------------------------------------------------------- download
_DOWNLOAD = DownloadState()
_DOWNLOAD_LOCK = threading.Lock()


def download_state() -> dict[str, Any]:
    return _DOWNLOAD.as_dict()


def start_download(info: UpdateInfo) -> dict[str, Any]:
    """Kick off the asset download in a background thread (idempotent)."""
    if not _is_safe_installer_url(info.url):
        raise ValueError("The update URL is not a valid HTTPS installer.")
    if info.size_bytes > MAX_INSTALLER_BYTES:
        raise ValueError("The update is larger than the allowed installer size.")

    with _DOWNLOAD_LOCK:
        if _DOWNLOAD.state == "downloading":
            return _DOWNLOAD.as_dict()
        _DOWNLOAD.__init__(  # reset any previous ready/error state
            state="downloading",
            tag=info.tag,
            total=info.size_bytes,
        )
        name = Path(info.url.split("?", 1)[0]).name
        if not name or not SAFE_INSTALLER_NAME.fullmatch(name) or not name.lower().endswith(".exe"):
            name = f"AI-Video-Enhancer-Setup-{info.version}.exe"
        target = _updates_dir() / name
        _DOWNLOAD.path = str(target)

    thread = threading.Thread(
        target=_run_download, args=(info, target), name="update-download", daemon=True
    )
    thread.start()
    return _DOWNLOAD.as_dict()


def _updates_dir() -> Path:
    directory = STORAGE_DIR / "updates"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _run_download(info: UpdateInfo, target: Path) -> None:
    digest = hashlib.sha256()
    received = 0
    temp = target.with_suffix(target.suffix + ".part")
    try:
        req = urlrequest.Request(
            info.url, headers={"User-Agent": f"AI-Video-Enhancer/{AVE_VERSION}"}
        )
        with urlrequest.urlopen(req, timeout=60) as response:  # noqa: S310 - fixed HTTPS host
            declared = _safe_int(response.headers.get("Content-Length"), 0)
            if declared < 0 or declared > MAX_INSTALLER_BYTES:
                raise ValueError("The server announced an implausible installer size.")
            if info.size_bytes > 0 and declared > 0 and declared != info.size_bytes:
                raise ValueError("The installer size does not match the release metadata.")
            expected = info.size_bytes or declared
            if expected:
                _DOWNLOAD.total = expected
            limit = min(MAX_INSTALLER_BYTES, expected) if expected else MAX_INSTALLER_BYTES

            with temp.open("wb") as handle:
                while chunk := response.read(DOWNLOAD_CHUNK):
                    received += len(chunk)
                    if received > limit:
                        raise ValueError("The installer is larger than expected; download aborted.")
                    handle.write(chunk)
                    digest.update(chunk)
                    _DOWNLOAD.received = received
            if expected and received != expected:
                raise ValueError(
                    f"Download was truncated ({received} of {expected} bytes)."
                )
            temp.replace(target)

        _DOWNLOAD.received = received
        _DOWNLOAD.state = "ready"
        if info.sha256:
            _DOWNLOAD.sha256_ok = digest.hexdigest().lower() == info.sha256
            if not _DOWNLOAD.sha256_ok:
                _DOWNLOAD.state = "error"
                _DOWNLOAD.error = "Downloaded file failed the SHA-256 check - not installing it."
                target.unlink(missing_ok=True)
        else:
            _DOWNLOAD.sha256_ok = None  # nothing published to verify against
        if _DOWNLOAD.state == "ready":
            _prune_installers(keep=target)
        logger.info("Update %s downloaded (%d bytes, sha ok=%s)", info.tag, received, _DOWNLOAD.sha256_ok)
    except Exception as exc:  # noqa: BLE001 - surface any network/disk failure
        logger.exception("Update download failed")
        _DOWNLOAD.state = "error"
        _DOWNLOAD.error = str(exc)
        temp.unlink(missing_ok=True)
        target.unlink(missing_ok=True)


def _prune_installers(*, keep: Path | None) -> None:
    """Remove old setup files so repeated updates do not fill storage."""
    directory = _updates_dir()
    for path in directory.iterdir():
        try:
            if not path.is_file():
                continue
            if keep is not None and path.resolve() == keep.resolve():
                continue
            if path.suffix.lower() in (".exe", ".part"):
                path.unlink(missing_ok=True)
        except OSError:
            continue


def ready_installer() -> Path | None:
    """Path of a fully downloaded, verified installer, when there is one."""
    if _DOWNLOAD.state != "ready" or not _DOWNLOAD.path:
        return None
    path = Path(_DOWNLOAD.path)
    if not path.is_file() or path.suffix.lower() != ".exe" or path.stat().st_size <= 0:
        return None
    return path


def launch_installer(path: Path) -> None:
    """Open the setup wizard (visible - not CREATE_NO_WINDOW). Windows only."""
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != ".exe" or path.stat().st_size <= 0:
        raise OSError(f"Refusing to launch a missing or invalid installer: {path}")
    logger.info("Launching updater: %s", path)
    os.startfile(str(path))  # noqa: S606 - deliberate: the user must see the wizard
