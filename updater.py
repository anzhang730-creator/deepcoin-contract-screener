from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import ctypes
from pathlib import Path


REPOSITORY = os.environ.get(
    "DEEPCOIN_UPDATE_REPOSITORY",
    "anzhang730-creator/deepcoin-contract-screener",
)
CURRENT_VERSION = "1.0.4"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASE_PAGE_URL = f"https://github.com/{REPOSITORY}/releases/latest"
ASSET_NAME = "DeepcoinScreen-Windows.zip"
CHECKSUMS_NAME = "SHA256SUMS.txt"
MAX_ARCHIVE_BYTES = 250 * 1024 * 1024
MAX_UNPACKED_BYTES = 500 * 1024 * 1024
MAX_ARCHIVE_FILES = 256
MAX_CHECKSUM_BYTES = 64 * 1024
LOCK_PREFIX = "deepcoin-screener-update"
VERSION_PATTERN = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?$", re.I)
UPDATE_LOG_PATH = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "DeepcoinScreen" / "update.log"


def _log_update(message: str) -> None:
    try:
        UPDATE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with UPDATE_LOG_PATH.open("a", encoding="utf-8") as output:
            output.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def _process_alive(pid: int) -> bool:
    """Check a lock owner without os.kill(pid, 0)'s Windows exit-code side effect."""
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def current_version() -> str:
    """Return the installed version, using package metadata when frozen."""
    if getattr(sys, "frozen", False):
        version_file = Path(sys.executable).with_name("version.txt")
        try:
            value = version_file.read_text(encoding="ascii").strip()
            if value:
                return value
        except OSError:
            pass
    return os.environ.get("DEEPCOIN_VERSION", CURRENT_VERSION)


def _version(value: str) -> tuple[int, int, int, int]:
    """Normalize valid v1, v1.2 and v1.2.3 tags for numeric comparison."""
    match = VERSION_PATTERN.fullmatch((value or "").strip())
    if not match:
        return (0, 0, 0, 0)
    return (*[int(part or 0) for part in match.groups()], 0)


def _github_download_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise ValueError("release asset must be hosted on github.com")
    return value


def _request_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "deepcoin-contract-screener",
        },
    )
    with urllib.request.urlopen(request, timeout=4) as response:
        payload = response.read(2 * 1024 * 1024)
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid release response")
    return value


