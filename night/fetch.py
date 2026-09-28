#!/usr/bin/env python3
"""
Nachtlaeufer. Holt volle Spielrecords, eine Datei pro Partie.
Absichtlich simpel: keine Datenbank, keine Abhaengigkeiten ausser requests.
Was schon da ist, wird uebersprungen -- ein Neustart kostet nichts.

  fetch.py ids.txt out/ [--rps 0.7]
"""
import os, sys, time, json
import urllib.request, urllib.error

API = "https://api.openfront.io"
UA = "openfront-ai-research/0.1 (self-play RL research)"

ids_file, out_dir = sys.argv[1], sys.argv[2]
rps = float(sys.argv[sys.argv.index("--rps") + 1]) if "--rps" in sys.argv else 0.7
gap = 1.0 / rps
os.makedirs(out_dir, exist_ok=True)

ids = [l.strip() for l in open(ids_file) if l.strip()]
done = skip = fail = 0
t0 = time.time()
backoff = 2.0

for i, gid in enumerate(ids, 1):
    path = os.path.join(out_dir, gid[:2], gid + ".json")
    if os.path.exists(path):
        skip += 1
        continue
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ok = False
    for attempt in range(5):
        try:
            req = urllib.request.Request(f"{API}/public/game/{gid}", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
            json.loads(body)                      # nur vollstaendige Dateien behalten
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(body)
            os.replace(tmp, path)
            ok = True
            backoff = 2.0
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                break
            print(f"  {gid}: HTTP {e.code}, warte {backoff:.0f}s", flush=True)
            time.sleep(backoff); backoff = min(backoff * 2, 300)
        except Exception as e:
            print(f"  {gid}: {type(e).__name__}, warte {backoff:.0f}s", flush=True)
            time.sleep(backoff); backoff = min(backoff * 2, 300)
    done += ok
    fail += (not ok)
    if i % 100 == 0:
        el = time.time() - t0
        rate = done / max(el, 1)
        left = (len(ids) - i) / max(rate, 0.01) / 3600
        print(f"{i}/{len(ids)}  geholt={done} uebersprungen={skip} fehlend={fail} "
              f"| {rate:.2f}/s | noch ~{left:.1f}h", flush=True)
    time.sleep(gap)

print(f"FERTIG geholt={done} uebersprungen={skip} fehlend={fail} "
      f"in {(time.time()-t0)/3600:.1f}h", flush=True)
