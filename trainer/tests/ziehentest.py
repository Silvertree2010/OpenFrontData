"""Ziehen statt Argmax in der Spielregel: ändert sich die Typverteilung, bleibt alles legal?

Baut aus echten Samples die Anfragen des Viewers (wie tests/inftest.py) und entscheidet
sie mehrfach: einmal mit Argmax (Standard) und einmal mit Ziehen. Geprüft wird:
  1. Argmax ist deterministisch und unverändert gegenüber dem Stand ohne Ziehen.
  2. Ziehen erzeugt eine andere, breitere Verteilung der Aktionstypen.
  3. Jede räumliche Entscheidung nennt nur Zellen, deren legal-Bit für die Gruppe gesetzt
     ist; Einheit, Zielspieler und Intent-Felder bleiben gültig.
  4. Die Schwelle für Nichtstun wirkt unabhängig vom Ziehen (gleiche P(handeln), gleiche
     Nichtstun-Entscheidung bei hoher Schwelle).

    python trainer/tests/ziehentest.py --ckpt checkpoints/d0_rauch.pt --daten ~/of-mat2-out
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch  # noqa: E402

import daten as D  # noqa: E402
import netz_d0 as ND  # noqa: E402
import reader as R  # noqa: E402
import spielen as SP  # noqa: E402
import tabellen as T  # noqa: E402
import actions as AC  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--ckpt", required=True)
ap.add_argument("--daten", nargs="+", default=["~/of-mat2-out"])
ap.add_argument("--reputation", default=None)
ap.add_argument("--partien", type=int, default=3)
ap.add_argument("--je", type=int, default=8, help="Anfragen je Partie")
ap.add_argument("--wiederholungen", type=int, default=8, help="Ziehungen je Anfrage")
ap.add_argument("--temp", type=float, default=1.0)
ap.add_argument("--top-k", type=int, default=0)
ap.add_argument("--threads", type=int, default=2)
a = ap.parse_args()
torch.set_num_threads(a.threads)

ad = ND.D0Adapter()
ad.modul.load_state_dict(torch.load(a.ckpt, map_location="cpu", weights_only=True)["model"])
ad.modul.train()
rep = D._rep_tabelle(a.reputation)
argmax = SP.Entscheider(ad, "cpu", rep)                                   # Standard: wie bisher
ziehen = SP.Entscheider(ad, "cpu", rep, wahl=SP.Wahl(ziehen=True, temp=a.temp, top_k=a.top_k, saat=7))
fehler = []


def pruefe(name, bed, info=""):
    print(f"{'ok  ' if bed else 'FEHL'} {name} {info}")
    if not bed:
        fehler.append(name)


def anfrage(m, mblock, cblock):
    o = {"map_b64": base64.b64encode(D._zdec_n(mblock, R.MAP_BYTES)).decode(),
         "own": m["own"], "opps": m["opps"], "config": m["cfg"], "sid": m["sid"], "allies": m.get("allies", []),
         "ctx": {"mapW": m["mapW"], "mapH": m["mapH"], "troops": m["troops"], "gold": m["gold"],
                 "oppIds": m["oppIds"], "ownUnitIds": m["ownUnitIds"], "ownAttackIds": m["ownAttackIds"],
                 "oppSids": [x.get("id") for x in m["opps"]]}}
    if cblock is not None:
        o["cells_b64"] = base64.b64encode(D._zdec_n(cblock, R.CELL_BYTES)).decode()
    return json.loads(json.dumps(o))


def legal_ok(o, res):
    """Jede genannte Zelle muss das legal-Bit der Gruppe tragen."""
    if not res.get("kandidaten"):
        return True
    roh = base64.b64decode(o["cells_b64"])
    legal = roh[3 * 16200:]
    bit = T.BIT[res["gruppe"]]
    return all((legal[c] >> bit) & 1 for c in res["kandidaten"])


def intent_ok(res):
    it = res.get("intent") or {}
    if it.get("type") == "no_op":
        return True
    if res.get("einheit") is not None and res["einheit"] not in AC.UNIT_TYPES:
        return False
    w = res.get("wahl", {})
    if "target" in w and not (-1 <= w["target"] < AC.NUM_TARGET):
        return False
    return "type" in it


alle = D.finde_partien(a.daten)
val = sorted(g for g in alle if R.is_val(g))[:a.partien]
rng = random.Random(3)
anfragen = []
for gid in val:
    d = alle[gid][0]
    G = R.Game(d, gid)
    meta, mbl, cbl = G.meta(), list(G.map_blocks()), list(G.cell_blocks())
    raum = [i for i, m in enumerate(meta) if m.get("cell", -1) >= 0]
    for i in rng.sample(raum, min(a.je, len(raum))):
        anfragen.append(anfrage(meta[i], mbl[i], cbl[meta[i]["cell"]]))
print(f"[test] {len(anfragen)} Anfragen aus {len(val)} Val-Partien, Checkpoint {os.path.basename(a.ckpt)}")

v_arg, v_zieh = Counter(), Counter()
legal_fehler, intent_fehler, p_abweichung = 0, 0, 0.0
deterministisch = True
for o in anfragen:
    r1 = argmax.entscheide(o, 0.0)
    r2 = argmax.entscheide(o, 0.0)
    deterministisch &= r1["atype"] == r2["atype"] and r1.get("kandidaten") == r2.get("kandidaten")
    v_arg[r1["atype"]] += 1
    legal_fehler += not legal_ok(o, r1)
    intent_fehler += not intent_ok(r1)
    for _ in range(a.wiederholungen):
        rz = ziehen.entscheide(o, 0.0)
        v_zieh[rz["atype"]] += 1
        legal_fehler += not legal_ok(o, rz)
        intent_fehler += not intent_ok(rz)
        p_abweichung = max(p_abweichung, abs(rz["p_handeln"] - r1["p_handeln"]))

pruefe("Argmax bleibt deterministisch", deterministisch)
pruefe("nur legale Zellen in den Kandidaten", legal_fehler == 0, f"({legal_fehler} Verstösse)")
pruefe("Intent-Felder gültig", intent_fehler == 0, f"({intent_fehler} ungültig)")
pruefe("P(handeln) unverändert vom Ziehen", p_abweichung < 1e-6, f"(max. {p_abweichung:.2e})")
pruefe("Ziehen verteilt breiter", len(v_zieh) > len(v_arg), f"argmax {len(v_arg)} Typen, ziehen {len(v_zieh)}")

# Schwelle wirkt unabhängig vom Ziehen
hoch_a = [argmax.entscheide(o, 1.1)["atype"] for o in anfragen[:5]]
hoch_z = [ziehen.entscheide(o, 1.1)["atype"] for o in anfragen[:5]]
pruefe("hohe Schwelle: beide Nichtstun", set(hoch_a) == {"NO_OP"} and set(hoch_z) == {"NO_OP"})

n_z = sum(v_zieh.values())
print(f"Argmax : {dict(v_arg.most_common())}")
print(f"Ziehen : " + ", ".join(f"{k} {v / n_z:.0%}" for k, v in v_zieh.most_common(8)))
print("\n" + ("ALLES OK" if not fehler else f"{len(fehler)} FEHLER: {fehler}"))
sys.exit(1 if fehler else 0)
