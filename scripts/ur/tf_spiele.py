import json, time, urllib.request, urllib.error, os, sys
ids = [l.strip() for l in open("ur_ids.txt") if l.strip()]
aus = "ur_tf_spiele.jsonl"
fertig = set()
if os.path.exists(aus):
    fertig = {json.loads(l)["game_id"] for l in open(aus) if l.strip()}
f = open(aus, "a")
warte = 5.0
for i, g in enumerate(ids, 1):
    if g in fertig:
        continue
    for versuch in range(6):
        try:
            req = urllib.request.Request(f"https://trackerfront.com/api/public/games/{g}",
                                         headers={"User-Agent": "openfront-ai-research/0.1"})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
            f.write(json.dumps(d) + "\n"); f.flush(); warte = 5.0
            break
        except urllib.error.HTTPError as e:
            print(g, "HTTP", e.code, "warte", warte, flush=True)
            if e.code == 404: break
            time.sleep(warte); warte = min(warte * 2, 300)
        except Exception as e:
            print(g, type(e).__name__, flush=True); time.sleep(warte); warte = min(warte * 2, 300)
    if i % 50 == 0: print(i, flush=True)
    time.sleep(3.0)
print("FERTIG", flush=True)
