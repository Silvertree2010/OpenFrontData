"""
Trainings-Loader — liest Materializer-Shards und liefert fertige Samples.

Pro Sample: Karten-Tensor (18×90×180 float, dequantisiert), own-Vektor, opp-Matrix
+ Maske (featurize.py), und das Label (actions.encode auf dem gespeicherten Kontext).
Reputation (§F) wird hier je Gegner nachgeschlagen (data/reputation.json) und als
Prior in die opp-Features gegeben — Fallback 0.5 fuer Unbekannte.

Beobachtung + Label werden erst HIER erzeugt (nicht beim Materialisieren), damit
Feature-/Label-Aenderungen keine Neu-Extraktion brauchen.
"""
from __future__ import annotations
import json, struct, sys, os
from compression import zstd
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))
import actions as AC
import featurize as F

MAPLEN = 18 * 90 * 180
REPUTATION = {}

def load_reputation(path):
    global REPUTATION
    if os.path.exists(path):
        REPUTATION = json.load(open(path))
    return len(REPUTATION)

def _rep_for(o):
    key = f"{o.get('user') or '?'}\x1f{o.get('clan') or ''}"
    return REPUTATION.get(key, 0.5)

def load_game(shard_dir, gid):
    """Generator: liefert je Sample dict{map, own, opp, mask, label(dict), atype}."""
    meta_path = os.path.join(shard_dir, f"{gid}.meta.zst")
    maps_path = os.path.join(shard_dir, f"{gid}.maps")
    lines = zstd.decompress(open(meta_path, "rb").read()).decode().split("\n")
    with open(maps_path, "rb") as mf:
        for line in lines:
            if not line:
                continue
            s = json.loads(line)
            # --- Karte lesen (laengen-praefigierter zstd-Block) ---
            ln = struct.unpack("<I", mf.read(4))[0]
            block = zstd.decompress(mf.read(ln))
            u8 = np.frombuffer(block, dtype=np.uint8)
            assert u8.size == MAPLEN, f"{u8.size} != {MAPLEN}"
            mp = (u8.astype(np.float32) / 127.5) - 1.0        # dequantisieren → [-1,1]
            mp = mp.reshape(18, 90, 180)
            # --- Label: actions.encode auf gespeichertem Kontext ---
            ctx = AC.Context(map_w=s["mapW"], map_h=s["mapH"], troops=s["troops"], gold=s["gold"],
                             opp_ids=s["oppIds"], own_unit_ids=s["ownUnitIds"], own_attack_ids=s["ownAttackIds"])
            act = AC.encode(s["intent"], ctx)
            # --- obs-Features (Reputation je Gegner injizieren) ---
            opps = s["opps"]
            for o in opps:
                o["_rep"] = _rep_for(o)
            own_vec = F.featurize_own(s["own"])
            opp_mat, mask = F.featurize_opps(opps)
            # Label als {head:int} fuer aktive Koepfe
            label = {"atype": int(act.atype)}
            for h in AC.HEAD_SCHEMA.get(AC.A(act.atype), ()):
                v = getattr(act, h)
                if v is not None and v >= 0:      # UNRESOLVED (-1) → nicht supervidieren
                    label[h] = int(v)
            yield {"map": mp, "own": np.array(own_vec, np.float32),
                   "opp": np.array(opp_mat, np.float32), "mask": np.array(mask, bool),
                   "label": label, "atype": int(act.atype),
                   "win": float(s.get("win", 0))}   # Ziel fuer den Wert-Kopf


if __name__ == "__main__":
    # Verifikation: ein Shard laden, Formen + Label-Sauberkeit pruefen
    shard_dir, gid = sys.argv[1], sys.argv[2]
    reppath = os.path.join(os.path.dirname(__file__), "..", "data", "reputation.json")
    print("Reputation geladen:", load_reputation(reppath), "Spieler")
    n = 0; clean = 0; unresolved_heads = 0
    mp_min, mp_max = 9, -9
    import collections
    atypes = collections.Counter()
    for s in load_game(shard_dir, gid):
        n += 1
        assert s["map"].shape == (18, 90, 180)
        assert s["own"].shape == (F.OWN_DIM,)
        assert s["opp"].shape == (F.MAX_OPP, F.OPP_DIM)
        mp_min = min(mp_min, float(s["map"].min())); mp_max = max(mp_max, float(s["map"].max()))
        atypes[AC.A(s["atype"]).name] += 1
        # sauber = alle aktiven Koepfe des Typs haben ein Label (kein UNRESOLVED gedroppt)
        need = set(AC.HEAD_SCHEMA.get(AC.A(s["atype"]), ()))
        got = set(s["label"].keys()) - {"atype"}
        if need == got: clean += 1
        else: unresolved_heads += 1
    print(f"Samples: {n}")
    print(f"  Karte-Wertebereich: [{mp_min:.2f}, {mp_max:.2f}] (soll ~[-1,1])")
    print(f"  vollstaendige Labels: {clean} ({100*clean/max(n,1):.1f}%), mit gedropptem Kopf: {unresolved_heads}")
    print(f"  own={F.OWN_DIM} opp={F.OPP_DIM}  Aktionstypen: {dict(atypes.most_common(6))}")
