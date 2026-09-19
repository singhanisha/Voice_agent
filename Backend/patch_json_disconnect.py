import json

data = json.load(open('Updated_Real_Estate_Agent.json', encoding='utf-8'))

# Add a universal disconnect node if it doesn't exist
has_disconnect = False
for node in data['conversationFlow']['nodes']:
    if node['id'] == 'node-universal-disconnect':
        has_disconnect = True
        break

if not has_disconnect:
    data['conversationFlow']['nodes'].append({
        "name": "Universal Disconnect",
        "id": "node-universal-disconnect",
        "type": "conversation",
        "response": "Alright, I understand. I'll let you go now. Have a great day ahead!",
        "edges": []
    })

json.dump(data, open('Updated_Real_Estate_Agent.json', 'w', encoding='utf-8'), indent=4, ensure_ascii=False)
print("JSON DISCONNECT NODE PATCHED")
