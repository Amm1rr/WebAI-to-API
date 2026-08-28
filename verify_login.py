import asyncio
import os
import sys
import re
from contextlib import suppress

# Add src to sys.path to allow imports
sys.path.append(os.path.join(os.getcwd(), "src"))

try:
    from playwright.async_api import Error as PlaywrightError
except ModuleNotFoundError as exc:
    if exc.name == "playwright":
        print("\n[ERROR] Playwright is not installed.\n")
        print("This utility requires Playwright.\n")
        print("Install dependencies with:\n")
        print("  poetry install")
        print("  poetry run playwright install chromium\n")
        print("Then run:\n")
        print("  poetry run python verify_login.py")
        sys.exit(1)
    raise
except ImportError as exc:
    if "greenlet" in str(exc).lower() or "_greenlet" in str(exc).lower():
        print("\n[ERROR] Playwright dependency failed to load.\n")
        print("The Python package 'greenlet' is installed but its native extension could not be loaded.\n")

        if os.name == "nt":
            print(
                "On Windows, this is commonly caused by a missing or corrupted\n"
                "Microsoft Visual C++ Redistributable 2015-2022 (x64).\n"
            )
            print("Try the following:\n")
            print("  1. Install or repair Microsoft Visual C++ Redistributable 2015-2022 (x64)")
            print("  2. Reopen your terminal")
            print("  3. Reinstall Playwright dependencies:\n")
            print("     poetry run pip install --force-reinstall greenlet playwright")
            print("     poetry run playwright install chromium\n")
        else:
            print("Try reinstalling dependencies:\n")
            print("  poetry run pip install --force-reinstall greenlet playwright")
            print("  poetry run playwright install chromium\n")

        sys.exit(1)

    raise

from app.services.browser.engine import get_browser_engine
from app.services.providers.gemini.auth import persist_shared_gemini_state
from app.services.providers.gemini.scripts.gemini_scripts import SELECTORS
from app.logger import logger


async def _wait_for_stdin_enter():
    """
    Wait for ENTER without blocking the event loop.

    On Unix-like platforms, use add_reader().
    On Windows, use non-blocking msvcrt polling because ProactorEventLoop
    does not support add_reader().
    """
    if os.name == "nt":
        import msvcrt

        while True:
            if msvcrt.kbhit():
                key = msvcrt.getwch()
                if key in ("\r", "\n"):
                    return "enter"
            await asyncio.sleep(0.1)

    loop = asyncio.get_running_loop()
    done = loop.create_future()
    fd = sys.stdin.fileno()

    def on_stdin_ready():
        with suppress(Exception):
            loop.remove_reader(fd)
        if not done.done():
            sys.stdin.readline()
            done.set_result("enter")

    loop.add_reader(fd, on_stdin_ready)
    try:
        return await done
    finally:
        with suppress(Exception):
            loop.remove_reader(fd)


async def _wait_for_browser_shutdown(engine, page, session, poll_interval=0.25):
    while True:
        if engine.is_shutting_down:
            return "engine_shutdown"
        try:
            if page.is_closed():
                return "page_closed"
        except PlaywrightError:
            return "page_closed"
        except Exception:
            return "page_closed"
        if not session.is_alive:
            return "session_closed"
        await asyncio.sleep(poll_interval)


async def _wait_for_completion_signal(engine, page, session, stdin_waiter=None):
    stdin_waiter = stdin_waiter or _wait_for_stdin_enter
    wait_tasks = {
        asyncio.create_task(stdin_waiter()): "stdin",
        asyncio.create_task(_wait_for_browser_shutdown(engine, page, session)): "browser",
    }

    done, pending = await asyncio.wait(wait_tasks.keys(), return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    finished = done.pop()
    return await finished


async def verify_login():
    """
    SMART Utility script for manual login.
    Detects successful login and saves state automatically.
    """
    bootstrap_engine = await get_browser_engine(headless=False, is_bootstrap=True)
    page_wrapper = None
    primary_error = None
    try:
        engine = bootstrap_engine
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
        print("   You may also close the browser window to finish after login is detected.")
        print("="*60 + "\n")
        
        login_detected = False
        persistence_error = None

        async def persist_state():
            nonlocal persistence_error
            try:
                persisted = await persist_shared_gemini_state(session)
            except RuntimeError as exc:
                persistence_error = exc
                print(f"\n[ERROR] {persistence_error}", file=sys.stderr)
                return False
            except Exception:
                logger.error(
                    "Failed to persist authenticated state to %s.",
                    resolved_path,
                    exc_info=True,
                )
                persisted = False

            if persisted:
                return True

            if persistence_error is None:
                persistence_error = RuntimeError(
                    f"Authenticated state could not be persisted to: {resolved_path}"
                )
                print(f"\n[ERROR] {persistence_error}", file=sys.stderr)
            return False
 
        async def auto_save_loop():
            nonlocal login_detected
            try:
                while True:
                    # Check if we are logged in by looking for the input box
                    input_exists = await page.locator(SELECTORS["INPUT"]).first.is_visible()
                    if input_exists and not login_detected:
                        if not await persist_state():
                            return
                        login_detected = True
                        print(f"\n[SUCCESS] Shared Gemini authentication state saved atomically to: {resolved_path}")
                        print("You can now safely press ENTER to finish.")
                    
                    # Periodic backup every 20 seconds
                    await asyncio.sleep(20)
                    if login_detected:
                        if not await persist_state():
                            return
            except asyncio.CancelledError:
                raise
            except PlaywrightError:
                pass # Expected Playwright closure errors exit silently
            except Exception as e:
                logger.warning(f"Unexpected error in auto-save loop: {e}", exc_info=True)
 
        # Start the background observer
        save_task = asyncio.create_task(auto_save_loop())
        completion_task = asyncio.create_task(_wait_for_completion_signal(engine, page, session))
  
        # Wait for user to press Enter in a non-blocking way for the loop
        try:
            done, pending = await asyncio.wait(
                {save_task, completion_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                await task
            if persistence_error is not None:
                raise persistence_error
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
            if login_detected and persistence_error is None:
                if await persist_state():
                    print(f"\n[FINAL SAVE] Verified persistent state saved to: {resolved_path}")
            
            if persistence_error is not None:
                raise persistence_error
                
            print("Manual bootstrap utility successfully completed and exiting...")
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_error = None
        try:
            if page_wrapper:
                await page_wrapper.close()
        except BaseException as exc:
            cleanup_error = exc

        try:
            await bootstrap_engine.close(save_state=False)
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
            else:
                logger.error("Manual bootstrap engine cleanup failed after page cleanup failure.", exc_info=True)

        if cleanup_error is not None:
            if primary_error is not None:
                logger.error("Manual bootstrap cleanup failed while preserving original error.", exc_info=True)
            else:
                raise cleanup_error

if __name__ == "__main__":
    try:
        asyncio.run(verify_login())
    except KeyboardInterrupt:
        pass
