# src/app/services/gemini_client.py
"""
[DEPRECATED] Compatibility shim for the Gemini client manager.

The authoritative implementation has been moved to:
app.services.providers.gemini.client

All new components should import from the authoritative path. 
This shim is maintained for backward compatibility with legacy endpoints 
and existing test mocks.
"""

from app.services.providers.gemini.client import (
    GeminiClientNotInitializedError,
    init_gemini_client,
    get_gemini_client
)

# For backward compatibility with components or tests accessing private members
import app.services.providers.gemini.client as _client_module

def __getattr__(name):
    return getattr(_client_module, name)
