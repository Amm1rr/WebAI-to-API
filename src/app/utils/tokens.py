import secrets

def generate_opaque_token() -> str:
    """Generate a cryptographically secure opaque token for conversation IDs."""
    return secrets.token_urlsafe(16)
