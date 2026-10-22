import json

with open("data/supervisor.json") as f:
    s = json.load(f)

print("State:", s.get("state"))
print("Delivery state:", s.get("delivery_state"))
print("Components:", s.get("components"))
print("Services:", s.get("services"))
print("Stability audit:", s.get("stability_audit"))
