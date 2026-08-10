import asyncio
import configparser
import logging
import os
import tempfile
from anyio import to_thread

logger = logging.getLogger(__name__)

# Global lock for synchronous configuration writes to prevent race conditions.
_config_lock = asyncio.Lock()

async def save_config_atomic(config: configparser.ConfigParser, config_file: str = "config.conf") -> bool:
    """
    Save the configuration to a file atomically and non-blockingly.
    
    Uses a global lock to ensure sequential access and anyio.to_thread to avoid 
    blocking the main event loop.
    """
    async with _config_lock:
        try:
            # We use a temporary file to ensure the write is atomic.
            # 1. Create a temporary file in the same directory as the target config.
            dir_name = os.path.dirname(os.path.abspath(config_file))
            fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix=".config_tmp_")
            
            try:
                # 2. Write the config to the temporary file in a thread pool.
                def _write():
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        config.write(f)
                    # Force data to be written to disk.
                    # Note: We already closed f via context manager, but we need to ensure 
                    # the directory entry is updated too if we wanted maximum durability.
                
                await to_thread.run_sync(_write)
                
                # 3. Rename the temporary file to the target config file (atomic on most OSs).
                await to_thread.run_sync(os.replace, temp_path, config_file)
                
                return True
            except Exception as e:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise e
        except Exception as e:
            logger.error(f"Failed to save configuration atomically: {e}", exc_info=True)
            return False
