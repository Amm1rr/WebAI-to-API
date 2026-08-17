import asyncio

from fastapi.responses import StreamingResponse

from app.logger import logger


class GeminiLeaseStreamingResponse(StreamingResponse):
    def __init__(self, content, *, lease, cleanup=None, **kwargs):
        super().__init__(content, **kwargs)
        self._gemini_lease = lease
        self._gemini_cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self._gemini_cleanup is not None:
                try:
                    await self._gemini_cleanup()
                except Exception as e:
                    logger.warning(f"Error cleaning up Gemini streaming response: {e}")
            try:
                await asyncio.shield(self._gemini_lease.release())
            except Exception as e:
                logger.warning(f"Error releasing Gemini streaming lease: {e}")
