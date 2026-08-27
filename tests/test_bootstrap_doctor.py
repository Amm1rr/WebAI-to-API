import ast
import os
import sys
import subprocess
import shutil
import stat
import unittest
import tempfile
import json
import importlib.util
import pytest
from types import SimpleNamespace


def _load_bootstrap_module():
    path = os.path.abspath("scripts/bootstrap.py")
    spec = importlib.util.spec_from_file_location("bootstrap_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_doctor_module():
    path = os.path.abspath("scripts/doctor.py")
    spec = importlib.util.spec_from_file_location("doctor_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mock_playwright_checks(monkeypatch, chromium_result):
    doctor = _load_doctor_module()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 0, "ok\n", "")
        return chromium_result

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    return doctor, calls

class TestBootstrapDoctor(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for tests
        self.test_dir = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        
        # Paths to scripts (absolute)
        self.bootstrap_path = os.path.abspath("scripts/bootstrap.py")
        self.doctor_path = os.path.abspath("scripts/doctor.py")
        self.config_example_path = os.path.abspath("config.conf.example")
        self.env_example_path = os.path.abspath(".env.example")
        
        # Copy example config to temp dir for tests
        shutil.copyfile(self.config_example_path, os.path.join(self.test_dir, "config.conf.example"))
        shutil.copyfile(self.env_example_path, os.path.join(self.test_dir, ".env.example"))

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

        if os.name == "posix":
            for path in (
                "runtime",
                "runtime/auth",
                "runtime/cache",
                "runtime/conversations",
            ):
                self.assertEqual(
                    stat.S_IMODE(os.stat(os.path.join(self.test_dir, path)).st_mode),
                    0o700,
                )
            for path in ("config.conf", ".env"):
                self.assertEqual(
                    stat.S_IMODE(os.stat(os.path.join(self.test_dir, path)).st_mode),
                    0o600,
                )
        
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

        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(config_path).st_mode), 0o600)

    @pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics only")
    def test_bootstrap_hardens_existing_state_without_overwriting(self):
        config_path = os.path.join(self.test_dir, "config.conf")
        env_path = os.path.join(self.test_dir, ".env")
        with open(config_path, "w") as handle:
            handle.write("KEEP CONFIG\n")
        with open(env_path, "w") as handle:
            handle.write("KEEP ENV\n")

        runtime_paths = [
            os.path.join(self.test_dir, "runtime"),
            os.path.join(self.test_dir, "runtime", "auth"),
            os.path.join(self.test_dir, "runtime", "cache"),
            os.path.join(self.test_dir, "runtime", "conversations"),
        ]
        for path in runtime_paths:
            os.makedirs(path, exist_ok=True)
            os.chmod(path, 0o755)
        os.chmod(config_path, 0o644)
        os.chmod(env_path, 0o644)

        command = ["python", self.bootstrap_path, "--no-install"]
        first = subprocess.run(command, cwd=self.test_dir, capture_output=True, text=True)
        second = subprocess.run(command, cwd=self.test_dir, capture_output=True, text=True)

        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        with open(config_path) as handle:
            self.assertEqual(handle.read(), "KEEP CONFIG\n")
        with open(env_path) as handle:
            self.assertEqual(handle.read(), "KEEP ENV\n")
        for path in runtime_paths:
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(config_path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(env_path).st_mode), 0o600)

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

    def test_doctor_corrupt_json_reports_playwright_impact(self):
        # Case 1: valid [Gemini] config cookies + corrupt JSON -> FAIL,
        # message explains backend impact and recovery.
        subprocess.run(
            ["python", self.bootstrap_path, "--no-install"],
            cwd=self.test_dir, capture_output=True, text=True
        )
        config_path = os.path.join(self.test_dir, "config.conf")
        with open(config_path, "w") as f:
            f.write("[Gemini]\n__Secure-1PSID = psid_val\n__Secure-1PSIDTS = ts_val\n")
        json_path = os.path.join(self.test_dir, "runtime", "auth", "gemini.json")
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w") as f:
            f.write('{"cookies": [broken')

        res = subprocess.run(
            ["python", self.doctor_path],
            cwd=self.test_dir, capture_output=True, text=True
        )
        self.assertIn("FAIL", res.stdout)
        self.assertIn("Playwright authentication is broken", res.stdout)
        self.assertIn("WebAPI cookie authentication may still be unaffected", res.stdout)
        self.assertIn("Run: python verify_login.py", res.stdout)

        # Case 2: no usable auth + corrupt JSON -> same actionable FAIL.
        with open(config_path, "w") as f:
            f.write("[Gemini]\n")
        res = subprocess.run(
            ["python", self.doctor_path],
            cwd=self.test_dir, capture_output=True, text=True
        )
        self.assertIn("FAIL", res.stdout)
        self.assertIn("Playwright authentication is broken", res.stdout)
        self.assertIn("Run: python verify_login.py", res.stdout)


