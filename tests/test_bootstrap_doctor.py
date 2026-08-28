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


def _assert_chromium_probe_transport(calls):
    command, kwargs = calls[1]
    assert command == ["poetry", "run", "python", "-"]
    assert "-c" not in command
    assert "input" in kwargs
    ast.parse(kwargs["input"])

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
        self.assertIn("poetry run python scripts/doctor.py", res.stdout)
        self.assertIn("poetry run python verify_login.py", res.stdout)
        self.assertIn("poetry run python src/run.py", res.stdout)
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

    def test_bootstrap_uses_custom_runtime_path(self):
        runtime_dir = os.path.join(self.test_dir, "runtime with spaces")
        env = {**os.environ, "RUNTIME_DIR": runtime_dir}
        res = subprocess.run(
            ["python", self.bootstrap_path, "--no-install"],
            cwd=self.test_dir, capture_output=True, text=True, env=env,
        )

        self.assertEqual(res.returncode, 0)
        for relative_path in ("", "auth", "cache", "conversations"):
            self.assertTrue(os.path.isdir(os.path.join(runtime_dir, relative_path)))

    def test_bootstrap_creates_custom_docker_runtime_source(self):
        source = os.path.join(self.test_dir, "docker state with spaces")
        env = {**os.environ, "DOCKER_RUNTIME_DIR": source}

        res = subprocess.run(
            ["python", self.bootstrap_path, "--no-install"],
            cwd=self.test_dir, capture_output=True, text=True, env=env,
        )

        self.assertEqual(res.returncode, 0)
        self.assertTrue(os.path.isdir(source))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(source).st_mode), 0o700)

    def test_bootstrap_rejects_docker_runtime_source_file(self):
        source = os.path.join(self.test_dir, "docker source")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write("not a directory")

        res = subprocess.run(
            ["python", self.bootstrap_path, "--no-install"],
            cwd=self.test_dir,
            capture_output=True,
            text=True,
            env={**os.environ, "DOCKER_RUNTIME_DIR": source},
        )

        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Docker runtime source", res.stderr)

    @pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics only")
    def test_bootstrap_preserves_existing_external_docker_runtime_source_mode(self):
        source = os.path.join(self.test_dir, "external docker source")
        os.makedirs(source)
        os.chmod(source, 0o755)

        res = subprocess.run(
            ["python", self.bootstrap_path, "--no-install"],
            cwd=self.test_dir,
            capture_output=True,
            text=True,
            env={**os.environ, "DOCKER_RUNTIME_DIR": source},
        )

        self.assertEqual(res.returncode, 0)
        self.assertEqual(stat.S_IMODE(os.stat(source).st_mode), 0o755)

    def test_bootstrap_uses_custom_auth_path_without_default_auth_dir(self):
        runtime_dir = os.path.join(self.test_dir, "runtime")
        auth_dir = os.path.join(self.test_dir, "private auth")
        env = {
            **os.environ,
            "RUNTIME_DIR": runtime_dir,
            "AUTH_STATE_DIR": auth_dir,
        }
        res = subprocess.run(
            ["python", self.bootstrap_path, "--no-install"],
            cwd=self.test_dir, capture_output=True, text=True, env=env,
        )

        self.assertEqual(res.returncode, 0)
        self.assertTrue(os.path.isdir(auth_dir))
        self.assertFalse(os.path.exists(os.path.join(runtime_dir, "auth")))
        self.assertTrue(os.path.isdir(os.path.join(runtime_dir, "cache")))
        self.assertTrue(os.path.isdir(os.path.join(runtime_dir, "conversations")))

    def test_bootstrap_honors_explicit_config_auth_path(self):
        auth_dir = os.path.join(self.test_dir, "configured auth")
        with open(os.path.join(self.test_dir, "config.conf"), "w", encoding="utf-8") as handle:
            handle.write(f"[Playwright]\nauth_state_dir = {auth_dir}\n")

        res = subprocess.run(
            ["python", self.bootstrap_path, "--no-install"],
            cwd=self.test_dir, capture_output=True, text=True,
        )

        self.assertEqual(res.returncode, 0)
        self.assertTrue(os.path.isdir(auth_dir))
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, "runtime", "auth")))

    def test_bootstrap_creates_custom_conversation_parent_without_chmodding_existing_parent(self):
        runtime_dir = os.path.join(self.test_dir, "runtime")
        db_parent = os.path.join(self.test_dir, "external conversations")
        db_path = os.path.join(db_parent, "state.db")
        env = {
            **os.environ,
            "RUNTIME_DIR": runtime_dir,
            "CONVERSATION_SNAPSHOT_DB": db_path,
        }
        res = subprocess.run(
            ["python", self.bootstrap_path, "--no-install"],
            cwd=self.test_dir, capture_output=True, text=True, env=env,
        )

        self.assertEqual(res.returncode, 0)
        self.assertTrue(os.path.isdir(db_parent))
        self.assertFalse(os.path.exists(os.path.join(runtime_dir, "conversations")))

    @pytest.mark.skipif(os.name != "posix", reason="POSIX mode semantics only")
    def test_bootstrap_leaves_existing_custom_conversation_parent_mode_unchanged(self):
        db_parent = os.path.join(self.test_dir, "external conversations")
        os.makedirs(db_parent)
        os.chmod(db_parent, 0o755)
        env = {**os.environ, "CONVERSATION_SNAPSHOT_DB": os.path.join(db_parent, "state.db")}

        res = subprocess.run(
            ["python", self.bootstrap_path, "--no-install"],
            cwd=self.test_dir, capture_output=True, text=True, env=env,
        )

        self.assertEqual(res.returncode, 0)
        self.assertEqual(stat.S_IMODE(os.stat(db_parent).st_mode), 0o755)

    def test_bootstrap_skips_directory_for_memory_conversation_database(self):
        runtime_dir = os.path.join(self.test_dir, "runtime")
        env = {
            **os.environ,
            "RUNTIME_DIR": runtime_dir,
            "CONVERSATION_SNAPSHOT_DB": ":memory:",
        }
        res = subprocess.run(
            ["python", self.bootstrap_path, "--no-install"],
            cwd=self.test_dir, capture_output=True, text=True, env=env,
        )

        self.assertEqual(res.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(runtime_dir, "conversations")))

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

    def test_doctor_rejects_invalid_semantic_config_without_traceback(self):
        with open(os.path.join(self.test_dir, "config.conf"), "w", encoding="utf-8") as handle:
            handle.write("[Gemini]\nbackend = invalid\n")

        res = subprocess.run(
            ["python", self.doctor_path], cwd=self.test_dir, capture_output=True, text=True,
        )

        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Configuration", res.stdout)
        self.assertIn("Invalid Gemini backend configured", res.stdout)
        self.assertNotIn("Traceback", res.stdout)
        self.assertNotIn("Traceback", res.stderr)

    def test_doctor_rejects_malformed_config_without_traceback(self):
        with open(os.path.join(self.test_dir, "config.conf"), "w", encoding="utf-8") as handle:
            handle.write("backend = webapi\n")

        res = subprocess.run(
            ["python", self.doctor_path], cwd=self.test_dir, capture_output=True, text=True,
        )

        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Configuration", res.stdout)
        self.assertIn("FAIL", res.stdout)
        self.assertNotIn("Traceback", res.stdout)
        self.assertNotIn("Traceback", res.stderr)

    def test_doctor_rejects_invalid_startup_boolean(self):
        for section, option in (("EnabledAI", "gemini"), ("Logging", "disable_access_logs")):
            with open(os.path.join(self.test_dir, "config.conf"), "w", encoding="utf-8") as handle:
                handle.write(f"[{section}]\n{option} = maybe\n")

            res = subprocess.run(
                ["python", self.doctor_path], cwd=self.test_dir, capture_output=True, text=True,
            )

            self.assertNotEqual(res.returncode, 0)
            self.assertIn(f"Invalid boolean value for {section}.{option}", res.stdout)
            self.assertNotIn("Traceback", res.stdout)

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

        # Scenario A: [Gemini] section with only required canonical PSID
        with open(config_path, 'w') as f:
            f.write("[Gemini]\n__Secure-1PSID = psid_val\n")
        res = subprocess.run(["python", self.doctor_path], cwd=self.test_dir, capture_output=True, text=True)
        self.assertIn("PASS", res.stdout)
        self.assertIn("Gemini cookies found in [Gemini] configuration", res.stdout)

        # Scenario B: [Gemini] section with supported alias keys
        with open(config_path, 'w') as f:
            f.write("[Gemini]\ngemini_cookie_1psid = psid_val\ngemini_cookie_1psidts = ts_val\n")
        res = subprocess.run(["python", self.doctor_path], cwd=self.test_dir, capture_output=True, text=True)
        self.assertIn("PASS", res.stdout)
        self.assertIn("Gemini cookies found in [Gemini] configuration", res.stdout)

        # Scenario C: Legacy [Cookies] section with only required PSID
        with open(config_path, 'w') as f:
            f.write("[Cookies]\ngemini_cookie_1psid = psid_val\n")
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

    def test_doctor_checks_resolved_paths_without_creating_them(self):
        runtime_dir = os.path.join(self.test_dir, "runtime with spaces")
        auth_dir = os.path.join(self.test_dir, "private auth")
        config_path = os.path.join(self.test_dir, "config.conf")
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write("[Gemini]\n")
        env = {
            **os.environ,
            "RUNTIME_DIR": runtime_dir,
            "AUTH_STATE_DIR": auth_dir,
        }

        res = subprocess.run(
            ["python", self.doctor_path], cwd=self.test_dir, capture_output=True, text=True, env=env,
        )

        self.assertNotEqual(res.returncode, 0)
        self.assertIn(f"Runtime: {runtime_dir}", res.stdout)
        self.assertIn(f"Auth: {auth_dir}", res.stdout)
        self.assertIn(os.path.join(auth_dir, "gemini.json"), res.stdout)
        self.assertFalse(os.path.exists(runtime_dir))
        self.assertFalse(os.path.exists(auth_dir))

    def test_doctor_reports_docker_runtime_source_separately(self):
        source = os.path.join(self.test_dir, "docker source")
        os.makedirs(source)
        with open(os.path.join(self.test_dir, "config.conf"), "w", encoding="utf-8") as handle:
            handle.write("[Gemini]\n")

        res = subprocess.run(
            ["python", self.doctor_path],
            cwd=self.test_dir,
            capture_output=True,
            text=True,
            env={**os.environ, "DOCKER_RUNTIME_DIR": source},
        )

        self.assertIn("Docker Runtime", res.stdout)
        self.assertIn(f"{source} -> /app/runtime", res.stdout)

    def test_doctor_checks_resolved_auth_json_path(self):
        runtime_dir = os.path.join(self.test_dir, "runtime")
        auth_dir = os.path.join(self.test_dir, "private auth")
        os.makedirs(os.path.join(runtime_dir, "cache"))
        os.makedirs(os.path.join(runtime_dir, "conversations"))
        os.makedirs(auth_dir)
        json_path = os.path.join(auth_dir, "gemini.json")
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump({"cookies": []}, handle)
        with open(os.path.join(self.test_dir, "config.conf"), "w", encoding="utf-8") as handle:
            handle.write("[Gemini]\n")
        env = {
            **os.environ,
            "RUNTIME_DIR": runtime_dir,
            "AUTH_STATE_DIR": auth_dir,
        }

        res = subprocess.run(
            ["python", self.doctor_path], cwd=self.test_dir, capture_output=True, text=True, env=env,
        )

        self.assertIn("Auth (JSON)", res.stdout)
        self.assertIn(f"{json_path} exists and is valid", res.stdout)

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
        self.assertIn("Run: poetry run python verify_login.py", res.stdout)

        # Case 2: no usable auth + corrupt JSON -> same actionable FAIL.
        with open(config_path, "w") as f:
            f.write("[Gemini]\n")
        res = subprocess.run(
            ["python", self.doctor_path],
            cwd=self.test_dir, capture_output=True, text=True
        )
        self.assertIn("FAIL", res.stdout)
        self.assertIn("Playwright authentication is broken", res.stdout)
        self.assertIn("Run: poetry run python verify_login.py", res.stdout)


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
    ("version", "platform_name", "reason", "expected_ok", "required"),
    [
        ((3, 11, 10), "nt", "supported", True, "3.11.10+"),
        ((3, 12, 4), "nt", "supported", True, "3.12.4+"),
        ((3, 11, 9), "nt", "windows_patch_too_old", False, "3.11.10+"),
        ((3, 12, 3), "nt", "windows_patch_too_old", False, "3.12.4+"),
        ((3, 10, 99), "nt", "unsupported_major_minor", False, ">=3.11,<3.13"),
        ((3, 13, 0), "nt", "unsupported_major_minor", False, ">=3.11,<3.13"),
        ((3, 11, 0), "posix", "supported", True, ">=3.11,<3.13"),
        ((3, 12, 3), "posix", "supported", True, ">=3.11,<3.13"),
    ],
)
def test_python_version_classification_preserves_boolean_contract(
    version, platform_name, reason, expected_ok, required
):
    from app.utils.python_version import classify_python_version, is_supported_python

    result = classify_python_version(version, platform_name=platform_name)

    assert result["supported"] is expected_ok
    assert result["reason"] == reason
    assert result["version"] == ".".join(str(part) for part in version)
    assert result["supported_range"] == ">=3.11,<3.13"
    assert result["required"] == required
    assert is_supported_python(version, platform_name=platform_name) is expected_ok


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


