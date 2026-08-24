# Updating (host installations)

`./update.sh` updates a host installation to the latest `origin/master`.

```bash
./update.sh          # update if origin/master differs
./update.sh --stop   # stop the service (unchanged legacy behavior)
```

Contract: the tracked root `VERSION` file is the update trigger; Git is the
transport. If remote `VERSION` equals local, nothing happens.

Behavior:

- Fetch/version/preflight run while the service keeps running.
- Preflight aborts (service untouched) on: dirty tracked/staged files, local
  commits not on `origin/master`, wrong branch/detached HEAD, protected paths
  becoming tracked, or untracked-file collisions.
- The service is stopped only after preflight passes; it restarts afterwards
  only if it was running before. Success = `GET /health` returns 200 within
  ~60s (`/ready` is intentionally not used; browser runtime starts lazily).
- If `pyproject.toml`/`poetry.lock` changed: `poetry install --sync`; a
  changed `poetry.lock` also runs `poetry run playwright install chromium`.
- On failure after the code switch (deps, start, health), the updater rolls
  back to the previous commit, restores dependencies, restarts the old
  version and re-checks `/health`. A failed rollback exits non-zero and
  points at the server log.

User-owned state — `.env`, `.env.local`, `config.conf`, `runtime/` — is never
tracked and never touched. The updater refuses to run when `origin/master`
would start tracking them or would overwrite colliding untracked files.

Rollback covers code and dependencies only. If a newer version migrated
persistent state (e.g., snapshot DB schema), rolling back cannot undo that;
back up `runtime/` before updating if in doubt.

Docker deployments are out of scope for this script (it refuses to run inside
containers): update with `git pull && docker compose up -d --build`.
