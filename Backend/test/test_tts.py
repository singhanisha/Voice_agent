import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
from tts import generate_speech_stream

def generate_speech(text):
    return b"".join(generate_speech_stream(text))

# Set up logging to see what TTS is doing
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def test_tts():
    # Test cases: English, Hindi, and Hinglish (mixed)
    test_cases = {
        "en": "Hello, this is a test of the text to speech engine.",
        "hi": "नमस्ते, यह वॉइस इंजन का एक परीक्षण है।",
        "mixed": "कल शाम मैंने बहुत दिनों बाद my old friends से बात की। We talked for hours, old memories refresh हो गईं। सच में, true friendship की कोई कीमत नहीं होती। It was the best part of my week!" 
    }

    print("\n" + "="*40)
    print("🚀  Starting TTS Test  🚀")
    print("="*40 + "\n")
    
    for lang, text in test_cases.items():
        print(f"🔹 Generating {lang.upper()} speech...")
        print(f"👉 Text: '{text}'")
        
        # This will load the model on the first call (may take ~30s to download)
        audio_bytes = generate_speech(text)
        
        if audio_bytes and len(audio_bytes) > 0:
            filename = f"test_{lang}.wav"
            with open(filename, "wb") as f:
                f.write(audio_bytes)
            
            size_kb = len(audio_bytes) / 1024
            print(f"✅ SUCCESS! Saved to {filename} ({size_kb:.1f} KB)")
        else:
            print(f"❌ FAILED to generate audio for {lang}")
        print("-" * 20)

    print("\n" + "="*40)
    print("🎉  TTS Test Complete  🎉")
    print("="*40 + "\n")

if __name__ == "__main__":
    test_tts()
