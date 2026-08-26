# Updating (host installations)

The updater installs a newer remote `[project].version` from `origin/master`.
A newer commit without a version bump is intentionally not installed.

By default, host server startup performs a separate, notification-only update
check after Uvicorn has started. It fetches `origin/master`, compares
`[project].version`, and logs a result within 10 seconds. It never updates,
stops the service, or installs dependencies. Disable it with:

```ini
[General]
check_updates = false
```

Docker skips the startup check. Host update commands remain platform-specific.

```bash
./update-linux-macos.sh
./update-linux-macos.sh --stop
```

```cmd
update-windows.cmd
update-windows.cmd --stop
```

From Windows PowerShell:

```powershell
.\update-windows.cmd
.\update-windows.cmd --stop
```

`origin/master` is the Git source and transport. The `[project].version` field
in `pyproject.toml` is the update trigger. If remote and local versions match,
the updater intentionally does nothing.

> On macOS, ensure `python3` resolves to Python 3.11 or 3.12 before running
> the updater (e.g., via Homebrew or pyenv). Hosted cross-platform updater
> validation covers Linux, macOS, and Windows.

## Version bump discipline

**Every deploy-worthy merge to `master` must bump `[project].version`.**
Otherwise the local and remote versions match, the updater intentionally
reports "already up to date", and newer code is not applied. There is no CI
enforcement yet — reviewers should verify the bump.

## Preflight

Before stopping the service or changing the worktree, the updater verifies:

- The current directory is a Git repository on branch `master`.
- The `origin` remote exists and `git fetch origin master` succeeds.
- Remote `[project].version` is readable.
- Local `HEAD` is an ancestor of `origin/master`.
- No tracked or staged changes exist.
- Protected user paths are not remotely tracked: `.env`, `.env.local`,
  `config.conf`, and `runtime/`.
- No untracked or ignored local paths collide with paths tracked by
  `origin/master`.

A pure version bump does NOT force a dependency sync. The updater compares
the dependency-bearing configuration (`requires-python`, `dependencies`,
`optional-dependencies`, `dependency-groups`, Poetry dependency tables)
between HEAD and origin/master and runs `poetry install --sync` only when
that signature or `poetry.lock` changed; a changed lock also runs
`poetry run playwright install chromium`.

## Updater locking

One shared lock file serializes explicit updates, explicit `--stop`, and the
startup update-availability check. The startup check is opportunistic: it
acquires the lock non-blocking and skips silently (debug log) when something
else already owns it. Explicit commands instead wait for the shared lock
briefly — long enough to cover the startup check's bounded window plus its
subprocess-cleanup allowance and a small scheduler margin. If the shared lock
remains occupied beyond that explicit wait window (for example, a full update
is running), the command fails nonzero and reports that the requested action
was not performed. A lifecycle or update action is never reported as
successful when it did not run.

Behavior:

- Fetch/version/preflight run while the service keeps running.
- The service state is preserved: running before update means restarted after
  successful update; stopped before update means it remains stopped.
- Health validation runs only when the updater restarts a service that was
  running before the update. The default endpoint is
  `http://127.0.0.1:6969/health`, with a 60-second timeout and 2-second poll
  interval. `/ready` is intentionally not used.
- On failure after the code switch (deps, start, health), the updater rolls
  back to the previous SHA, restores dependencies when needed, restarts the
  old version only if it was running before the update, and re-checks health.
  A failed rollback exits non-zero, leaves the service stopped where
  restoration itself failed, and points at the server log.

User-owned state — `.env`, `.env.local`, `config.conf`, `runtime/` — is never
tracked and never touched. Symlinks and untracked/ignored files that would
collide with newly tracked upstream paths block the update instead of being
overwritten.

Rollback covers code and dependencies only. If a newer version migrated
persistent state (e.g., snapshot DB schema), rolling back cannot undo that;
back up `runtime/` before updating if in doubt.

## Platform stop behavior

Linux/macOS sends graceful POSIX termination first, waits within a bounded grace
window, and force-kills only as fallback.

Windows uses graceful loopback shutdown IPC through
`runtime/shutdown-control.json`. Metadata contains port, token, and the actual
server PID; fresh metadata is adopted before post-start health validation, with
token-authenticated listener identity required when adoption changes PID
authority.
When resolving an existing service, a live metadata PID repairs a stale
launcher PID in the updater PID file before stop or update decisions.
PID-less legacy metadata remains usable for graceful shutdown.
The updater retries within a bounded approximately 10-second liveness budget
and waits for accepted shutdown to reach actual process exit. Force termination
is only a fallback; Windows graceful shutdown does not use POSIX signals.

The Windows launcher selects `py -3.12`, then `py -3.11`, then `python`.

The manual cross-platform validation workflow is triggered by
`workflow_dispatch` only.

Docker deployments are out of scope for this script (only `--stop` works
inside containers): update with `git pull && docker compose up -d --build`.
