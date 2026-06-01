# Manual audit script only.
# Not part of automated tests.
# Requires runtime/auth/gemini.json and live Gemini UI.

import asyncio
import os
import sys
from playwright.async_api import async_playwright

async def audit():
    async with async_playwright() as p:
        # Load storage state
        storage_state = "runtime/auth/gemini.json"
        if not os.path.exists(storage_state):
            print(f"Error: {storage_state} not found.")
            return

        print(f"Using storage state: {storage_state}")
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=storage_state)
        page = await context.new_page()

        print("Navigating to Gemini...")
        try:
            await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=30000)
            print(f"Current URL: {page.url}")
        except Exception as e:
            print(f"Navigation failed: {e}")
            await browser.close()
            return

        # Wait for the input to be visible (auth check)
        try:
            print(f"Page title: {await page.title()}")
            # Re-using selectors from production
            input_selector = 'div[contenteditable="true"][role="textbox"], textarea.gds-body-l, textarea[placeholder*="Gemini"]'
            await page.wait_for_selector(input_selector, timeout=20000)
            print("Successfully authenticated and reached Gemini app.")
            
            # Diagnostic: Take a look at the DOM structure
            body_html = await page.evaluate("() => document.body.innerHTML.substring(0, 1000)")
            # print(f"Body snippet: {body_html}")
            
        except Exception as e:
            print(f"Failed to reach Gemini app or auth expired: {e}")
            print(f"Current URL: {page.url}")
            # print(f"Page content snippet: {(await page.content())[:1000]}")
            await browser.close()
            return

        print("\n--- Model Selector Audit ---")
        
        # Give it a bit more time for dynamic content
        await asyncio.sleep(5)
        
        selectors_to_check = [
            'button[aria-label*="Select model"]',
            '[data-test-id="model-selector"]',
            '.input-area-switch-label',
            'button[aria-haspopup="menu"]',
            'button:has-text("Gemini")',
            'button'
        ]
        
        found_any = False
        for selector in selectors_to_check:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                if count > 0:
                    print(f"Found {count} elements for selector: {selector}")
                    for i in range(count):
                        el = locator.nth(i)
                        text = (await el.inner_text()).replace("\n", " ")
                        aria_label = await el.get_attribute("aria-label")
                        print(f"  [{i}] Text: '{text.strip()}', Aria-Label: '{aria_label}'")
                    found_any = True
            except:
                pass
        
        if not found_any:
            print("No common model selectors found via static selectors.")
            # Let's try to find any button that might be it
            buttons = await page.query_selector_all('button')
            print(f"Total buttons on page: {len(buttons)}")
            for btn in buttons:
                label = await btn.get_attribute("aria-label")
                if label and "Gemini" in label:
                    print(f"Potential button: label='{label}'")

        # 2. Try to find the active model name in the UI
        print("\n--- Active Model Detection ---")
        # Often displayed near the top or in the input area
        potential_model_displays = page.locator('button[aria-label*="Select model"], .model-name, .mode-title, .active-model')
        count = await potential_model_displays.count()
        if count == 0:
             # Try searching by text
             gemini_mentions = page.locator('*:has-text("Gemini")')
             # print(f"Found {await gemini_mentions.count()} elements mentioning Gemini")
        else:
            for i in range(count):
                 print(f"Potential model display: {(await potential_model_displays.nth(i).inner_text()).strip()}")

        # 3. Try to click and list menu items if found
        selector = 'button[aria-label*="Open mode picker"]'
        
        if await page.locator(selector).count() > 0:
            print(f"\nAttempting to open model menu via {selector}...")
            await page.click(selector)
            await asyncio.sleep(3) # wait for menu
            
            # Look for menu items
            menu_items = page.locator('[role="menuitem"], [role="option"], .mat-mdc-menu-item, .model-item, [role="listbox"] > *')
            item_count = await menu_items.count()
            print(f"Found {item_count} menu items.")
            
            target_model_text = "3.1 Pro"
            clicked = False
            for i in range(item_count):
                item = menu_items.nth(i)
                text = await item.inner_text()
                if target_model_text in text:
                    print(f"Found target model '{target_model_text}' in item: '{text.strip()}'. Clicking...")
                    await item.click()
                    clicked = True
                    break
            
            if clicked:
                await asyncio.sleep(2)
                new_text = await page.locator(selector).inner_text()
                print(f"Model picker button text after click: '{new_text.strip()}'")
                
                # Try sending a prompt
                print("\nSending prompt 'hi'...")
                await page.locator(input_selector).first.fill("hi")
                await page.keyboard.press("Enter")
                
                print("Waiting for response to finish (polling for send button to be enabled)...")
                send_button_selector = 'button[aria-label*="Send message"]'
                # Poll for up to 30 seconds
                for _ in range(30):
                    is_enabled = await page.locator(send_button_selector).is_enabled()
                    if is_enabled:
                        print("Response finished.")
                        break
                    await asyncio.sleep(1)
                
                # Check if selector is still active
                is_disabled = await page.locator(selector).is_disabled()
                print(f"Model selector is disabled after response finished: {is_disabled}")
                
                if not is_disabled:
                    print("Attempting to switch model AGAIN after response...")
                    await page.click(selector)
                    await asyncio.sleep(2)
                    
                    menu_items = page.locator('[role="menuitem"], [role="option"], .mat-mdc-menu-item, .model-item, [role="listbox"] > *')
                    print(f"Found {await menu_items.count()} menu items after response.")
                    
                    # Try switching back to Flash
                    target_back = "3.5 Flash"
                    clicked_back = False
                    for i in range(await menu_items.count()):
                        item = menu_items.nth(i)
                        text = await item.inner_text()
                        if target_back in text:
                            print(f"Switching back to '{target_back}'...")
                            await item.click()
                            clicked_back = True
                            break
                    
                    if clicked_back:
                        await asyncio.sleep(2)
                        final_text = await page.locator(selector).inner_text()
                        print(f"Model picker button text after second switch: '{final_text.strip()}'")
        else:
            print("\nCould not find a clickable model selector.")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(audit())
