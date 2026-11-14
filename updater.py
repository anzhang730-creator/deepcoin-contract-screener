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
import urllib.request
import zipfile
from pathlib import Path


REPOSITORY = os.environ.get(
    "DEEPCOIN_UPDATE_REPOSITORY",
    "anzhang730-creator/deepcoin-contract-screener",
)
CURRENT_VERSION = "1.0.0"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ASSET_NAME = "DeepcoinScreen-Windows.zip"
MAX_ARCHIVE_BYTES = 250 * 1024 * 1024
MAX_UNPACKED_BYTES = 500 * 1024 * 1024
LOCK_NAME = "deepcoin-screener-update.lock"


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
    """Normalize v1, v1.2 and v1.2.3 tags for numeric comparison."""
    match = re.search(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[.-](\d+))?", value or "", re.I)
    if not match:
        return (0, 0, 0, 0)
    return tuple(int(part or 0) for part in match.groups())


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


def _download(url: str, destination: Path, expected_size: int | None, expected_digest: str | None) -> None:
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
    if expected_digest:
        normalized = expected_digest.removeprefix("sha256:").lower()
        if normalized != digest.hexdigest().lower():
            raise ValueError("release archive hash mismatch")


def _safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    unpacked_bytes = 0
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            target = (destination / member.filename).resolve()
            if target != destination and destination not in target.parents:
                raise ValueError("release archive contains an unsafe path")
            is_symlink = (member.external_attr >> 16) & 0o170000 == 0o120000
            if is_symlink:
                raise ValueError("release archive contains a symlink")
            unpacked_bytes += member.file_size
            if unpacked_bytes > MAX_UNPACKED_BYTES:
                raise ValueError("release archive expands too far")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _acquire_lock() -> Path | None:
    lock = Path(tempfile.gettempdir()) / LOCK_NAME
    try:
        if lock.exists() and (time.time() - lock.stat().st_mtime) > 3600:
            lock.unlink()
        descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
        return lock
    except (FileExistsError, OSError):
        return None


def _schedule_replace(stage: Path, workspace: Path, install_dir: Path, executable: Path, lock: Path) -> None:
    """Run the replacement after this process exits, then launch the new executable."""
    script = workspace / "apply-update.ps1"
    script.write_text(
        "param([string]$Stage, [string]$InstallDir, [string]$Executable, [string]$Workspace, [string]$Lock)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "Start-Sleep -Seconds 2\n"
        "try {\n"
        "  & robocopy $Stage $InstallDir /E /COPY:DAT /DCOPY:DAT /IS /IT /R:3 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null\n"
        "  if ($LASTEXITCODE -gt 7) { throw ('robocopy failed: ' + $LASTEXITCODE) }\n"
        "  if (-not (Test-Path -LiteralPath $Executable)) { throw 'updated executable is missing' }\n"
        "} catch {\n"
        "  Add-Content -LiteralPath (Join-Path $env:TEMP 'deepcoin-screener-update.log') -Value $_\n"
        "} finally {\n"
        "  Remove-Item -LiteralPath $Lock -Force -ErrorAction SilentlyContinue\n"
        "  Remove-Item -LiteralPath $Workspace -Recurse -Force -ErrorAction SilentlyContinue\n"
        "  Start-Process -FilePath $Executable\n"
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
        ],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )


def schedule_update_if_available() -> bool:
    """Download and schedule a newer Windows release; failures leave the app usable."""
    if not getattr(sys, "frozen", False) or os.environ.get("DEEPCOIN_DISABLE_UPDATE"):
        return False

    executable = Path(sys.executable).resolve()
    install_dir = executable.parent
    lock = _acquire_lock()
    if lock is None:
        return False
    workspace = Path(tempfile.mkdtemp(prefix="deepcoin-update-"))
    scheduled = False
    try:
        release = _request_json(RELEASES_URL)
        tag = str(release.get("tag_name", ""))
        if not tag or _version(tag) <= _version(current_version()):
            return False
        assets = release.get("assets") or []
        asset = next((item for item in assets if item.get("name") == ASSET_NAME), None)
        if not isinstance(asset, dict) or not asset.get("browser_download_url"):
            return False

        archive = workspace / ASSET_NAME
        stage = workspace / "stage"
        _download(
            str(asset["browser_download_url"]),
            archive,
            int(asset["size"]) if asset.get("size") is not None else None,
            str(asset.get("digest")) if asset.get("digest") else None,
        )
        stage.mkdir()
        _safe_extract(archive, stage)
        package_version = (stage / "version.txt").read_text(encoding="ascii").strip()
        if _version(package_version) <= _version(current_version()):
            return False
        if _version(package_version) != _version(tag) or not (stage / executable.name).is_file():
            return False
        _schedule_replace(stage, workspace, install_dir, executable, lock)
        scheduled = True
        return True
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError, zipfile.BadZipFile):
        return False
    finally:
        if not scheduled:
            shutil.rmtree(workspace, ignore_errors=True)
            lock.unlink(missing_ok=True)
