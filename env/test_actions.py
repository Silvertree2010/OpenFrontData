"""Round-Trip-Test: decode(encode(intent)) == intent, über alle echten Züge.

Pro Zug wird ein konsistenter synthetischer Kontext gebaut (Empfänger steht in
der Gegnerliste, Einheit in der eigenen Liste, fester Nenner für die Menge),
sodass die kontextabhängigen Felder exakt zurückkommen müssen. Verlustbehaftete
Felder werden mit der richtigen Toleranz geprüft: Truppen/Gold innerhalb einer
Mengenklasse, Kachel in derselben Feinzelle.
"""
import glob, json, collections, sys
sys.path.insert(0, "env")
import actions as AC

try:
    from compression import zstd
    dec = zstd.decompress
except Exception:
    import zstandard
    dec = lambda b: zstandard.ZstdDecompressor().decompress(b)

W, H = 2000, 1000   # synthetische Kartenmaße (echte kommen aus obs.ts)

def ctx_for(i):
    c = AC.Context(map_w=W, map_h=H)
    ids = [v for k in ("recipient", "target", "requestor", "targetID")
           if isinstance((v := i.get(k)), str)]
    if ids:
        c.opp_ids = ["dummyA"] + ids + ["dummyB"]   # alle Empfänger auflösbar
    if i.get("unitId") is not None:  c.own_unit_ids = [999, int(i["unitId"]), 111]
    if i.get("unitID") is not None:  c.own_unit_ids = [999, int(i["unitID"]), 111]
    if i.get("unitIds"):             c.own_unit_ids = [999, int(i["unitIds"][0]), 111]
    if i.get("attackID"):            c.own_attack_ids = ["xA", i["attackID"], "xB"]
    # Nenner so, dass der Wert genau auf einer Bruchteil-Klasse (0.30) liegt
    tv = i.get("troops"); gv = i.get("gold")
    c.troops = (float(tv) / 0.30) if tv else 1.0
    c.gold = (float(gv) / 0.30) if gv else 1.0
    return c

def norm(intent):
    """Kanonische Form für den Vergleich: optionale Standardwerte entfernen."""
    d = dict(intent); d.pop("clientID", None)
    if d.get("amount") == 1: d.pop("amount", None)
    if d.get("rocketDirectionUp") is False: d.pop("rocketDirectionUp", None)
    if d.get("type") == "quick_chat": d.pop("target", None)  # optionaler 2. Zeiger, v1 ausgelassen
    return d

files = glob.glob("data/raw/**/*.zst", recursive=True)
total = ok = skipped = multi_warship = 0
fails = collections.Counter()
lossy_bad = collections.Counter()
per_type = collections.Counter()
seen_types = collections.Counter()

for f in files:
    d = json.loads(dec(open(f, "rb").read()))
    for t in d["turns"]:
        for i in t.get("intents", []):
            ty = i["type"]
            seen_types[ty] += 1
            if ty in AC.IGNORED_TYPES:
                skipped += 1; continue
            # Bekannte v1-Grenze: move_warship mit mehreren Schiffen ist mit
            # einem einzelnen Zeiger nicht darstellbar. Separat zählen.
            if ty == "move_warship" and len(i.get("unitIds", [])) > 1:
                multi_warship += 1; continue
            total += 1
            c = ctx_for(i)
            a = AC.encode(i, c)
            r = AC.decode(a, c)
            o, g = norm(i), norm(r)
            good = True
            for k, ov in o.items():
                gv = g.get(k)
                if k in ("troops", "gold"):
                    # innerhalb einer Mengenklasse?
                    denom = c.troops if k == "troops" else c.gold
                    be = AC.mag_encode(float(ov), denom)
                    bg = AC.mag_encode(float(gv), denom) if gv is not None else -9
                    if be != bg: good = False; lossy_bad[k] += 1
                elif k in ("tile", "dst"):
                    pass  # echte Kartenmaße fehlen hier → separat unten getestet
                elif ov != gv:
                    good = False; fails[f"{ty}.{k}"] += 1
            # kein zusätzliches Feld erfunden?
            for k in g:
                if k not in o: good = False; fails[f"{ty}.+{k}"] += 1
            ok += good
            per_type[ty] += good

print(f"Züge gesamt: {total:,}   bestanden: {ok:,}   ({100*ok/max(total,1):.2f}%)")
print(f"übersprungen (mark_disconnected u.ä.): {skipped:,}")
print(f"v1-Grenze (move_warship mit mehreren Schiffen): {multi_warship:,}")
print("\nPro Typ (bestanden / gesehen):")
for ty in sorted(seen_types, key=lambda x: -seen_types[x]):
    if ty in AC.IGNORED_TYPES: continue
    n = seen_types[ty]; p = per_type[ty]
    flag = "" if p == n else "  ← FEHLER"
    print(f"  {ty:<20} {p:>7,} / {n:>7,}{flag}")
if fails:
    print("\nStruktur-Fehler:")
    for k, v in fails.most_common(15): print(f"  {k}: {v}")
if lossy_bad:
    print("\nVerlust-Fehler (sollten 0 sein, da Nenner auf Klasse gelegt):")
    for k, v in lossy_bad.items(): print(f"  {k}: {v}")
print("\nKopfgrößen fürs Netz:", AC.HEAD_SIZES)

# ── Kachel-Geometrie separat, mit KORREKTEN Maßen mehrerer echter Kartengrößen
print("\nKachel-Round-Trip (encode→decode→encode landet in derselben Zelle):")
for (mw, mh) in [(674, 672), (1364, 1324), (2000, 1500), (2424, 2424), (600, 400)]:
    import random
    random.seed(1); n = 20000; incell = 0
    for _ in range(n):
        tile = random.randrange(mw * mh)
        c, f = AC.tile_encode(tile, mw, mh)
        c2, f2 = AC.tile_encode(AC.tile_decode(c, f, mw, mh), mw, mh)
        incell += (c, f) == (c2, f2)
    feiner = AC.GW > mw or AC.GH > mh
    note = "  (Raster feiner als Karte — sub-Kachel, erwartet <100%)" if feiner else ""
    print(f"  {mw}×{mh}: {100*incell/n:5.1f}% in-Zelle{note}")