def _latest_release() -> dict:
    """Read the API first, then use GitHub's redirect when the API is rate-limited."""
    try:
        return _request_json(RELEASES_URL)
    except urllib.error.HTTPError as error:
        if error.code not in (403, 429):
            raise
    request = urllib.request.Request(
        RELEASE_PAGE_URL,
        headers={"User-Agent": "deepcoin-contract-screener"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        final_url = response.geturl()
    match = re.search(r"/releases/tag/([^/?#]+)", urllib.parse.urlparse(final_url).path)
    if not match:
        raise ValueError("latest release redirect did not contain a tag")
    tag = urllib.parse.unquote(match.group(1))
    encoded_tag = urllib.parse.quote(tag, safe="")
    base_url = f"https://github.com/{REPOSITORY}/releases/download/{encoded_tag}"
    return {
        "tag_name": tag,
        "_fallback": True,
        "assets": [
            {
                "name": ASSET_NAME,
                "browser_download_url": f"{base_url}/{ASSET_NAME}",
            },
            {
                "name": CHECKSUMS_NAME,
                "browser_download_url": f"{base_url}/{CHECKSUMS_NAME}",
            },
        ],
    }


def check_for_update() -> dict:
    """Return a small, UI-safe update summary without downloading an archive."""
    current = current_version()
    release = _latest_release()
    tag = str(release.get("tag_name", "")).strip()
    if not VERSION_PATTERN.fullmatch(tag):
        raise ValueError("latest release has an invalid version tag")
    return {
        "currentVersion": current,
        "latestVersion": tag,
        "updateAvailable": _version(tag) > _version(current),
        "releaseUrl": f"https://github.com/{REPOSITORY}/releases/tag/{urllib.parse.quote(tag, safe='')}",
        "repository": REPOSITORY,
        "checkedAt": int(time.time() * 1000),
        "packaged": bool(getattr(sys, "frozen", False)),
        # Kept private so the app can schedule the exact release it just checked.
        "_release": release,
    }


def _read_github_asset(url: str, maximum_bytes: int, timeout: int = 30) -> bytes:
    request = urllib.request.Request(
        _github_download_url(url),
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "deepcoin-contract-screener",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > maximum_bytes:
            raise ValueError("release asset is too large")
        content = response.read(maximum_bytes + 1)
    if len(content) > maximum_bytes:
        raise ValueError("release asset is too large")
    return content


def _checksum_from_manifest(manifest: bytes, asset_name: str) -> str:
    text = manifest.decode("ascii")
    for line in text.splitlines():
        match = re.fullmatch(r"\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*", line)
        if match and match.group(2).strip().replace("\\", "/").rsplit("/", 1)[-1] == asset_name:
            return match.group(1).lower()
    raise ValueError(f"checksum manifest does not contain {asset_name}")


def _asset_digest(release: dict, asset: dict, tag: str) -> str:
    digest = str(asset.get("digest") or "").strip()
    if digest.startswith("sha256:"):
        digest = digest[7:]
    if re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        return digest.lower()
    assets = release.get("assets") or []
    checksum_asset = next(
        (item for item in assets if isinstance(item, dict) and item.get("name") == CHECKSUMS_NAME),
        None,
    )
    if checksum_asset and checksum_asset.get("browser_download_url"):
        checksum_url = str(checksum_asset["browser_download_url"])
    elif release.get("_fallback"):
        encoded_tag = urllib.parse.quote(tag, safe="")
        checksum_url = f"https://github.com/{REPOSITORY}/releases/download/{encoded_tag}/{CHECKSUMS_NAME}"
    else:
        raise ValueError("release does not provide a verifiable SHA-256 digest")
    manifest = _read_github_asset(checksum_url, MAX_CHECKSUM_BYTES)
    return _checksum_from_manifest(manifest, ASSET_NAME)


def _download(url: str, destination: Path, expected_size: int | None, expected_digest: str | None) -> None:
    url = _github_download_url(url)
    if not expected_digest or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_digest.removeprefix("sha256:")):
        raise ValueError("release archive is missing a valid SHA-256 digest")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "deepcoin-contract-screener",
        },
    )
    digest = hashlib.sha256()
    bytes_read = 0
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_ARCHIVE_BYTES:
            raise ValueError("release archive is too large")
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > MAX_ARCHIVE_BYTES:
                raise ValueError("release archive is too large")
            digest.update(chunk)
            output.write(chunk)
    if expected_size is not None and bytes_read != expected_size:
        raise ValueError("release archive size mismatch")
    normalized = expected_digest.removeprefix("sha256:").lower()
    if normalized != digest.hexdigest().lower():
        raise ValueError("release archive hash mismatch")


def _safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    unpacked_bytes = 0
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if len(members) > MAX_ARCHIVE_FILES:
            raise ValueError("release archive contains too many files")
        seen: set[str] = set()
        for member in members:
            normalized_name = member.filename.replace("\\", "/").casefold()
            if normalized_name in seen:
                raise ValueError("release archive contains duplicate paths")
            seen.add(normalized_name)
            target = (destination / member.filename).resolve()
            if target != destination and destination not in target.parents:
                raise ValueError("release archive contains an unsafe path")
            if ":" in member.filename or member.filename.startswith(("/", "\\")):
                raise ValueError("release archive contains an invalid path")
            is_symlink = (member.external_attr >> 16) & 0o170000 == 0o120000
            if is_symlink:
                raise ValueError("release archive contains a symlink")
            if member.file_size > MAX_UNPACKED_BYTES or unpacked_bytes + member.file_size > MAX_UNPACKED_BYTES:
                raise ValueError("release archive expands too far")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, target.open("wb") as output:
                copied = 0
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    unpacked_bytes += len(chunk)
                    if unpacked_bytes > MAX_UNPACKED_BYTES or copied > member.file_size:
                        raise ValueError("release archive expands too far")
                    output.write(chunk)
                if copied != member.file_size:
                    raise ValueError("release archive file size mismatch")


