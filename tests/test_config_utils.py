import pytest
import os
import configparser
import tempfile
import asyncio
from app.utils.config_utils import save_config_atomic

@pytest.mark.asyncio
async def test_save_config_atomic_success():
    """Verify save_config_atomic correctly writes a config file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "test.conf")
        
        config = configparser.ConfigParser()
        config["Test"] = {"key": "value"}
        
        success = await save_config_atomic(config, config_path)
        
        assert success is True
        assert os.path.exists(config_path)
        
        # Verify content
        read_config = configparser.ConfigParser()
        read_config.read(config_path)
        assert read_config["Test"]["key"] == "value"

@pytest.mark.asyncio
async def test_save_config_atomic_concurrency():
    """Verify save_config_atomic handles concurrent write attempts via its internal lock."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "concurrent.conf")
        
        # Create 20 concurrent save tasks to increase pressure
        async def save_task(index):
            config = configparser.ConfigParser()
            config["Task"] = {"index": str(index)}
            return await save_config_atomic(config, config_path)
        
        results = await asyncio.gather(*(save_task(i) for i in range(20)))
        
        assert all(results)
        assert os.path.exists(config_path)
        
        # 1. Verify file is parsable and not corrupted
        read_config = configparser.ConfigParser()
        try:
            read_config.read(config_path, encoding="utf-8")
        except Exception as e:
            pytest.fail(f"Config file corrupted and unparsable: {e}")
            
        # 2. Verify no partial data (the section must exist)
        assert "Task" in read_config
        assert int(read_config["Task"]["index"]) in range(20)
        
        # 3. Verify no temporary files remain in the directory
        files = os.listdir(tmpdir)
        # Only the main config file should be present
        assert len(files) == 1
        assert files[0] == "concurrent.conf"
