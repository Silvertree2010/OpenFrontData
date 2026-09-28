"""Partienliste, Deckel und Val-Trennung auf einem synthetischen Pool (CPU, Sekunden).

Prüft:
  1. waehle_partien.py: Scan liest Samples und Spielerzahl, die Filter greifen, die
     Hochrechnung des Deckels stimmt.
  2. daten.finde_partien beachtet die Liste.
  3. Der Strom liefert jedes Sample höchstens einmal; ohne Deckel genau einmal. Mit Deckel
     gilt emittiert + Deckel-verworfen + verworfen = Samples der Partien.
  4. Der Deckel behält im Mittel N Samples je Partie und verteilt sie über die ganze
     Partie (nicht die ersten N), zieht je Epoche neu und je Epoche reproduzierbar.
  5. Kein Val-Sample landet im Trainingsstrom, egal welche Liste gilt.

    python trainer/tests/deckeltest.py
"""
from __future__ import annotations

import math
import os
import random
import sys
import tempfile
import types
from collections import Counter

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HIER))
sys.path.insert(0, HIER)

import daten as D  # noqa: E402
import reader as R  # noqa: E402
import waehle_partien as W  # noqa: E402
from trockenlauf import gids, schreibe_partie  # noqa: E402

fehler = []


def pruefe(name, bed, info=""):
    print(f"{'ok  ' if bed else 'FEHL'} {name} {info}")
    if not bed:
        fehler.append(name)


tmp = tempfile.mkdtemp(prefix="deckel-")
out = os.path.join(tmp, "s0")
os.makedirs(out)
rng = random.Random(7)
val_g, train_g = gids(rng, 4, 14)
GROESSEN = [40, 90, 260, 700]
groessen = {}
for i, g in enumerate(val_g + train_g):
    n = GROESSEN[i % len(GROESSEN)]
    schreibe_partie(out, g, n, rng)
    groessen[g] = n
print(f"[pool] {len(groessen)} Partien, {sum(groessen.values())} Samples in {tmp}")

# 1. waehle_partien
cache = os.path.join(tmp, "pool.jsonl")
W.scan([out], cache)
recs = W.lade(cache)
pruefe("Scan findet alle Partien", len(recs) == len(groessen))
pruefe("Scan liest Samples, Spieler und Modus",
       all(r["samples"] == groessen[r["gid"]] and r["spieler"] == 3 and r["modus"] == "Free For All" for r in recs))
pruefe("Scan liest die Aktionstypen", all(sum(r["intents"].values()) == r["samples"] for r in recs))
opt = types.SimpleNamespace(modus="Free For All", min_samples=100, min_spieler=3, spieler_art="alle",
                            nur_ungeschnitten=False)
gef = W.filtern(recs, opt)
pruefe("Filter Mindest-Samples", {r["gid"] for r in gef} == {g for g, n in groessen.items() if n >= 100})
pruefe("Filter Spielerzahl", W.filtern(recs, types.SimpleNamespace(**{**vars(opt), "min_spieler": 4})) == [])
pruefe("Filter Modus", W.filtern(recs, types.SimpleNamespace(**{**vars(opt), "modus": "Team"})) == [])
DECKEL = 150
v = W.verteilung(gef, DECKEL)
pruefe("Hochrechnung Deckel", v["samples_deckel"] == sum(min(groessen[r["gid"]], DECKEL) for r in gef),
       f"{v['samples_deckel']}")

# 2. Liste im Lader
liste = {r["gid"] for r in gef}
alle = D.finde_partien([out], nur=liste)
pruefe("finde_partien beachtet die Liste", set(alle) == liste)
pruefe("ohne Liste alle Partien", set(D.finde_partien([out])) == set(groessen))


def lauf(deckel, epoche, partien):
    o = D.LaderOpt(deckel=deckel, epoche=epoche)
    ds = D.Strom([(g, alle[g][0]) for g in partien], batch=16, puffer=64, offen=2, seed=1, opt=o)
    ids, z = [], Counter()
    for b in ds:
        z.update(b["zaehler"])
        if not b.get("leer"):
            ids += [tuple(x) for x in b["ids"]]
    return ids, z


teil = sorted(liste)
gesamt = sum(groessen[g] for g in teil)
ids0, z0 = lauf(0, 0, teil)
verworfen0 = sum(val for k, val in z0.items() if k.startswith("verworfen"))
pruefe("ohne Deckel: jedes Sample genau einmal",
       len(ids0) == len(set(ids0)) and len(ids0) + verworfen0 == gesamt, f"{len(ids0)} + {verworfen0} von {gesamt}")
