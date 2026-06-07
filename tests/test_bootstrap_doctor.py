import os
import subprocess
import shutil
import unittest
import tempfile
import json
from pathlib import Path

class TestBootstrapDoctor(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for tests
        self.test_dir = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        
        # Paths to scripts (absolute)
        self.bootstrap_path = os.path.abspath("scripts/bootstrap.py")
        self.doctor_path = os.path.abspath("scripts/doctor.py")
        self.config_example_path = os.path.abspath("config.conf.example")
        
        # Copy example config to temp dir for tests
        shutil.copyfile(self.config_example_path, os.path.join(self.test_dir, "config.conf.example"))

    def tearDown(self):
        # Remove the temporary directory
        shutil.rmtree(self.test_dir)

    def test_bootstrap_dry_run(self):
        # Run bootstrap --check --no-install in temp dir
        res = subprocess.run(
            ["python", self.bootstrap_path, "--check", "--no-install"], 
            cwd=self.test_dir,
            capture_output=True, 
            text=True
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("[DRY-RUN] Would create directory: runtime", res.stdout)
        self.assertIn("[DRY-RUN] Would create config.conf from config.conf.example", res.stdout)
        
        # Verify no changes made
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, "config.conf")))
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, "runtime")))

    def test_bootstrap_no_install(self):
        # Run bootstrap --no-install in temp dir
        res = subprocess.run(
            ["python", self.bootstrap_path, "--no-install"], 
            cwd=self.test_dir,
            capture_output=True, 
            text=True
        )
        self.assertEqual(res.returncode, 0)
        
        # Verify changes made
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "config.conf")))
        self.assertTrue(os.path.isdir(os.path.join(self.test_dir, "runtime")))
        self.assertTrue(os.path.isdir(os.path.join(self.test_dir, "runtime", "auth")))
        
        # Verify config content matches example
        with open(os.path.join(self.test_dir, "config.conf"), 'r') as f:
            content = f.read()
            self.assertIn("[Gemini]", content)

    def test_bootstrap_no_overwrite(self):
        # Create dummy config in temp dir
        config_path = os.path.join(self.test_dir, "config.conf")
        with open(config_path, 'w') as f:
            f.write("DUMMY")
        
        res = subprocess.run(
            ["python", self.bootstrap_path, "--no-install"], 
            cwd=self.test_dir,
            capture_output=True, 
            text=True
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("config.conf already exists. Skipping.", res.stdout)
        
        with open(config_path, 'r') as f:
            self.assertEqual(f.read(), "DUMMY")

    def test_doctor_fail_no_config(self):
        # Ensure no config in temp dir
        res = subprocess.run(
            ["python", self.doctor_path], 
            cwd=self.test_dir,
            capture_output=True, 
            text=True
        )
        # Should fail because config.conf is missing
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("FAIL", res.stdout)
        self.assertIn("config.conf is missing", res.stdout)

    def test_doctor_report_after_bootstrap(self):
        # Run bootstrap first in temp dir
        subprocess.run(
            ["python", self.bootstrap_path, "--no-install"], 
            cwd=self.test_dir,
            capture_output=True, 
            text=True
        )
        
        # Run doctor
        res = subprocess.run(
            ["python", self.doctor_path], 
            cwd=self.test_dir,
            capture_output=True, 
            text=True
        )
        
        # It should PASS on Config and Directories.
        self.assertIn("Configuration", res.stdout)
        self.assertIn("Directories", res.stdout)
        self.assertIn("PASS", res.stdout)

    def test_doctor_auth_scenarios(self):
        # Run bootstrap to get dirs
        subprocess.run(["python", self.bootstrap_path, "--no-install"], cwd=self.test_dir, capture_output=True)
        config_path = os.path.join(self.test_dir, "config.conf")
        auth_json_path = os.path.join(self.test_dir, "runtime", "auth", "gemini.json")

        # Scenario A: [Gemini] section with canonical keys
        with open(config_path, 'w') as f:
            f.write("[Gemini]\n__Secure-1PSID = psid_val\n__Secure-1PSIDTS = ts_val\n")
        res = subprocess.run(["python", self.doctor_path], cwd=self.test_dir, capture_output=True, text=True)
        self.assertIn("PASS", res.stdout)
        self.assertIn("Gemini cookies found in [Gemini] configuration", res.stdout)

        # Scenario B: [Gemini] section with supported alias keys
        with open(config_path, 'w') as f:
            f.write("[Gemini]\ngemini_cookie_1psid = psid_val\ngemini_cookie_1psidts = ts_val\n")
        res = subprocess.run(["python", self.doctor_path], cwd=self.test_dir, capture_output=True, text=True)
        self.assertIn("PASS", res.stdout)
        self.assertIn("Gemini cookies found in [Gemini] configuration", res.stdout)

        # Scenario C: Legacy [Cookies] section
        with open(config_path, 'w') as f:
            f.write("[Cookies]\ngemini_cookie_1psid = psid_val\ngemini_cookie_1psidts = ts_val\n")
        res = subprocess.run(["python", self.doctor_path], cwd=self.test_dir, capture_output=True, text=True)
        self.assertIn("WARN", res.stdout)
        self.assertIn("Using legacy [Cookies] configuration (supported but deprecated)", res.stdout)

        # Scenario D: JSON-only auth
        with open(config_path, 'w') as f:
            f.write("[Gemini]\n") # No cookies
        os.makedirs(os.path.dirname(auth_json_path), exist_ok=True)
        with open(auth_json_path, 'w') as f:
            json.dump({"cookies": [{"name": "__Secure-1PSID", "value": "val"}]}, f)
        res = subprocess.run(["python", self.doctor_path], cwd=self.test_dir, capture_output=True, text=True)
        self.assertIn("WARN", res.stdout)
        self.assertIn("No Gemini cookies configured; runtime/auth/gemini.json will be used", res.stdout)

        # Scenario E: No auth
        if os.path.exists(auth_json_path): os.remove(auth_json_path)
        res = subprocess.run(["python", self.doctor_path], cwd=self.test_dir, capture_output=True, text=True)
        self.assertIn("WARN", res.stdout)
        self.assertIn("No Gemini auth material found", res.stdout)

if __name__ == "__main__":
    unittest.main()
