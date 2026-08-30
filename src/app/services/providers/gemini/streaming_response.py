import asyncio

from fastapi.responses import StreamingResponse

from app.logger import logger


async def _await_shielded(awaitable):
    task = asyncio.ensure_future(awaitable)
    cancellation = None
    operation_error = None

    # Keep critical teardown running while preserving caller cancellation.
    while True:
        try:
            await asyncio.shield(task)
            break
        except asyncio.CancelledError as error:
            if task.cancelled():
                raise
            cancellation = error
        except Exception as error:
            operation_error = error
            break

    if cancellation is not None:
        raise cancellation
    if operation_error is not None:
        raise operation_error


class GeminiLeaseStreamingResponse(StreamingResponse):
    def __init__(self, content, *, lease, cleanup=None, **kwargs):
        super().__init__(content, **kwargs)
        self._gemini_lease = lease
        self._gemini_cleanup = cleanup

    async def __call__(self, scope, receive, send):
        cancellation = None
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self._gemini_cleanup is not None:
                try:
                    await _await_shielded(self._gemini_cleanup())
                except asyncio.CancelledError as error:
                    cancellation = error
                except Exception as e:
                    logger.warning(f"Error cleaning up Gemini streaming response: {e}")
            try:
                await _await_shielded(self._gemini_lease.release())
            except asyncio.CancelledError as error:
                cancellation = cancellation or error
            except Exception as e:
                logger.warning(f"Error releasing Gemini streaming lease: {e}")
            if cancellation is not None:
                raise cancellation