def test_check_poetry_missing_fails(monkeypatch, capsys):
    doctor = _load_doctor_module()
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)

    assert doctor.check_poetry() is False
    captured = capsys.readouterr().out
    assert "Poetry" in captured
    assert "FAIL" in captured
    assert "not found" in captured


def test_check_poetry_success_passes(monkeypatch, capsys):
    doctor = _load_doctor_module()
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: "poetry")
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, "Poetry (version 2.3.4)\n", ""
        ),
    )

    assert doctor.check_poetry() is True
    captured = capsys.readouterr().out
    assert "PASS" in captured
    assert "Poetry (version 2.3.4)" in captured


def test_check_poetry_nonzero_fails_with_output_detail(monkeypatch, capsys):
    doctor = _load_doctor_module()
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: "poetry")
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 7, "partial output\n", "Poetry failed\n"
        ),
    )

    assert doctor.check_poetry() is False
    captured = capsys.readouterr().out
    assert "FAIL" in captured
    assert "exit code 7" in captured
    assert "stderr: Poetry failed" in captured
    assert "stdout: partial output" in captured


def test_check_poetry_exception_fails_without_traceback(monkeypatch, capsys):
    doctor = _load_doctor_module()
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: "poetry")

    def raise_error(*args, **kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr(doctor.subprocess, "run", raise_error)

    assert doctor.check_poetry() is False
    captured = capsys.readouterr().out
    assert "FAIL" in captured
    assert "PermissionError" in captured
    assert "access denied" in captured
    assert "Traceback" not in captured


def test_check_poetry_timeout_fails(monkeypatch, capsys):
    doctor = _load_doctor_module()
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: "poetry")

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            args[0], 5, output="partial output", stderr="still running"
        )

    monkeypatch.setattr(doctor.subprocess, "run", raise_timeout)

    assert doctor.check_poetry() is False
    captured = capsys.readouterr().out
    assert "FAIL" in captured
    assert "timed out after 5 seconds" in captured
    assert "stderr: still running" in captured
    assert "stdout: partial output" in captured


