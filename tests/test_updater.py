import hashlib
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import updater


class UpdaterTests(unittest.TestCase):
    def test_version_comparison_handles_two_digit_minor_versions(self):
        self.assertGreater(updater._version("v1.10.0"), updater._version("v1.9.99"))

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

            def record_schedule(stage, workspace, _install_dir, _executable, lock):
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


if __name__ == "__main__":
    unittest.main()
