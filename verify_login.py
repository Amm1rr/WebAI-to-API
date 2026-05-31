import asyncio
import os
import sys
import re

# Add src to sys.path to allow imports
sys.path.append(os.path.join(os.getcwd(), "src"))

from app.services.browser.engine import get_browser_engine
from app.services.browser.adapters.scripts.gemini_scripts import SELECTORS
from playwright.async_api import Error as PlaywrightError
from app.logger import logger

async def verify_login():
    """
    SMART Utility script for manual login.
    Detects successful login and saves state automatically.
    """
    bootstrap_engine = await get_browser_engine(headless=False, is_bootstrap=True)
    async with bootstrap_engine as engine:
        # 1. Obtain managed page via engine to ensure browser health/init with persistence enabled
        page_wrapper = await engine.get_page("gemini", enable_persistence=True)
        page = page_wrapper.page
        
        # 2. Fetch the session for scoped state persistence logic
        session = await engine.get_session("gemini", enable_persistence=True)
        
        # Resolve the destination path for display
        resolved_path = os.path.abspath(session.state_path)
        
        print("\n" + "="*60)
        print("MANUAL BOOTSTRAP UTILITY: GEMINI SESSION INITIALIZATION")
        print("="*60)
        print(f"Auth State Directory: {os.path.dirname(resolved_path)}")
        print(f"Target State File   : {resolved_path}")
        print("-"*60)
        print("Navigating to https://gemini.google.com/app...")
        
        await page.goto("https://gemini.google.com/app")
        
        print("\nINSTRUCTIONS:")
        print("1. Log in to your Google account in the headful browser window.")
        print("2. Once you reach the chat interface, this utility will automatically detect it.")
        print("3. The persistent state will be saved atomically to the target file.")
        print("4. Press ENTER in this terminal to complete verification and close.")
        print("="*60 + "\n")
        
        login_detected = False
 
        async def auto_save_loop():
            nonlocal login_detected
            try:
                while True:
                    # Check if we are logged in by looking for the input box
                    input_exists = await page.locator(SELECTORS["INPUT"]).first.is_visible()
                    if input_exists and not login_detected:
                        login_detected = True
                        # Use session-scoped save_state
                        await session.save_state()
                        print(f"\n[SUCCESS] Login detected! State saved atomically to: {resolved_path}")
                        print("You can now safely press ENTER to finish.")
                    
                    # Periodic backup every 20 seconds
                    await asyncio.sleep(20)
                    if login_detected:
                        await session.save_state()
            except asyncio.CancelledError:
                raise
            except PlaywrightError:
                pass # Expected Playwright closure errors exit silently
            except Exception as e:
                logger.warning(f"Unexpected error in auto-save loop: {e}", exc_info=True)
 
        # Start the background observer
        save_task = asyncio.create_task(auto_save_loop())
 
        # Wait for user to press Enter in a non-blocking way for the loop
        try:
            # Using run_in_executor to not block the event loop while waiting for input
            await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
        except KeyboardInterrupt:
            pass
        finally:
            save_task.cancel()
            try:
                await save_task
            except asyncio.CancelledError:
                pass
            
            # Deterministic shutdown ordering
            # 1. Final session save
            if login_detected:
                await session.save_state()
                print(f"\n[FINAL SAVE] Verified persistent state saved to: {resolved_path}")
            
            # 2. Release managed resources (closes page and releases semaphore)
            if page_wrapper:
                await page_wrapper.close()
                
            print("Manual bootstrap utility successfully completed and exiting...")

if __name__ == "__main__":
    try:
        asyncio.run(verify_login())
    except KeyboardInterrupt:
        pass