def test_doctor_main_fails_when_poetry_check_fails(monkeypatch, capsys):
    doctor = _load_doctor_module()
    monkeypatch.setattr(doctor, "check_python_version", lambda: True)
    monkeypatch.setattr(doctor, "check_config", lambda: (True, object()))
    monkeypatch.setattr(doctor, "check_env", lambda: True)
    monkeypatch.setattr(doctor, "check_poetry", lambda: False)
    monkeypatch.setattr(doctor, "check_runtime_dirs", lambda _config: True)
    monkeypatch.setattr(doctor, "check_platform", lambda: False)
    monkeypatch.setattr(doctor, "check_port", lambda: True)
    monkeypatch.setattr(doctor, "check_exposure", lambda: True)

    with pytest.raises(SystemExit) as error:
        doctor.main()

    assert error.value.code == 1
    assert "DIAGNOSTICS FAILED" in capsys.readouterr().out


def test_doctor_skips_config_dependent_checks_after_config_failure(monkeypatch):
    doctor = _load_doctor_module()
    calls = []
    monkeypatch.setattr(doctor, "check_python_version", lambda: True)
    monkeypatch.setattr(doctor, "check_config", lambda: (False, None))
    monkeypatch.setattr(doctor, "check_env", lambda: True)
    monkeypatch.setattr(doctor, "check_poetry", lambda: True)
    monkeypatch.setattr(doctor, "check_runtime_dirs", lambda _config: calls.append("dirs") or True)
    monkeypatch.setattr(doctor, "check_platform", lambda: False)
    monkeypatch.setattr(doctor, "check_playwright", lambda _is_arch_based: calls.append("playwright") or True)
    monkeypatch.setattr(doctor, "check_auth_material", lambda _config: calls.append("auth") or True)
    monkeypatch.setattr(doctor, "check_port", lambda: True)
    monkeypatch.setattr(doctor, "check_exposure", lambda: True)

    with pytest.raises(SystemExit) as error:
        doctor.main()

    assert error.value.code == 1
    assert calls == ["playwright"]


def test_check_playwright_found_path_uses_valid_script(monkeypatch, capsys):
    result = subprocess.CompletedProcess([], 0, "found\n", "")
    doctor, calls = _mock_playwright_checks(monkeypatch, result)

    assert doctor.check_playwright() is True
    _assert_chromium_probe_transport(calls)
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
    monkeypatch.setattr(doctor, "check_runtime_dirs", lambda _config: True)
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
