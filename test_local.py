import json
import urllib.request
from pathlib import Path

BASE_URL = "http://127.0.0.1:8080"
DATA_DIR = Path("expanded") if Path("expanded").exists() else Path("dataset")

def post(path, body):
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def get(path):
    with urllib.request.urlopen(f"{BASE_URL}{path}") as resp:
        return json.loads(resp.read().decode("utf-8"))

def main():
    print("--- 1. Healthz ---")
    print(get("/v1/healthz"))

    print("\n--- 2. Pushing Categories ---")
    cat_dir = DATA_DIR / "categories"
    for cat_file in cat_dir.glob("*.json"):
        with open(cat_file) as f:
            cat_data = json.load(f)
        slug = cat_data.get("slug", cat_file.stem)
        res = post("/v1/context", {
            "scope": "category",
            "context_id": slug,
            "version": 1,
            "payload": cat_data
        })
        print(f"Pushed category/{slug}: {res}")

    print("\n--- 3. Pushing Merchants ---")
    m_dir = DATA_DIR / "merchants" if (DATA_DIR / "merchants").exists() else DATA_DIR
    if (DATA_DIR / "merchants").exists():
        for m_file in list(m_dir.glob("*.json"))[:10]:
            with open(m_file) as f:
                m_data = json.load(f)
            mid = m_data["merchant_id"]
            res = post("/v1/context", {
                "scope": "merchant",
                "context_id": mid,
                "version": 1,
                "payload": m_data
            })
            print(f"Pushed merchant/{mid[:20]}: {res}")
    else:
        with open(DATA_DIR / "merchants_seed.json") as f:
            m_seeds = json.load(f)["merchants"]
        for m_data in m_seeds[:5]:
            mid = m_data["merchant_id"]
            res = post("/v1/context", {
                "scope": "merchant",
                "context_id": mid,
                "version": 1,
                "payload": m_data
            })
            print(f"Pushed merchant/{mid[:20]}: {res}")

    print("\n--- 4. Pushing Triggers ---")
    t_dir = DATA_DIR / "triggers" if (DATA_DIR / "triggers").exists() else DATA_DIR
    trig_ids = []
    if (DATA_DIR / "triggers").exists():
        for t_file in list(t_dir.glob("*.json"))[:10]:
            with open(t_file) as f:
                t_data = json.load(f)
            tid = t_data["id"]
            trig_ids.append(tid)
            res = post("/v1/context", {
                "scope": "trigger",
                "context_id": tid,
                "version": 1,
                "payload": t_data
            })
            print(f"Pushed trigger/{tid[:25]}: {res}")
    else:
        with open(DATA_DIR / "triggers_seed.json") as f:
            t_seeds = json.load(f)["triggers"]
        for t_data in t_seeds[:5]:
            tid = t_data["id"]
            trig_ids.append(tid)
            res = post("/v1/context", {
                "scope": "trigger",
                "context_id": tid,
                "version": 1,
                "payload": t_data
            })
            print(f"Pushed trigger/{tid[:25]}: {res}")

    print("\n--- 5. Healthz after push ---")
    print(get("/v1/healthz"))

    print("\n--- 6. Tick ---")
    tick_res = post("/v1/tick", {
        "now": "2026-09-27T12:00:00Z",
        "available_triggers": trig_ids
    })
    print(f"Actions generated: {len(tick_res.get('actions', []))}")
    for act in tick_res.get("actions", []):
        print(f"\n  [Trigger: {act['trigger_id']}]")
        print(f"  Send As: {act['send_as']}")
        print(f"  CTA: {act['cta']}")
        print(f"  Body: {act['body'].encode('ascii', 'replace').decode('ascii')}")
        print(f"  Rationale: {act['rationale'].encode('ascii', 'replace').decode('ascii')}")

    print("\n--- 7. Reply (Simulated Turns) ---")
    if tick_res.get("actions"):
        first_act = tick_res["actions"][0]
        cid = first_act["conversation_id"]
        mid = first_act["merchant_id"]

        print("\nTurn A: Merchant says 'Yes please send details'")
        r1 = post("/v1/reply", {
            "conversation_id": cid,
            "merchant_id": mid,
            "from_role": "merchant",
            "message": "Yes please send details",
            "turn_number": 2
        })
        print(f"Bot reply: {json.dumps(r1, ensure_ascii=True)}")

        print("\nTurn B: Merchant auto-reply")
        r2 = post("/v1/reply", {
            "conversation_id": "conv_test_auto",
            "merchant_id": mid,
            "from_role": "merchant",
            "message": "Thank you for contacting us! Our team will respond shortly.",
            "turn_number": 2
        })
        print(f"Bot reply to auto-reply: {json.dumps(r2, ensure_ascii=True)}")

        print("\nTurn C: Merchant says 'Stop messaging me'")
        r3 = post("/v1/reply", {
            "conversation_id": "conv_test_hostile",
            "merchant_id": mid,
            "from_role": "merchant",
            "message": "Stop messaging me. This is useless spam.",
            "turn_number": 2
        })
        print(f"Bot reply to hostile: {json.dumps(r3, ensure_ascii=True)}")

if __name__ == "__main__":
    main()
