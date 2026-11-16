import os
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
UNINSTALL_SH = os.path.join(REPO_ROOT, "uninstall.sh")
INSTALL_SH = os.path.join(REPO_ROOT, "install.sh")


class TestUninstallScript(unittest.TestCase):
    def test_uninstall_help_option(self):
        result = subprocess.run(
            ["bash", UNINSTALL_SH, "--help"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("--purge", result.stdout)
        self.assertIn("--dry-run", result.stdout)

    def test_uninstall_short_help_option(self):
        result = subprocess.run(
            ["bash", UNINSTALL_SH, "-h"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)

    def test_uninstall_dry_run_option(self):
        result = subprocess.run(
            ["bash", UNINSTALL_SH, "--dry-run"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("DRY-RUN", result.stdout)

    def test_install_help_option(self):
        result = subprocess.run(
            ["bash", INSTALL_SH, "--help"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("--uninstall", result.stdout)

    def test_install_uninstall_help_delegation(self):
        result = subprocess.run(
            ["bash", INSTALL_SH, "--uninstall", "--help"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("--purge", result.stdout)

    def test_install_uninstall_dry_run_delegation(self):
        result = subprocess.run(
            ["bash", INSTALL_SH, "uninstall", "--dry-run"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("DRY-RUN", result.stdout)


if __name__ == "__main__":
    unittest.main()
