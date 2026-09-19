import asyncio
import websockets
import json

async def test_ws():
    uri = "ws://localhost:8000/api/voice-demo?agentId=default&clientId=realty&leadName=Test"
    try:
        async with websockets.connect(uri) as ws:
            print("Connected to Voice Demo WS")
            
            # Test ping/pong
            print("Sending binary ping...")
            await ws.send(b"ping")
            pong = await ws.recv()
            print(f"Received from server: {pong}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_ws())
