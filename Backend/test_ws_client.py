import asyncio
import websockets
import json

async def test_ws():
    uri = "ws://localhost:8000/api/voice-demo?agentId=default&clientId=test&leadName=Test"
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected!")
            await websocket.send(json.dumps({"type": "mic_ready", "sampleRate": 16000}))
            
            while True:
                try:
                    response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    if isinstance(response, str):
                        print(f"< {response[:200]}")
                    else:
                        print(f"< [Binary Data: {len(response)} bytes]")
                except asyncio.TimeoutError:
                    print("Timeout waiting for response")
                    break
    except Exception as e:
        print(f"Error connecting: {e}")

if __name__ == "__main__":
    asyncio.run(test_ws())