@pytest.mark.parametrize(
    ("version", "expected_ok"),
    [
        ((3, 10), False),
        ((3, 11), True),
        ((3, 12), True),
        ((3, 13), False),
    ],
)
def test_check_python_version_matches_pyproject_contract(monkeypatch, capsys, version, expected_ok):
    bootstrap = _load_bootstrap_module()
    monkeypatch.setattr(bootstrap.sys, "version_info", version + (0, 0, "final"))

    result = bootstrap.check_python_version()

    assert result is expected_ok
    if not expected_ok:
        captured = capsys.readouterr()
        assert "3.11 to <3.13 is required." in captured.err
        assert f"Current version is {version[0]}.{version[1]}." in captured.err


@pytest.mark.parametrize(
    ("version", "expected_ok"),
    [
        ((3, 10), False),
        ((3, 11), True),
        ((3, 12), True),
        ((3, 13), False),
    ],
)
def test_doctor_check_python_version_matches_pyproject_contract(monkeypatch, capsys, version, expected_ok):
    doctor = _load_doctor_module()
    monkeypatch.setattr(doctor.sys, "version_info", version + (0, 0, "final"))

    result = doctor.check_python_version()

    assert result is expected_ok
    captured = capsys.readouterr()
    if expected_ok:
        assert "PASS" in captured.out
        assert "is supported" in captured.out
    else:
        assert "FAIL" in captured.out
        assert f"Version {version[0]}.{version[1]} is unsupported" in captured.out
        assert "Python 3.11 to <3.13 is required" in captured.out
        assert "Run: poetry run python scripts/doctor.py" in captured.out


@pytest.mark.parametrize(
    ("version", "expected_ok"),
    [
        ((3, 11, 9), False),
        ((3, 11, 10), True),
        ((3, 12, 3), False),
        ((3, 12, 4), True),
    ],
)
def test_windows_python_patch_contract_is_shared_by_bootstrap_and_doctor(
    monkeypatch, capsys, version, expected_ok
):
    bootstrap = _load_bootstrap_module()
    doctor = _load_doctor_module()
    fake_sys = SimpleNamespace(
        version_info=version + (0, 0, "final"),
        platform="win32",
        stderr=sys.stderr,
    )
    monkeypatch.setattr(bootstrap, "sys", fake_sys)
    monkeypatch.setattr(doctor, "sys", fake_sys)

    assert bootstrap.check_python_version() is expected_ok
    assert doctor.check_python_version() is expected_ok

    captured = capsys.readouterr()
    if expected_ok:
        assert "PASS" in captured.out
    else:
        assert "FAIL" in captured.out
        assert "Windows Python 3.11.10+ or 3.12.4+" in captured.err
        assert "Windows Python 3.11.10+ or 3.12.4+" in captured.out


