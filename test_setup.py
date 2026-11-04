"""Installer regression checks; use temporary application data only."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import setup_launcher as setup


class SetupTests(unittest.TestCase):
    def test_shortcut_uses_powershell_and_redirected_desktop(self):
        def run(command, **kwargs):
            script = Path(command[-1]).read_text(encoding="utf-8-sig")
            self.assertIn("GetFolderPath('DesktopDirectory')", script)
            self.assertIn("O''Brien", script)
            self.assertTrue(command[0].endswith("powershell.exe"))
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(setup, "INSTALL_ROOT", Path("C:/Users/O'Brien/MemberPay")), patch.object(setup.subprocess, "run", side_effect=run):
            setup.create_shortcut()

    def test_shortcut_failure_keeps_actual_error(self):
        with patch.object(setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "Access denied")):
            with self.assertRaisesRegex(RuntimeError, "Access denied"):
                setup.create_shortcut()

    def test_install_preserves_data_and_updates_program(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, target = root / "payload", root / "installed"
            source.mkdir()
            target.mkdir()
            for name in ("memberpay.db", "password.txt", "MemberPay.exe"):
                (source / name).write_text("new")
                (target / name).write_text("existing")
            (target / "backups").mkdir()
            (target / "backups" / "saved.db").write_text("backup")
            with patch.object(setup, "INSTALL_ROOT", target), patch.object(setup, "bundle_root", return_value=source), patch.object(setup, "legacy_root", return_value=None), patch.object(setup, "create_shortcut"):
                setup.install()
            self.assertEqual((target / "memberpay.db").read_text(), "existing")
            self.assertEqual((target / "password.txt").read_text(), "existing")
            self.assertEqual((target / "MemberPay.exe").read_text(), "new")
            self.assertEqual((target / "backups" / "saved.db").read_text(), "backup")

    def test_worker_passes_error_without_tk_calls(self):
        import queue
        worker = object.__new__(setup.Installer)
        worker.results = queue.Queue()
        with tempfile.TemporaryDirectory() as folder, patch.object(setup, "INSTALL_ROOT", Path(folder)), patch.object(setup, "install", side_effect=RuntimeError("specific failure")):
            worker._install_worker()
            self.assertIn("specific failure", worker.results.get_nowait())
            self.assertIn("specific failure", (Path(folder) / "setup-error.log").read_text())


if __name__ == "__main__":
    unittest.main()
