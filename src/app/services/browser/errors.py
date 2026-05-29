class WebAIRuntimeError(Exception):
    """Base runtime error for all browser and provider operations."""
    pass

class BrowserEngineError(WebAIRuntimeError):
    """Base exception for BrowserEngine-scoped errors."""
    pass

class BrowserShuttingDownError(BrowserEngineError):
    """Raised when an operation is attempted while the browser engine is shutting down."""
    pass

class BrowserDisconnectedError(BrowserEngineError):
    """Raised when the underlying browser process unexpectedly disconnects."""
    pass

class BrowserGenerationMismatchError(BrowserEngineError):
    """Raised when operating on a browser resource from a previous/stale generation."""
    
    @classmethod
    def validate(cls, resource_generation: int, current_generation: int, context_msg: str = "Browser generation mismatch detected"):
        if resource_generation != current_generation:
            raise cls(context_msg)

class SessionError(WebAIRuntimeError):
    """Base exception for ProviderSession-scoped errors."""
    pass

class SessionNotAliveError(SessionError):
    """Raised when a liveness probe on the context's keepalive page fails."""
    pass

class RequestError(WebAIRuntimeError):
    """Base exception for request-scoped failures."""
    pass

class LeaseInvalidatedError(RequestError):
    """Raised when attempting to operate on an invalidated or stale lease."""
    pass

class QueueOverflowError(RequestError):
    """Raised when the event stream queue saturates during bridge enqueuing."""
    pass
