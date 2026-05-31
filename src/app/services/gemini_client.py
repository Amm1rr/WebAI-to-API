# src/app/services/gemini_client.py
"""
Compatibility shim for the Gemini client manager.
The authoritative implementation has moved to app.services.providers.gemini.client.
"""

from app.services.providers.gemini.client import (
    GeminiClientNotInitializedError,
    init_gemini_client,
    get_gemini_client
)

# TODO:Remove gemini_client compatibility shim after all internal imports and tests migrate to providers.gemini.client
# For backward compatibility with components or tests accessing private members
import app.services.providers.gemini.client as _client_module

def __getattr__(name):
    return getattr(_client_module, name)
