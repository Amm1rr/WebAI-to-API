# src/run.py
if __name__ == "__main__":
    try:
        from app.server import main

        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
