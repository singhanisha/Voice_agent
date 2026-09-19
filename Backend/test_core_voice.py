import asyncio
import logging
from core_voice_loop import CallSession
from dotenv import load_dotenv

logging.basicConfig(level=logging.DEBUG)

async def main():
    load_dotenv()
    try:
        print("Initializing CallSession...")
        session = CallSession(system_prompt="Hello")
        print("Starting CallSession...")
        await session.start()
        print("Session started successfully.")
        await asyncio.sleep(2)
        print("Stopping session...")
        await session.stop()
        print("Session stopped.")
    except Exception as e:
        print(f"Failed with exception: {e}")

if __name__ == "__main__":
    asyncio.run(main())
