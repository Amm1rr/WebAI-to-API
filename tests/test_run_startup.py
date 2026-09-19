import runpy
import subprocess
import sys
from pathlib import Path

import pytest

RUN_PY = Path(__file__).resolve().parents[1] / "src" / "run.py"


def _run_startup_subprocess(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RUN_PY)],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_invalid_boolean_config_exits_cleanly(tmp_path):
    (tmp_path / "config.conf").write_text(
        "[Logging]\ndisable_access_logs = enable\n", encoding="utf-8"
    )

    res = _run_startup_subprocess(tmp_path)

    assert res.returncode == 1
    assert "Configuration error" in res.stderr
    assert "Invalid boolean value for Logging.disable_access_logs" in res.stderr
    assert "Traceback" not in res.stderr
    assert "Traceback" not in res.stdout


def test_malformed_config_exits_cleanly(tmp_path):
    (tmp_path / "config.conf").write_text("backend = webapi\n", encoding="utf-8")

    res = _run_startup_subprocess(tmp_path)

    assert res.returncode == 1
    assert "Configuration error" in res.stderr
    assert "Traceback" not in res.stderr
    assert "Traceback" not in res.stdout


def test_runtime_startup_config_error_is_not_converted(
    monkeypatch, tmp_path, capsys
):
    import app.server as server_mod
    from app.config_contract import StartupConfigError

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        server_mod,
        "main",
        lambda: (_ for _ in ()).throw(StartupConfigError("late failure")),
    )

    with pytest.raises(StartupConfigError, match="late failure"):
        runpy.run_path(str(RUN_PY), run_name="__main__")

    captured = capsys.readouterr()
    assert "Configuration error" not in captured.err
    assert "Configuration error" not in captured.out


def test_import_time_value_error_is_not_converted(monkeypatch, tmp_path, capsys):
    import builtins

    monkeypatch.chdir(tmp_path)
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "app.server" or name.startswith("app.server."):
            raise ValueError("import bug")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ValueError, match="import bug"):
        runpy.run_path(str(RUN_PY), run_name="__main__")

    captured = capsys.readouterr()
    assert "Configuration error" not in captured.err
    assert "Configuration error" not in captured.out


def test_unexpected_server_error_is_not_converted(monkeypatch, tmp_path, capsys):
    import app.server as server_mod

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        server_mod, "main", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError, match="boom"):
        runpy.run_path(str(RUN_PY), run_name="__main__")

    captured = capsys.readouterr()
    assert "Configuration error" not in captured.err
    assert "Configuration error" not in captured.out


def test_plain_value_error_from_server_is_not_converted(
    monkeypatch, tmp_path, capsys
):
    import app.server as server_mod

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        server_mod, "main", lambda: (_ for _ in ()).throw(ValueError("unrelated bug"))
    )

    with pytest.raises(ValueError, match="unrelated bug"):
        runpy.run_path(str(RUN_PY), run_name="__main__")

    captured = capsys.readouterr()
    assert "Configuration error" not in captured.err
    assert "Configuration error" not in captured.out


def test_keyboard_interrupt_exits_130(monkeypatch, tmp_path):
    import app.server as server_mod

    monkeypatch.chdir(tmp_path)

    def _raise():
        raise KeyboardInterrupt

    monkeypatch.setattr(server_mod, "main", _raise)

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(RUN_PY), run_name="__main__")

    assert excinfo.value.code == 130
