# src/app/services/providers/exceptions.py

class SessionRecoveryError(Exception):
    """Base class for all conversation snapshot recovery errors."""
    pass


class SnapshotNotFoundError(SessionRecoveryError):
    """Raised when the conversation snapshot is missing in storage."""
    pass


class StateIntegrityError(SessionRecoveryError):
    """Raised when the snapshot session_state is corrupted or invalid."""
    pass


class ProviderThreadExpiredError(SessionRecoveryError):
    """Raised when the remote provider reports that the conversation thread has expired."""
    pass