def _acquire_lock(executable: Path) -> Path | None:
    install_key = hashlib.sha256(str(executable.resolve()).casefold().encode("utf-8")).hexdigest()[:16]
    lock = Path(tempfile.gettempdir()) / f"{LOCK_PREFIX}-{install_key}.lock"
    try:
        if lock.exists():
            try:
                owner = json.loads(lock.read_text(encoding="ascii"))
                owner_pid = int(owner.get("pid", 0))
                old = time.time() - lock.stat().st_mtime > 6 * 3600
                alive = _process_alive(owner_pid)
                if old or not alive:
                    lock.unlink(missing_ok=True)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                if time.time() - lock.stat().st_mtime > 6 * 3600:
                    lock.unlink(missing_ok=True)
        descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(descriptor, json.dumps({"pid": os.getpid(), "createdAt": int(time.time())}).encode("ascii"))
        os.close(descriptor)
        return lock
    except (FileExistsError, OSError):
        return None


def _schedule_replace(stage: Path, workspace: Path, install_dir: Path, executable: Path, lock: Path,
                      expected_version: str | None = None, port: int = 8000) -> None:
    """Replace a running Windows package, verify startup, and restore files on failure."""
    script = workspace / "apply-update.ps1"
    script.write_text(
        "param([string]$Stage, [string]$InstallDir, [string]$Executable, [string]$Workspace, [string]$Lock, [int]$ParentPid, [string]$ExpectedVersion, [int]$Port)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$updatedFiles = [System.Collections.Generic.List[string]]::new()\n"
        "$startedPid = $null\n"
        "$backup = Join-Path $Workspace 'backup'\n"
        "$logDir = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'DeepcoinScreen' } else { Join-Path $env:TEMP 'DeepcoinScreen' }\n"
        "New-Item -ItemType Directory -Path $logDir -Force -ErrorAction SilentlyContinue | Out-Null\n"
        "$log = Join-Path $logDir 'update.log'\n"
        "function Write-UpdateLog([string]$Message) { try { Add-Content -LiteralPath $log -Value ((Get-Date -Format o) + ' ' + $Message) } catch {} }\n"
        "Start-Sleep -Milliseconds 500\n"
        "try {\n"
        "  $parent = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue\n"
        "  if ($parent) { Wait-Process -Id $ParentPid -Timeout 120 -ErrorAction Stop }\n"
        "  $files = @(Get-ChildItem -LiteralPath $Stage -File -Recurse)\n"
        "  $stagedExecutable = Join-Path $Stage ([System.IO.Path]::GetFileName($Executable))\n"
        "  if (-not (Test-Path -LiteralPath $stagedExecutable)) { throw 'updated executable is missing from stage' }\n"
        "  if (-not (Test-Path -LiteralPath (Join-Path $Stage 'version.txt'))) { throw 'updated version file is missing' }\n"
        "  foreach ($source in $files) {\n"
        "    $relative = $source.FullName.Substring($Stage.TrimEnd('\\').Length).TrimStart('\\')\n"
        "    $target = Join-Path $InstallDir $relative\n"
        "    $saved = Join-Path $backup $relative\n"
        "    $temporary = $target + '.update-new'\n"
        "    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null\n"
        "    if (Test-Path -LiteralPath $target) { New-Item -ItemType Directory -Path (Split-Path -Parent $saved) -Force | Out-Null; Copy-Item -LiteralPath $target -Destination $saved -Force }\n"
        "    Copy-Item -LiteralPath $source.FullName -Destination $temporary -Force\n"
        "    if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $source.FullName -Algorithm SHA256).Hash) { throw ('staged copy verification failed: ' + $relative) }\n"
        "    Move-Item -LiteralPath $temporary -Destination $target -Force\n"
        "    $updatedFiles.Add($relative)\n"
        "  }\n"
        "  $process = Start-Process -FilePath $Executable -PassThru\n"
        "  $startedPid = $process.Id\n"
        "  $deadline = (Get-Date).AddSeconds(45)\n"
        "  $healthy = $false\n"
        "  while ((Get-Date) -lt $deadline) {\n"
        "    Start-Sleep -Seconds 1\n"
        "    try { $health = Invoke-RestMethod -Uri ('http://127.0.0.1:{0}/api/health' -f $Port) -TimeoutSec 2; if ($health.ok -and $health.exchange -eq 'Deepcoin' -and ((-not $ExpectedVersion) -or $health.version -eq $ExpectedVersion)) { $healthy = $true; break } } catch {}\n"
        "  }\n"
        "  if (-not $healthy) { throw 'updated application failed its local health check' }\n"
        "  Write-UpdateLog 'Update completed and new application passed health check.'\n"
        "} catch {\n"
        "  Write-UpdateLog ('Update failed; restoring previous package: ' + $_)\n"
        "  if ($startedPid) { Stop-Process -Id $startedPid -Force -ErrorAction SilentlyContinue }\n"
        "  for ($i = $updatedFiles.Count - 1; $i -ge 0; $i--) {\n"
        "    $relative = $updatedFiles[$i]\n"
        "    $target = Join-Path $InstallDir $relative\n"
        "    $saved = Join-Path $backup $relative\n"
        "    if (Test-Path -LiteralPath $saved) { Copy-Item -LiteralPath $saved -Destination $target -Force } else { Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue }\n"
        "  }\n"
        "  try { Start-Process -FilePath $Executable | Out-Null } catch { Write-UpdateLog ('Previous application restart failed: ' + $_) }\n"
        "} finally {\n"
        "  Remove-Item -LiteralPath $Lock -Force -ErrorAction SilentlyContinue\n"
        "  Remove-Item -LiteralPath $Workspace -Recurse -Force -ErrorAction SilentlyContinue\n"
        "}\n",
        encoding="ascii",
    )
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Stage",
            str(stage),
            "-InstallDir",
            str(install_dir),
            "-Executable",
            str(executable),
            "-Workspace",
            str(workspace),
            "-Lock",
            str(lock),
            "-ParentPid",
            str(os.getpid()),
            "-ExpectedVersion",
            str(expected_version or ""),
            "-Port",
            str(port),
        ],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )


