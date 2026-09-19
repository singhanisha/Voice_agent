import requests
import json
import time

BASE_URL = "http://localhost:3000/api"

def test_flow():
    print("--- Starting Backend Verification ---")
    
    # 1. Check Dashboard
    print("Testing /dashboard...")
    res = requests.get(f"{BASE_URL}/dashboard")
    print(f"Status: {res.status_code}, Data: {res.json()}")
    
    # 2. Create Agent
    print("\nTesting /agents (POST)...")
    agent_data = {
        "name": "Integration Test Agent",
        "voice": "ElevenLabs Priya",
        "language": "Hindi + English",
        "max_duration": 120,
        "provider": "Vapi.ai",
        "script": "You are a test agent...",
        "data_fields": ["Call Status", "Interested"]
    }
    res = requests.post(f"{BASE_URL}/agents", json=agent_data)
    print(f"Status: {res.status_code}, Data: {res.json()}")
    
    # 3. Upload Leads
    print("\nTesting /leads/upload...")
    campaign_id = f"test_camp_{int(time.time())}"
    leads_data = {
        "campaignId": campaign_id,
        "leads": [
            {"name": "Test User 1", "phone": "+91 00000 00001"},
            {"name": "Test User 2", "phone": "+91 00000 00002"}
        ]
    }
    res = requests.post(f"{BASE_URL}/leads/upload", json=leads_data)
    print(f"Status: {res.status_code}, Data: {res.json()}")
    
    # 4. Start Campaign
    print("\nTesting /campaigns/start...")
    res = requests.post(f"{BASE_URL}/campaigns/start", json={"campaignId": campaign_id})
    print(f"Status: {res.status_code}, Data: {res.json()}")
    
    # 5. Check Results (Poll a few times)
    print("\nPolling /campaigns/{id}/results...")
    for _ in range(3):
        res = requests.get(f"{BASE_URL}/campaigns/{campaign_id}/results")
        results = res.json()
        print(f"Results count: {len(results)}")
        if len(results) > 0:
            print(f"Latest Result: {results[-1]}")
        time.sleep(3)

    print("\n--- Verification Complete ---")

if __name__ == "__main__":
    try:
        test_flow()
    except Exception as e:
        print(f"Error during verification: {e}")
