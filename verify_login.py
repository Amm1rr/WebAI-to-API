import asyncio
import os
import sys
import re

# Add src to sys.path to allow imports
sys.path.append(os.path.join(os.getcwd(), "src"))

from app.services.browser.engine import get_browser_engine
from app.services.providers.gemini_playwright_scripts import SELECTORS

async def verify_login():
    """
    SMART Utility script for manual login.
    Detects successful login and saves state automatically.
    """
    engine = await get_browser_engine()
    page = await engine.get_page()
    
    print("\n" + "="*50)
    print("PLAYWRIGHT SMART LOGIN VERIFIER")
    print("="*50)
    print("Navigating to https://gemini.google.com/app...")
    
    await page.goto("https://gemini.google.com/app")
    
    print("\nINSTRUCTIONS:")
    print("1. Log in to your Google account in the browser window.")
    print("2. Once you reach the chat interface, the script will detect it.")
    print("3. State will be saved automatically upon detection.")
    print("4. Press ENTER in this terminal to finish and close.")
    print("="*50 + "\n")
    
    login_detected = False

    async def auto_save_loop():
        nonlocal login_detected
        try:
            while True:
                # Check if we are logged in by looking for the input box
                # Correct Playwright Async API usage: await is_visible() without timeout
                input_exists = await page.locator(SELECTORS["INPUT"]).first.is_visible()
                if input_exists and not login_detected:
                    login_detected = True
                    await engine.save_state()
                    print("\n[SUCCESS] Login detected and session saved to disk!")
                    print("You can now safely press ENTER or continue setting up.")
                
                # Periodic backup every 20 seconds instead of 5
                await asyncio.sleep(20)
                if login_detected:
                    await engine.save_state()
        except Exception:
            pass # Silent exit on page close

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
        await engine.save_state()
        await engine.close()
        print("\nSession saved. BrowserEngine closed.")

if __name__ == "__main__":
    try:
        asyncio.run(verify_login())
    except KeyboardInterrupt:
        pass
