# Observability PR B Verification

## Auth Logging Current State

### Currently Logged
- ✅ Auth backend configuration warnings (auth_manager.py)
- ✅ Login state transitions (auth_manager.py)
- ✅ Post-login recovery steps (auth_manager.py, auth.py)
- ✅ Legacy cookie deprecation warnings (auth_loader.py)
- ✅ State file parsing errors (auth_loader.py)
- ✅ Login flow monitoring (auth.py)

### NOT Logged
- ❌ Auth source selection (which source was chosen)
- ❌ Auth fallback path details (which fallback was used)
- ❌ Auth validation success/failure
- ❌ Auth failure root cause details
- ❌ Auth timing information

### Key Gap: auth_selector.py
The auth_selector.py file has **NO logging at all** despite being responsible for source selection and fallback logic.

## Recovery Logging Current State

### Currently Logged
- ✅ Session failure escalation (session.py:405)
- ✅ Generation rollover detection (session.py:356, engine.py:103)
- ✅ Browser disconnection (engine.py:115)
- ✅ Tab purge completion (session.py:392)
- ✅ Client initialization attempts (client.py)

### NOT Logged
- ❌ Recovery start timestamps
- ❌ Recovery duration tracking
- ❌ Recovery success/failure status (explicit)
- ❌ Number of affected tabs/sessions during recovery
- ❌ Number of active conversations lost during generation rollover
- ❌ Recovery attempt counts
- ❌ Time-to-recovery metrics

## Generation Rollover Logging Current State

### Currently Logged
- ✅ Generation number transition (X → Y)
- ✅ "New generation active"

### NOT Logged
- ❌ Number of affected sessions
- ❌ Number of tabs purged
- ❌ Number of active conversations lost
- ❌ Time taken for rollover process
- ❌ Impact on active requests

## Missing Logs Summary

### Critical (Production Impact)
| # | Missing | Impact |
|---|---------|--------|
| 1 | Auth source selection | Cannot determine which auth source was used |
| 2 | Auth fallback path | Cannot debug auth failures |
| 3 | Recovery duration | Cannot detect slow recovery |
| 4 | Generation rollover impact | Cannot identify affected conversations |
| 5 | Recovery success/failure | Cannot measure recovery health |

### High (Operational Impact)
| # | Missing | Impact |
|---|---------|--------|
| 6 | Auth validation logs | Cannot troubleshoot auth issues |
| 7 | Recovery attempt counts | Cannot measure recovery reliability |
| 8 | Time-to-recovery metrics | Cannot optimize recovery performance |

## Proposed Minimal Changes

### 1. Auth Source Selection Logging
- Add logs in auth_selector.py for source selection
- Log which source is being attempted
- Log which source was selected
- Log why sources were rejected

### 2. Auth Fallback Path Logging
- Add logs in auth_loader.py for fallback attempts
- Log which fallback priority is being used
- Log fallback success/failure

### 3. Auth Validation Logging
- Add logs in auth_manager.py for validation success/failure
- Log validation results with context

### 4. Recovery Timing Logging
- Add start/end timestamps for recovery operations
- Calculate and log recovery duration
- Add recovery success/failure logs

### 5. Generation Rollover Impact Logging
- Log number of affected tabs
- Log number of purged tabs
- Log idle vs leased tab breakdown
- Log rollover duration

### 6. Error Context Enhancement
- Add more detailed error context for auth failures
- Add recovery trigger context
- Add operation context for recovery operations
