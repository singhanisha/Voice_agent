import os
import requests
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".env"
)

load_dotenv(ENV_PATH)

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

if not NVIDIA_API_KEY:
    raise RuntimeError(
        f"NVIDIA_API_KEY not found.\n"
        f"Expected .env at: {ENV_PATH}"
    )

print("\n🎙️ NVIDIA MAGPIE HOSTED TTS TEST")
print("=" * 60)

# Don't print the actual key
print("API Key loaded :", "YES")
print("API Key prefix :", NVIDIA_API_KEY[:10] + "...")


BASE_URL = (
    "https://877104f7-e885-42b9-8de8-f6e4c6303969"
    ".invocation.api.nvcf.nvidia.com"
)

TTS_URL = f"{BASE_URL}/v1/audio/synthesize"


VOICES = {
    "hindi_female": {
        "language": "hi-IN",
        "voice": "Magpie-Multilingual.HI-IN.Sofia",
        "text": "नमस्ते, मैं आपकी सहायता करने के लिए तैयार हूँ। अगर आप अपने लिए नया घर, फ्लैट, प्लॉट या कमर्शियल प्रॉपर्टी खरीदने या किराए पर लेने की योजना बना रहे हैं, तो मैं आपके बजट और जरूरत के अनुसार सही प्रॉपर्टी खोजने में आपकी मदद कर सकती हूँ।",
        "filename": "magpie_hindi_female.wav",
    },

    "hindi_male": {
        "language": "hi-IN",
        "voice": "Magpie-Multilingual.HI-IN.Pascal",
        "text": "नमस्ते, मैं आपकी सहायता करने के लिए तैयार हूँ। अगर आप अपने लिए नया घर, फ्लैट, प्लॉट या कमर्शियल प्रॉपर्टी खरीदने या किराए पर लेने की योजना बना रहे हैं, तो मैं आपके बजट और जरूरत के अनुसार सही प्रॉपर्टी खोजने में आपकी मदद कर सकता हूँ।",
        "filename": "magpie_hindi_male.wav",
    },

    "english_female": {
        "language": "en-US",
        "voice": "Magpie-Multilingual.EN-US.Mia",
        "text": "Hello, I am ready to help you. If you are looking to buy or rent a new home, apartment, plot, or commercial property, I can help you find suitable properties based on your budget, preferred location, and specific requirements.",
        "filename": "magpie_english_female.wav",
    },

    "english_male": {
        "language": "en-US",
        "voice": "Magpie-Multilingual.EN-US.Ray",
        "text": "Hello, I am ready to help you. If you are looking to buy or rent a new home, apartment, plot, or commercial property, I can help you find suitable properties based on your budget, preferred location, and specific requirements.",
        "filename": "magpie_english_male.wav",
    },
}


headers = {
    "Authorization": f"Bearer {NVIDIA_API_KEY}",
    "Accept": "audio/wav",
}


for name, config in VOICES.items():

    print("\n" + "=" * 60)
    print(f"Generating: {name}")
    print(f"Language : {config['language']}")
    print(f"Voice    : {config['voice']}")
    print("=" * 60)

    form_data = {
        "text": (None, config["text"]),
        "language": (None, config["language"]),
        "voice": (None, config["voice"]),
        "encoding": (None, "LINEAR_PCM"),
        "sample_rate_hz": (None, "22050"),
    }

    try:
        response = requests.post(
            TTS_URL,
            headers=headers,
            files=form_data,
            timeout=120,
        )

        print("HTTP Status:", response.status_code)

        if response.status_code == 200:

            output_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                config["filename"]
            )

            with open(output_path, "wb") as f:
                f.write(response.content)

            print("✅ Audio generated successfully!")
            print("Saved:", output_path)

        else:
            print("❌ TTS request failed")
            print("Response:", response.text)

    except Exception as e:
        print("❌ Request error:", str(e))


print("\n")
print("=" * 60)
print("🎉 TEST COMPLETED")
print("=" * 60)