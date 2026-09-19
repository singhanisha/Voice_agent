import asyncio
import os
import json
import websockets
from dotenv import load_dotenv

async def test_cartesia():
    load_dotenv()
    url = "wss://api.cartesia.ai/tts/websocket"
    headers = {
        "X-API-Key": os.getenv("CARTESIA_API_KEY"),
        "Cartesia-Version": "2024-06-10"
    }
    
    async with websockets.connect(url, additional_headers=headers) as ws:
        req = {
            "model_id": "sonic-3.5",
            "transcript": "Hello",
            "voice": {"mode": "id", "id": "95d51f79-c397-46f9-b49a-23763d3eaa2d"},
            "context_id": "test",
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": 16000
            }
        }
        await ws.send(json.dumps(req))
        while True:
            msg = await ws.recv()
            if isinstance(msg, str):
                print("Received from Cartesia:", msg)
                break

if __name__ == "__main__":
    asyncio.run(test_cartesia())
