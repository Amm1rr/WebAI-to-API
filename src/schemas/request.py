# src/schemas/request.py
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

class GeminiModels(str, Enum):
    """
    An enumeration of the available Gemini models based on the latest documentation.
    """
    
    # Available Models
    FLASH_1_5 = "gemini-1.5-flash"
    FLASH_2_0 = "gemini-2.0-flash"
    FLASH_THINKING = "gemini-2.0-flash-thinking"
    FLASH_THINKING_WITH_APPS = "gemini-2.0-flash-thinking-with-apps"
    # End of Available Models
    
    # Gemini 2.5 Series
    PRO_2_5 = "gemini-2.5-pro"
    FLASH_2_5 = "gemini-2.5-flash"
    FLASH_LITE_2_5_PREVIEW = "gemini-2.5-flash-lite-preview-06-17"
    FLASH_NATIVE_AUDIO_DIALOG_2_5 = "gemini-2.5-flash-preview-native-audio-dialog"
    FLASH_NATIVE_AUDIO_THINKING_2_5 = "gemini-2.5-flash-exp-native-audio-thinking-dialog"
    FLASH_TTS_2_5_PREVIEW = "gemini-2.5-flash-preview-tts"
    PRO_TTS_2_5_PREVIEW = "gemini-2.5-pro-preview-tts"

    # Gemini 2.0 Series
    # FLASH_2_0 = "gemini-2.0-flash"
    FLASH_IMAGE_GENERATION_2_0_PREVIEW = "gemini-2.0-flash-preview-image-generation"
    FLASH_LITE_2_0 = "gemini-2.0-flash-lite"

    # Gemini 1.5 Series
    # FLASH_1_5 = "gemini-1.5-flash"
    FLASH_8B_1_5 = "gemini-1.5-flash-8b"
    PRO_1_5 = "gemini-1.5-pro"

    # Other Models (Embedding, Imagen, Veo)
    EMBEDDING_EXP = "gemini-embedding-exp"
    IMAGEN_4_GENERATE_PREVIEW = "imagen-4.0-generate-preview-06-06"
    IMAGEN_4_ULTRA_GENERATE_PREVIEW = "imagen-4.0-ultra-generate-preview-06-06"
    IMAGEN_3_GENERATE = "imagen-3.0-generate-002"
    VEO_2_GENERATE = "veo-2.0-generate-001"
    
    # Live Models
    LIVE_FLASH_2_5_PREVIEW = "gemini-live-2.5-flash-preview"
    LIVE_FLASH_2_0 = "gemini-2.0-flash-live-001"


class GeminiRequest(BaseModel):
    message: str
    model: GeminiModels = Field(default=GeminiModels.FLASH_2_0, description="Model to use for Gemini.")
    files: Optional[List[str]] = []

class OpenAIChatRequest(BaseModel):
    messages: List[dict]
    model: Optional[GeminiModels] = None
    stream: Optional[bool] = False