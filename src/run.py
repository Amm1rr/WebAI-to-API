# src/run.py
import sys

if __name__ == "__main__":
    from app.config_contract import StartupConfigError

    try:
        from app.server import main
    except StartupConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
