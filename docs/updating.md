# Updating (host installations)

`./update-linux-macos.sh` updates a Linux/macOS host installation to the latest
`origin/master`. Windows CMD uses `update-windows.cmd`.

```bash
./update-linux-macos.sh          # update if origin/master differs
./update-linux-macos.sh --stop   # stop the service (unchanged legacy behavior)
```

```cmd
update-windows.cmd --stop
```

From Windows PowerShell, use `.\update-windows.cmd`.

Contract: the `[project].version` field in the tracked `pyproject.toml` is
the update trigger; Git is the transport. If the remote version equals the
local version, nothing happens.

> On macOS, ensure `python3` resolves to Python 3.11 or 3.12 before running
> the updater (e.g., via Homebrew or pyenv). Official multi-platform support
> status is pending validation.

## Version bump discipline

**Every deploy-worthy merge to `master` must bump `[project].version`.**
Otherwise the local and remote versions match, the updater intentionally
reports "already up to date", and newer code is not applied. There is no CI
enforcement yet — reviewers should verify the bump.

A pure version bump does NOT force a dependency sync. The updater compares
the dependency-bearing configuration (`requires-python`, `dependencies`,
`optional-dependencies`, `dependency-groups`, Poetry dependency tables)
between HEAD and origin/master and runs `poetry install --sync` only when
that signature or `poetry.lock` changed; a changed lock also runs
`poetry run playwright install chromium`.

Behavior:

- Fetch/version/preflight run while the service keeps running.
- Preflight aborts (service untouched) on: dirty tracked/staged files, local
  commits not on `origin/master`, wrong branch/detached HEAD, protected paths
  becoming tracked, or local untracked/ignored files colliding with paths
  origin/master tracks.
- The service stops only after preflight passes; it restarts afterwards only
  if it was running before. Success = `GET /health` returns 200 within ~60s.
  `/ready` is intentionally not used (browser runtime starts lazily).
- On failure after the code switch (deps, start, health), the updater rolls
  back to the previous commit, restores dependencies when needed, restarts
  the old version and re-checks `/health`. A failed rollback exits non-zero,
  leaves the service stopped where restoration itself failed, and points at
  the server log.

User-owned state — `.env`, `.env.local`, `config.conf`, `runtime/` — is never
tracked and never touched. Symlinks and untracked/ignored files that would
collide with newly tracked upstream paths block the update instead of being
overwritten.

Rollback covers code and dependencies only. If a newer version migrated
persistent state (e.g., snapshot DB schema), rolling back cannot undo that;
back up `runtime/` before updating if in doubt.

Docker deployments are out of scope for this script (only `--stop` works
inside containers): update with `git pull && docker compose up -d --build`.