ids1, z1 = lauf(DECKEL, 0, teil)
verworfen1 = sum(val for k, val in z1.items() if k.startswith("verworfen") and k != "deckel_verworfen")
pruefe("mit Deckel: kein Sample doppelt", len(ids1) == len(set(ids1)))
pruefe("mit Deckel: emittiert + Deckel + verworfen = Samples",
       len(ids1) + z1["deckel_verworfen"] + verworfen1 == gesamt,
       f"{len(ids1)} + {z1['deckel_verworfen']} + {verworfen1} von {gesamt}")
pruefe("mit Deckel: Teilmenge des vollen Laufs", set(ids1) <= set(ids0))

# 3. Menge und Streuung je Partie
je0, je1 = Counter(g for g, _ in ids0), Counter(g for g, _ in ids1)
klein = [g for g in teil if groessen[g] <= DECKEL]
gross = [g for g in teil if groessen[g] > DECKEL]
pruefe("kleine Partien unberührt", all(je1[g] == je0[g] for g in klein))
schlecht = []
for g in gross:
    p = DECKEL / groessen[g]
    sd = math.sqrt(groessen[g] * p * (1 - p))
    if abs(je1[g] / max(je0[g], 1) * groessen[g] - DECKEL) > 5 * sd:
        schlecht.append((g, je1[g], groessen[g]))
pruefe("grosse Partien auf ~N gedeckelt", not schlecht, str(schlecht))
gr = max(gross, key=lambda g: groessen[g])
idx = sorted(i for gg, i in ids1 if gg == gr)
mitte = sum(idx) / len(idx) / (groessen[gr] - 1)
pruefe("über die ganze Partie verteilt", 0.42 < mitte < 0.58 and max(idx) > 0.9 * (groessen[gr] - 1),
       f"Mittelwert der Position {mitte:.2f}, letzter Index {max(idx)} von {groessen[gr] - 1}")

# 4. Ziehung je Epoche
ids2, _ = lauf(DECKEL, 1, teil)
ids3, _ = lauf(DECKEL, 0, teil)
pruefe("je Epoche eine andere Teilmenge", set(ids1) != set(ids2))
pruefe("gleiche Epoche, gleiche Teilmenge", set(ids1) == set(ids3))
pruefe("zweite Epoche zeigt Neues", len(set(ids2) - set(ids1)) > 0)

# 5. Val-Trennung (hashbasiert, unabhängig von der Liste)
train_liste = [g for g in teil if not R.is_val(g)]
val_liste = [g for g in teil if R.is_val(g)]
idsT, _ = lauf(DECKEL, 0, train_liste)
pruefe("kein Val-Sample im Trainingsstrom", not any(R.is_val(g) for g, _ in idsT))
pruefe("Liste trennt train und val disjunkt", set(train_liste).isdisjoint(val_liste) and val_liste)
kleinere = sorted(liste)[: len(liste) // 2]
pruefe("andere Liste, gleiche Val-Zuordnung",
       {g for g in kleinere if R.is_val(g)} == set(kleinere) & set(val_liste))

# 6. Derselbe Nachweis über den DataLoader mit zwei Worker-Prozessen (wie tests/ladertest.py)
from torch.utils.data import DataLoader  # noqa: E402

ds = D.Strom([(g, alle[g][0]) for g in teil], 16, 64, 2, 1, D.LaderOpt(deckel=DECKEL, epoche=0))
idsW, zW = [], Counter()
for b in DataLoader(ds, batch_size=None, num_workers=2, multiprocessing_context="fork"):  # wie auf arch
    zW.update(b["zaehler"])
    if not b.get("leer"):
        idsW += [tuple(x) for x in b["ids"]]
verworfenW = sum(val for k, val in zW.items() if k.startswith("verworfen") and k != "deckel_verworfen")
pruefe("mit Workern: jedes Sample höchstens einmal", len(idsW) == len(set(idsW)))
pruefe("mit Workern: emittiert + Deckel + verworfen = Samples",
       len(idsW) + zW["deckel_verworfen"] + verworfenW == gesamt,
       f"{len(idsW)} + {zW['deckel_verworfen']} + {verworfenW} von {gesamt}")

print("\n" + ("ALLES OK" if not fehler else f"{len(fehler)} FEHLER: {fehler}"))
sys.exit(1 if fehler else 0)
