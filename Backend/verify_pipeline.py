import asyncio
import os
import sys
from groq import Groq
import edge_tts
from dotenv import load_dotenv
from llm import config as cfg

# Fix for Windows terminal emoji printing
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

async def test_groq():
    print("Testing Groq Cloud Connection...")
    if not GROQ_API_KEY:
        print("❌ Error: GROQ_API_KEY not found in .env")
        return False
    try:
        client = Groq(api_key=GROQ_API_KEY)
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": "Say 'Groq is ready'"}],
            model=cfg.MODEL_NAME,
        )
        print(f"✅ Groq Success: {chat_completion.choices[0].message.content}")
        return True
    except Exception as e:
        print(f"❌ Groq Failed: {e}")
        return False

async def test_edge_tts():
    print("Testing Edge-TTS Connection...")
    try:
        text = "Hello, this is a test of the Neerja voice."
        communicate = edge_tts.Communicate(text, "en-IN-NeerjaNeural")
        mp3_data = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3_data.extend(chunk["data"])
        
        if len(mp3_data) > 0:
            print(f"✅ Edge-TTS Success: Received {len(mp3_data)} bytes of audio.")
            print(f"⚠️ Note: Skipping PCM decoding check (miniaudio not installed).")
            return True
        else:
            print("❌ Edge-TTS Failed: No audio data received.")
            return False
    except Exception as e:
        print(f"❌ Edge-TTS Failed: {e}")
        return False

async def main():
    print("--- AI Voice Agent Pipeline Verification ---")
    g_ok = await test_groq()
    e_ok = await test_edge_tts()
    
    if g_ok and e_ok:
        print("\n🎉 ALL SYSTEMS GO! Your new laptop is ready for Phase 9 & 10.")
    else:
        print("\n⚠️ SOME SYSTEMS FAILED. Check your API keys and internet connection.")

if __name__ == "__main__":
    asyncio.run(main())
