import asyncio
import os
import sys

# Add src to sys.path to allow imports
sys.path.append(os.path.join(os.getcwd(), "src"))

from app.services.browser.engine import get_browser_engine

async def verify_login():
    """
    Utility script to launch the persistent browser context for manual login.
    """
    engine = await get_browser_engine()
    page = await engine.get_page()
    
    print("\n" + "="*50)
    print("PLAYWRIGHT LOGIN VERIFICATION")
    print("="*50)
    print("Navigating to https://gemini.google.com/app...")
    
    await page.goto("https://gemini.google.com/app")
    
    print("\nINSTRUCTIONS:")
    print("1. If the browser window appeared, please log in to your Google account.")
    print("2. Once logged in and the Gemini interface is visible, the session will be saved.")
    print("3. This script will stay open for 5 minutes. Press Ctrl+C to exit earlier.")
    print("="*50 + "\n")
    
    try:
        # Keep browser open for manual interaction
        await asyncio.sleep(300)
    except KeyboardInterrupt:
        print("\nVerification interrupted by user.")
    except Exception as e:
        print(f"\nAn error occurred: {e}")
    finally:
        await engine.close()
        print("BrowserEngine closed.")

if __name__ == "__main__":
    try:
        asyncio.run(verify_login())
    except KeyboardInterrupt:
        pass