def test_posix_python_patch_contract_remains_major_minor_only(monkeypatch, capsys):
    bootstrap = _load_bootstrap_module()
    doctor = _load_doctor_module()
    fake_sys = SimpleNamespace(
        version_info=(3, 11, 0, 0, "final"),
        platform="linux",
    )
    monkeypatch.setattr(bootstrap, "sys", fake_sys)
    monkeypatch.setattr(doctor, "sys", fake_sys)

    assert bootstrap.check_python_version() is True
    assert doctor.check_python_version() is True
    assert "PASS" in capsys.readouterr().out


def test_check_playwright_found_path_uses_valid_script(monkeypatch, capsys):
    result = subprocess.CompletedProcess([], 0, "found\n", "")
    doctor, calls = _mock_playwright_checks(monkeypatch, result)

    assert doctor.check_playwright() is True
    ast.parse(calls[1][0][-1])
    assert "Chromium executable found" in capsys.readouterr().out


def test_check_playwright_missing_path_fails(monkeypatch, capsys):
    result = subprocess.CompletedProcess([], 0, "missing\n", "")
    doctor, _ = _mock_playwright_checks(monkeypatch, result)

    assert doctor.check_playwright() is False
    captured = capsys.readouterr().out
    assert "FAIL" in captured
    assert "Chromium executable is missing" in captured


@pytest.mark.parametrize("stdout", ["", "found\nextra\n", "unexpected\n"])
def test_check_playwright_unexpected_output_fails(monkeypatch, capsys, stdout):
    result = subprocess.CompletedProcess([], 0, stdout, "")
    doctor, _ = _mock_playwright_checks(monkeypatch, result)

    assert doctor.check_playwright() is False
    captured = capsys.readouterr().out
    assert "FAIL" in captured
    assert "unexpected output" in captured


def test_check_playwright_verification_process_failure_fails(monkeypatch, capsys):
    result = subprocess.CompletedProcess([], 1, "", "SyntaxError: invalid syntax")
    doctor, _ = _mock_playwright_checks(monkeypatch, result)

    assert doctor.check_playwright() is False
    captured = capsys.readouterr().out
    assert "FAIL" in captured
    assert "SyntaxError: invalid syntax" in captured


def test_check_playwright_arch_indeterminate_result_warns(monkeypatch, capsys):
    result = subprocess.CompletedProcess([], 0, "indeterminate: unsupported\n", "")
    doctor, _ = _mock_playwright_checks(monkeypatch, result)

    assert doctor.check_playwright(is_arch_based=True) is True
    captured = capsys.readouterr().out
    assert "WARN" in captured
    assert "Arch-based system" in captured


def test_check_playwright_arch_indeterminate_result_fails_elsewhere(monkeypatch, capsys):
    result = subprocess.CompletedProcess([], 0, "indeterminate: unsupported\n", "")
    doctor, _ = _mock_playwright_checks(monkeypatch, result)

    assert doctor.check_playwright(is_arch_based=False) is False
    assert "FAIL" in capsys.readouterr().out


def test_doctor_main_fails_when_chromium_check_fails(monkeypatch, capsys):
    doctor = _load_doctor_module()
    monkeypatch.setattr(doctor, "check_python_version", lambda: True)
    monkeypatch.setattr(doctor, "check_config", lambda: (True, object()))
    monkeypatch.setattr(doctor, "check_env", lambda: True)
    monkeypatch.setattr(doctor, "check_poetry", lambda: True)
    monkeypatch.setattr(doctor, "check_runtime_dirs", lambda: True)
    monkeypatch.setattr(doctor, "check_platform", lambda: False)
    monkeypatch.setattr(doctor, "check_playwright", lambda _is_arch_based: False)
    monkeypatch.setattr(doctor, "check_auth_material", lambda _config: True)
    monkeypatch.setattr(doctor, "check_port", lambda: True)
    monkeypatch.setattr(doctor, "check_exposure", lambda: True)

    with pytest.raises(SystemExit) as error:
        doctor.main()

    assert error.value.code == 1
    assert "DIAGNOSTICS FAILED" in capsys.readouterr().out

if __name__ == "__main__":
    unittest.main()
