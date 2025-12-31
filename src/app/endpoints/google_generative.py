# src/app/endpoints/google_generative.py
from fastapi import APIRouter, HTTPException
from app.logger import logger
from schemas.request import GoogleGenerativeRequest
from app.services.gemini_client import get_gemini_client, GeminiClientNotInitializedError

router = APIRouter()

# @router.post("/v1beta/models/{model}:generateContent")
@router.post("/v1beta/models/{model}")
async def google_generative_generate(model: str, request: GoogleGenerativeRequest):
    try:
        gemini_client = get_gemini_client()
    except GeminiClientNotInitializedError as e:
        raise HTTPException(status_code=503, detail=str(e))

    model = model.split(":")

    try:
        # Extract the text from the request
        prompt = ""
        if request.contents:
            for content in request.contents:
                if content.parts:
                    for part in content.parts:
                        prompt += part.text

        # Call the gemini_client with the extracted prompt
        response = await gemini_client.generate_content(prompt, model[0])

        # Format the response to match the Google Generative AI API format
        google_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": response.text
                            }
                        ],
                        "role": "model"
                    },
                    "finishReason": "STOP",
                    "index": 0,
                    "safetyRatings": [
                        {
                            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            "probability": "NEGLIGIBLE"
                        },
                        {
                            "category": "HARM_CATEGORY_HATE_SPEECH",
                            "probability": "NEGLIGIBLE"
                        },
                        {
                            "category": "HARM_CATEGORY_HARASSMENT",
                            "probability": "NEGLIGIBLE"
                        },
                        {
                            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                            "probability": "NEGLIGIBLE"
                        }
                    ]
                }
            ],
            "promptFeedback": {
                "safetyRatings": [
                    {
                        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        "probability": "NEGLIGIBLE"
                    },
                    {
                        "category": "HARM_CATEGORY_HATE_SPEECH",
                        "probability": "NEGLIGIBLE"
                    },
                    {
                        "category": "HARM_CATEGORY_HARASSMENT",
                        "probability": "NEGLIGIBLE"
                    },
                    {
                        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                        "probability": "NEGLIGIBLE"
                    }
                ]
            }
        }

        return google_response
    except Exception as e:
        logger.error(f"Error in /google_generative endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error generating content: {str(e)}")
