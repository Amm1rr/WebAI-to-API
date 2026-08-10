# src/app/services/browser/auth_types.py

class AuthStatus:
    # Playwright Statuses
    VALID_SESSION = "VALID_SESSION"
    NO_SESSION = "NO_SESSION"
    EXPIRED_SESSION = "EXPIRED_SESSION"
    INVALID_STATE = "INVALID_STATE"

    # gemini-webapi Statuses
    AUTHENTICATED = "AUTHENTICATED"
    GUEST = "GUEST"
    INVALID = "INVALID"

class LoginState:
    IDLE = "IDLE"
    LOGIN_IN_PROGRESS = "LOGIN_IN_PROGRESS"