def schedule_update_if_available(release: dict | None = None) -> bool:
    """Download and schedule a newer Windows release; failures leave the app usable."""
    if not getattr(sys, "frozen", False) or os.environ.get("DEEPCOIN_DISABLE_UPDATE"):
        return False

    executable = Path(sys.executable).resolve()
    install_dir = executable.parent
    lock = _acquire_lock(executable)
    if lock is None:
        return False
    workspace = Path(tempfile.mkdtemp(prefix="deepcoin-update-"))
    scheduled = False
    try:
        release = release or _latest_release()
        tag = str(release.get("tag_name", ""))
        if not VERSION_PATTERN.fullmatch(tag):
            _log_update(f"Ignored release with invalid version tag: {tag!r}")
            return False
        if _version(tag) <= _version(current_version()):
            return False
        assets = release.get("assets") or []
        asset = next(
            (item for item in assets if isinstance(item, dict) and item.get("name") == ASSET_NAME),
            None,
        )
        if not isinstance(asset, dict) or not asset.get("browser_download_url"):
            return False

        archive = workspace / ASSET_NAME
        stage = workspace / "stage"
        digest = _asset_digest(release, asset, tag)
        _download(
            str(asset["browser_download_url"]),
            archive,
            int(asset["size"]) if asset.get("size") is not None else None,
            digest,
        )
        stage.mkdir()
        _safe_extract(archive, stage)
        package_version = (stage / "version.txt").read_text(encoding="ascii").strip()
        if not VERSION_PATTERN.fullmatch(package_version) or _version(package_version) <= _version(current_version()):
            return False
        if _version(package_version) != _version(tag) or not (stage / executable.name).is_file():
            _log_update(f"Release package version or executable did not match tag {tag}.")
            return False
        try:
            port = int(os.environ.get("PORT", "8000"))
        except ValueError:
            port = 8000
        _schedule_replace(stage, workspace, install_dir, executable, lock, package_version, port)
        _log_update(f"Staged release {tag}; waiting for the application to close before replacement.")
        scheduled = True
        return True
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError, zipfile.BadZipFile) as error:
        _log_update(f"Update check failed: {error}")
        return False
    finally:
        if not scheduled:
            shutil.rmtree(workspace, ignore_errors=True)
            lock.unlink(missing_ok=True)
