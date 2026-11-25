import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import updater


class UpdaterTests(unittest.TestCase):
    def test_check_for_update_returns_ui_summary_and_private_release(self):
        release = {"tag_name": "v1.2.0", "assets": [{"name": updater.ASSET_NAME}]}
        with patch.object(updater, "_latest_release", return_value=release), patch.object(
            updater, "current_version", return_value="1.1.0"
        ), patch.object(updater.sys, "frozen", True, create=True):
            result = updater.check_for_update()

        self.assertTrue(result["updateAvailable"])
        self.assertEqual(result["currentVersion"], "1.1.0")
        self.assertEqual(result["latestVersion"], "v1.2.0")
        self.assertIs(result["_release"], release)

    def test_version_comparison_handles_two_digit_minor_versions(self):
        self.assertGreater(updater._version("v1.10.0"), updater._version("v1.9.99"))
        self.assertEqual(updater._version("1.0.1"), updater._version("v1.0.1"))
        self.assertEqual(updater._version("release-1.2.3"), (0, 0, 0, 0))

    def test_checksum_manifest_requires_exact_archive_name(self):
        digest = "a" * 64
        self.assertEqual(
            updater._checksum_from_manifest(
                f"{digest} *DeepcoinScreen-Windows.zip\nother.txt\n".encode("ascii"),
                updater.ASSET_NAME,
            ),
            digest,
        )
        with self.assertRaises(ValueError):
            updater._checksum_from_manifest(
                f"{digest} *different.zip\n".encode("ascii"), updater.ASSET_NAME
            )

    def test_asset_digest_can_fall_back_to_release_checksum_asset(self):
        digest = "b" * 64
        release = {
            "tag_name": "v1.2.3",
            "assets": [{"name": updater.CHECKSUMS_NAME, "browser_download_url": "https://github.com/example/checksums"}],
        }
        with patch.object(updater, "_read_github_asset", return_value=f"{digest} *{updater.ASSET_NAME}\n".encode("ascii")):
            self.assertEqual(
                updater._asset_digest(release, {"name": updater.ASSET_NAME}, "v1.2.3"),
                digest,
            )

    def test_download_refuses_missing_digest_and_non_github_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "release.zip"
            with self.assertRaises(ValueError):
                updater._download("https://github.com/example/release.zip", destination, None, None)
            with self.assertRaises(ValueError):
                updater._download("https://example.invalid/release.zip", destination, None, "a" * 64)

    def test_update_lock_is_scoped_to_executable_and_released(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "one" / "DeepcoinScreen.exe"
            second = Path(temporary) / "two" / "DeepcoinScreen.exe"
            first.parent.mkdir()
            second.parent.mkdir()
            first_lock = updater._acquire_lock(first)
            second_lock = updater._acquire_lock(second)
            try:
                self.assertIsNotNone(first_lock)
                self.assertIsNotNone(second_lock)
                self.assertIsNone(updater._acquire_lock(first))
            finally:
                if first_lock:
                    first_lock.unlink(missing_ok=True)
                if second_lock:
                    second_lock.unlink(missing_ok=True)

    def test_process_alive_check_does_not_change_exit_status(self):
        self.assertTrue(updater._process_alive(os.getpid()))
        self.assertFalse(updater._process_alive(-1))

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell update script")
    def test_generated_replacement_script_has_valid_powershell_syntax(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "stage"
            workspace = root / "workspace"
            install_dir = root / "install"
            executable = install_dir / "DeepcoinScreen.exe"
            lock = root / "update.lock"
            workspace.mkdir()
            with patch.object(updater.subprocess, "Popen"):
                updater._schedule_replace(stage, workspace, install_dir, executable, lock)

            parser = root / "parse-powershell.ps1"
            parser.write_text(
                "$tokens = $null\n"
                "$errors = $null\n"
                "[System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$tokens, [ref]$errors) | Out-Null\n"
                "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_.Message }; exit 1 }\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-File", str(parser), str(workspace / "apply-update.ps1")],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_safe_extract_rejects_parent_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("../escape.txt", "blocked")
            with self.assertRaises(ValueError):
                updater._safe_extract(archive, root / "out")

    def test_new_release_is_staged_after_version_and_hash_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "release.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("DeepcoinScreen.exe", b"new executable")
                package.writestr("version.txt", "v1.0.1\n")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            executable = root / "DeepcoinScreen.exe"
            executable.write_bytes(b"old executable")
            (root / "version.txt").write_text("1.0.0\n", encoding="ascii")
            scheduled = []

            def copy_archive(_url, destination, _size, _digest):
                shutil.copyfile(archive, destination)

            def record_schedule(stage, workspace, _install_dir, _executable, lock, *_update_args):
                scheduled.append((stage, workspace, lock))

            with patch.object(updater.sys, "frozen", True, create=True), patch.object(
                updater.sys, "executable", str(executable)
            ), patch.object(
                updater,
                "_request_json",
                return_value={
                    "tag_name": "v1.0.1",
                    "assets": [
                        {
                            "name": updater.ASSET_NAME,
                            "browser_download_url": "https://example.invalid/release.zip",
                            "size": archive.stat().st_size,
                            "digest": f"sha256:{digest}",
                        }
                    ],
                },
            ), patch.object(updater, "_download", side_effect=copy_archive), patch.object(
                updater, "_schedule_replace", side_effect=record_schedule
            ):
                self.assertTrue(updater.schedule_update_if_available())

            self.assertEqual(len(scheduled), 1)
            stage, workspace, lock = scheduled[0]
            self.assertEqual((stage / "version.txt").read_text(encoding="ascii").strip(), "v1.0.1")
            self.assertEqual((stage / "DeepcoinScreen.exe").read_bytes(), b"new executable")
            shutil.rmtree(workspace, ignore_errors=True)
            lock.unlink(missing_ok=True)

    def test_malformed_release_assets_are_ignored_without_raising(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "DeepcoinScreen.exe"
            executable.write_bytes(b"old executable")
            with patch.object(updater.sys, "frozen", True, create=True), patch.object(
                updater.sys, "executable", str(executable)
            ), patch.object(
                updater, "_request_json", return_value={"tag_name": "v1.0.1", "assets": [None, "bad"]}
            ):
                self.assertFalse(updater.schedule_update_if_available())

    def test_replacement_script_contains_expected_port_and_version_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            with patch.object(updater.subprocess, "Popen"):
                updater._schedule_replace(
                    root / "stage", workspace, root / "install", root / "install" / "DeepcoinScreen.exe",
                    root / "update.lock", "v1.2.3", 18888,
                )
            script = (workspace / "apply-update.ps1").read_text(encoding="ascii")

        self.assertIn("ExpectedVersion", script)
        self.assertIn("-f $Port", script)
        self.assertIn("$health.version -eq $ExpectedVersion", script)


if __name__ == "__main__":
    unittest.main()
