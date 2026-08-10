CHAT_COMPLETIONS_REQUEST_EXAMPLES = {
    "textOnly": {
        "summary": "Text-only request",
        "value": {
            "model": "gemini-3-flash",
            "messages": [
                {
                    "role": "user",
                    "content": "Hello!",
                }
            ],
        },
    },
    "fileRequest": {
        "summary": "File attachment request",
        "value": {
            "model": "gemini-3-flash",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Summarize this document."},
                        {
                            "type": "file",
                            "file": {
                                "filename": "invoice.pdf",
                                "file_data": "data:application/pdf;base64,JVBERi0xLjQK",
                            },
                        },
                    ],
                }
            ],
        },
    },
}


TEMPORARY_CHAT_COMPLETIONS_REQUEST_EXAMPLES = {
    "temporaryTextOnly": {
        "summary": "Temporary text-only request",
        "value": {
            "model": "gemini-3-flash",
            "messages": [
                {
                    "role": "user",
                    "content": "Hello!",
                }
            ],
        },
    },
    "temporaryStreamWithFiles": {
        "summary": "Temporary streaming request with file attachment",
        "value": {
            "model": "gemini-3-flash",
            "stream": True,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Summarize this document."},
                        {
                            "type": "file",
                            "file": {
                                "filename": "invoice.pdf",
                                "file_data": "data:application/pdf;base64,JVBERi0xLjQK",
                            },
                        },
                    ],
                }
            ],
        },
    },
}


TEMPORARY_CHAT_COMPLETIONS_RESPONSE_400 = {
    "description": "Bad Request",
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "detail": {"type": "string"},
                },
                "required": ["detail"],
                "additionalProperties": True,
            },
            "examples": {
                "conversationIdRejected": {
                    "summary": "conversation_id is not supported",
                    "value": {
                        "detail": "conversation_id is not supported on the temporary chat endpoint.",
                    },
                },
                "unsupportedProviderRejected": {
                    "summary": "Unsupported provider or model namespace",
                    "value": {
                        "detail": "Playwright models are not supported on the temporary chat endpoint.",
                    },
                },
            },
        }
    },
}


CHAT_COMPLETIONS_RESPONSE_200 = {
    "description": "Successful Response",
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "object": {"type": "string"},
                    "created": {"type": "integer"},
                    "model": {"type": "string"},
                    "choices": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer"},
                                "message": {
                                    "type": "object",
                                    "properties": {
                                        "role": {"type": "string"},
                                        "content": {
                                            "anyOf": [
                                                {"type": "string"},
                                                {"type": "null"},
                                            ]
                                        },
                                    },
                                    "required": ["role", "content"],
                                    "additionalProperties": True,
                                },
                                "finish_reason": {
                                    "anyOf": [
                                        {"type": "string"},
                                        {"type": "null"},
                                    ]
                                },
                                "artifacts": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "type": {"type": "string"},
                                            "provider": {"type": "string"},
                                            "title": {"type": "string"},
                                            "url": {"type": "string"},
                                            "mime_type": {"type": "string"},
                                        },
                                        "additionalProperties": True,
                                    },
                                },
                            },
                            "required": ["index", "message", "finish_reason"],
                            "additionalProperties": True,
                        },
                    },
                },
                "required": ["id", "object", "created", "model", "choices"],
                "additionalProperties": True,
            },
            "examples": {
                "bufferedTextOnly": {
                    "summary": "Buffered text-only response",
                    "value": {
                        "id": "chatcmpl-123",
                        "object": "chat.completion",
                        "created": 1710000000,
                        "model": "gemini-3-flash",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "Hello!",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                    },
                },
                "bufferedArtifacts": {
                    "summary": "Buffered response with artifacts",
                    "value": {
                        "id": "chatcmpl-456",
                        "object": "chat.completion",
                        "created": 1710000001,
                        "model": "gemini-3-flash",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "Done.",
                                },
                                "finish_reason": "stop",
                                "artifacts": [
                                    {
                                        "type": "image",
                                        "provider": "gemini_webapi",
                                        "title": "Generated image",
                                        "url": "https://example.invalid/artifacts/generated.png",
                                        "mime_type": "image/png",
                                    }
                                ],
                            }
                        ],
                    },
                },
            },
        },
        "text/event-stream": {
            "schema": {"type": "string"},
            "examples": {
                "streamTextDelta": {
                    "summary": "Streaming text delta chunk",
                    "value": (
                        'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1710000000,'
                        '"model":"gemini-3-flash","choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]}\\n\\n'
                    ),
                },
                "streamFinalArtifacts": {
                    "summary": "Final artifact chunk before [DONE]",
                    "value": (
                        'data: {"id":"chatcmpl-456","object":"chat.completion.chunk","created":1710000001,'
                        '"model":"gemini-3-flash","choices":[{"index":0,"delta":{},"finish_reason":"stop","artifacts":[{"type":"image","provider":"gemini_webapi","title":"Generated image","url":"https://example.invalid/artifacts/generated.png","mime_type":"image/png"}]}]}\\n\\n'
                    ),
                },
                "streamDone": {
                    "summary": "Stream terminator",
                    "value": "data: [DONE]\\n\\n",
                },
            },
        },
    },
}
