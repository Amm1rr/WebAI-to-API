# scratch/runtime_validation.py
import asyncio
import logging
import sys
import os

# Adjust path to import app services
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout
)

# Set specific loggers to show detailed output
logging.getLogger("app").setLevel(logging.INFO)

from app.services.gemini_client import init_gemini_client, get_gemini_client

async def main():
    print("--- Initiating candidate-waterfall runtime validation ---")
    
    # 1. Run the initialization path
    success = await init_gemini_client()
    print(f"\nInitialization Result: {success}")
    
    if not success:
        print("Initialization failed.")
        return

    # 2. Retrieve client and inspect status
    try:
        client_wrapper = get_gemini_client()
        inner_client = client_wrapper.client
        
        status = "UNKNOWN"
        if hasattr(inner_client, 'account_status'):
            status = inner_client.account_status.name
            
        print(f"Final Client Registered Status: {status}")
        
        # 3. Test fetch_gems
        print("\nTesting fetch_gems()...")
        gems = await client_wrapper.fetch_gems()
        print(f"fetch_gems() successfully retrieved {len(gems)} gems.")
        for i, gem in enumerate(gems[:3]):
            gem_name = getattr(gem, 'name', 'Unknown')
            gem_id = getattr(gem, 'id', 'Unknown')
            print(f"  Gem {i+1}: {gem_name} (ID: {gem_id})")
            
        # 4. Clean up
        print("\nClosing client wrapper...")
        await client_wrapper.close()
        print("Cleanup completed.")
        
    except Exception as e:
        print(f"Error during runtime checks: {e}", file=sys.stderr)

if __name__ == "__main__":
    asyncio.run(main())
