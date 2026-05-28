import asyncio
import uuid
import time
from typing import List

from app.services.browser.engine import BrowserEngine, TabStatus
from app.config import CONFIG

async def simulate_persistent_request(engine: BrowserEngine, session_name: str, delay: float = 0.5):
    """Simulates a request that results in a persistent tab."""
    session = await engine.get_session(session_name)
    request_id = str(uuid.uuid4())
    conversation_id = f"conv_{request_id[:8]}"
    
    # Simulate acquire_lease
    lease = await session.acquire_lease(request_id=request_id)
    
    try:
        # Simulate work
        await asyncio.sleep(delay)
        
        # Simulate registration to persistent tab
        tab = await session.register_conversation(conversation_id, lease)
        
        # Simulate returning to IDLE
        await lease.close()
    except Exception as e:
        print(f"Error during request simulation: {e}")

async def run_stress_test():
    """Runs the stress test for soft-cap."""
    engine = await BrowserEngine.get_instance()
    
    # Wait for initial setup
    session = await engine.get_session("gemini")
    await session.ensure_healthy()
    
    print(f"Starting test with max_total_tabs = {engine.max_total_tabs}")
    
    tasks = []
    # Create enough tabs to exceed soft-cap
    for _ in range(engine.max_total_tabs + 10):
        tasks.append(asyncio.create_task(simulate_persistent_request(engine, "gemini", 0.1)))
        # slight stagger to avoid completely simultaneous semaphore hits
        await asyncio.sleep(0.01)
        
    await asyncio.gather(*tasks)
    
    # Give a moment for evictions to settle
    await asyncio.sleep(1)
    
    final_count = engine.total_page_count
    print(f"Final total page count: {final_count} (Cap: {engine.max_total_tabs})")
    
    if final_count <= engine.max_total_tabs + 5: # allow small buffer for timing
        print("PASS: Soft-cap enforced successfully.")
    else:
        print(f"FAIL: Soft-cap exceeded by too much. Count: {final_count}")
        
    await engine.close()

if __name__ == "__main__":
    asyncio.run(run_stress_test())
