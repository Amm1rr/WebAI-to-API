# Observability Hardening PR B - Final Deliverable

## Files Changed

1. **src/app/services/providers/gemini/auth_selector.py**
   - Added logger import
   - Added source selection attempt logging
   - Added source selection success logging
   - Added first_playwright_storage_candidate result logging

2. **src/app/services/browser/session.py**
   - Added recovery timing to _do_session_recovery
   - Added recovery success/failure logging with duration
   - Added generation rollover impact context
   - Added tab purge impact metrics (total, idle, leased counts)

## Logs Added

### Auth Source Selection Logging
```python
# Source attempt (auth_selector.py)
logger.debug("AuthSelector: Attempting source: %s", source_name)

# Source unavailable
logger.debug("AuthSelector: Source unavailable: %s", source_name)

# Source selected
logger.info("AuthSelector: Source selected: %s", source_name,
            extra={"source_name": source_name, "source_type": source_type,
                   "is_legacy": is_legacy, "supports_webapi": bool(psid),
                   "migration_needed": is_legacy})

# Playwright candidate result
logger.info("AuthSelector: Using candidate for Playwright storage: %s", candidate.source_name,
            extra={"source_name": candidate.source_name, "source_type": candidate.source_type})

# No candidate available
logger.warning("AuthSelector: No Playwright-compatible auth source available")
```

### Recovery Timing Logging
```python
# Recovery start (session.py)
logger.warning("ProviderSession(%s): Session recovery started", self.name,
                extra={"provider": self.name, "generation": self.engine.browser_generation})

# Recovery success
logger.info("ProviderSession(%s): Session recovery completed", self.name,
            extra={"provider": self.name, "duration_seconds": round(recovery_duration, 3),
                   "generation": self.engine.browser_generation})

# Recovery failure
logger.error("ProviderSession(%s): Session recovery failed: %s", self.name, e,
             exc_info=True,
             extra={"provider": self.name, "duration_seconds": round(recovery_duration, 3),
                    "error": str(e)})
```

### Generation Rollover Impact Logging
```python
# Generation rollover with context (session.py)
logger.warning("ProviderSession(%s): Browser generation rollover (%s -> %s)",
               self.name, old_generation, new_generation,
               extra={"provider": self.name, "old_generation": old_generation,
                      "new_generation": new_generation, "registry_size": len(self.conversation_registry)})

# Tab purge impact metrics (session.py)
logger.info("ProviderSession(%s): All tabs purged from registry", self.name,
            extra={"provider": self.name, "total_tabs": initial_count,
                   "idle_tabs": idle_count, "leased_tabs": leased_count,
                   "orphan_cleanup_scheduled": leased_count})
```

## Secret-Safety Review

**No secrets logged:**
- ✅ Auth source selection logs do NOT include cookie values
- ✅ Only logs metadata: source_name, source_type, is_legacy
- ✅ Does NOT log __Secure-1PSID or __Secure-1PSIDTS values
- ✅ Does NOT log authentication tokens

**Context logged instead of secrets:**
- Source type (config, json_store, legacy_cookies)
- Whether source is legacy
- Whether source supports WebAPI
- Whether migration is needed

## Compatibility Impact

**No breaking changes:**
- Auth API unchanged
- Recovery behavior unchanged
- Generation rollover behavior unchanged
- Existing tests pass (191/191)

**Log level impact:**
- DEBUG: Source selection attempts (can be disabled in production)
- INFO: Source selection, recovery completion, tab purge
- WARNING: No auth source available, generation rollover, recovery start
- ERROR: Recovery failure

## Test Results

```bash
PYTHONPATH=src pytest tests/ -v
# Result: 191 passed
```

All existing tests pass without modification:
- 46 auth-related tests passed
- 16 session/recovery tests passed
- All 191 tests passed

## Bugs Discovered

**No bugs discovered.**

The implementation added observability without changing behavior:
- Auth selector logic unchanged
- Recovery flow unchanged
- Generation rollover logic unchanged
- Tab purge logic unchanged

## Success Criteria Verification

| Criteria | Status | Evidence |
|----------|--------|----------|
| Why auth failed | ✅ | Source selection logs show which source was chosen/available |
| Which auth source was selected | ✅ | "Source selected: {source_name}" with full context |
| Which fallback path was used | ✅ | Source attempt logs show priority order |
| Whether recovery started | ✅ | "Session recovery started" log |
| Whether recovery succeeded | ✅ | "Session recovery completed" vs "failed" logs |
| How long recovery took | ✅ | Duration included in recovery logs |
| How many tabs affected by generation rollover | ✅ | total_tabs, idle_tabs, leased_tabs logged |

## Production Readiness

The system now provides sufficient observability for auth and recovery incidents:

**Example auth failure diagnosis:**

```text
1. Customer reports auth failure

2. Operator searches logs for auth events

3. Finds complete auth flow:
   - AuthSelector: Attempting source: [Gemini] config
   - AuthSelector: Source unavailable: [Gemini] config
   - AuthSelector: Attempting source: gemini.json canonical store
   - AuthSelector: Source selected: gemini.json canonical store
   - AuthSelector: Using candidate for Playwright storage: gemini.json

4. Root cause identified: Config source incomplete, fell back to json store

5. Resolution: Verify config contains correct cookie values
```

**Example recovery diagnosis:**

```text
1. Alert: Session recovery started

2. Operator searches logs for provider

3. Finds complete recovery flow:
   - ProviderSession(gemini): Session recovery started (generation: 2)
   - ProviderSession(gemini): Persistent auth state file exists and is preserved
   - ProviderSession(gemini): Session recovery completed (duration: 2.3s)

4. Recovery successful, duration acceptable

5. Resolution: No action needed, recovery worked as expected
```

**Example generation rollover impact:**

```text
1. Alert: Browser generation rollover

2. Operator searches logs for generation events

3. Finds complete impact:
   - ProviderSession(gemini): Browser generation rollover (1 -> 2)
     registry_size: 15
   - ProviderSession(gemini): All tabs purged from registry
     total_tabs: 15, idle_tabs: 12, leased_tabs: 3
     orphan_cleanup_scheduled: 3

4. Impact quantified: 15 conversations affected, 3 active requests orphaned

5. Resolution: Monitor for customer complaints about dropped requests
```
