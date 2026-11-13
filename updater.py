from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


REPOSITORY = "anzhang730-creator/deepcoin-contract-screener"
CURRENT_VERSION = "1.0.0"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ASSET_NAME = "DeepcoinScreen-Windows.zip"


def _version(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value or "")
    return tuple(int(number) for number in numbers) or (0,)


def _request_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "deepcoin-contract-screener",
        },
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "deepcoin-contract-screener"})
    with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def _schedule_replace(stage: Path, install_dir: Path, executable: Path) -> None:
    script = Path(tempfile.gettempdir()) / f"deepcoin-update-{os.getpid()}.cmd"
    script.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        f'robocopy "{stage}" "{install_dir}" /E /IS /IT /NFL /NDL /NJH /NJS /NP >nul\r\n'
        f'start "Deepcoin Screen" "{executable}"\r\n'
        f'rmdir /s /q "{stage}" >nul 2>nul\r\n'
        'del "%~f0"\r\n',
        encoding="ascii",
    )
    subprocess.Popen(
        ["cmd.exe", "/c", str(script)],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )


def schedule_update_if_available() -> bool:
    """Stage a newer Windows release and return True when restart was scheduled."""
    if not getattr(sys, "frozen", False) or os.environ.get("DEEPCOIN_DISABLE_UPDATE"):
        return False

    executable = Path(sys.executable).resolve()
    install_dir = executable.parent
    try:
        release = _request_json(RELEASES_URL)
        tag = str(release.get("tag_name", ""))
        if not tag or _version(tag) <= _version(CURRENT_VERSION):
            return False
        assets = release.get("assets") or []
        asset = next((item for item in assets if item.get("name") == ASSET_NAME), None)
        if not asset or not asset.get("browser_download_url"):
            return False

        workspace = Path(tempfile.mkdtemp(prefix="deepcoin-update-"))
        archive = workspace / ASSET_NAME
        stage = workspace / "stage"
        _download(str(asset["browser_download_url"]), archive)
        stage.mkdir()
        with zipfile.ZipFile(archive) as package:
            package.extractall(stage)
        if not (stage / executable.name).is_file():
            shutil.rmtree(workspace, ignore_errors=True)
            return False
        _schedule_replace(stage, install_dir, executable)
        return True
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError, zipfile.BadZipFile):
        return False
