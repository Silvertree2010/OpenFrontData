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

def dequantize(u8):
    """uint8-Block → float32-Karte (18,90,180) in [-1,1]. Umkehr der Quantisierung
    im Materializer. Einmal zentral, damit Loader und Verifikation identisch sind."""
    mp = (u8.astype(np.float32) / 127.5) - 1.0
    return mp.reshape(18, 90, 180)


# ── Spiel-Config (Modifier) je gameID aus den behaltenen Records, gecacht. ──
# Back-fill zur Trainingszeit: die Shards brauchen die Config NICHT einzubetten,
# wir lesen sie aus dem Original-Record (records_dirs). Fehlt der Record -> neutral.
CONFIG_CACHE = {}

def _find_record(gid, records_dirs):
    # Zusatz-Shards eines Nichtstun-Laufs heissen <gid>.noop — der Original-Record
    # (fuer den Modifier-Vektor) liegt unter <gid>.
    gid = gid[:-5] if gid.endswith(".noop") else gid
    for d in records_dirs or []:
        for cand in (os.path.join(d, gid[:2], gid + ".json"), os.path.join(d, gid + ".json")):
            if os.path.exists(cand):
                return cand
    return None

def config_vec_for(gid, records_dirs):
    if gid in CONFIG_CACHE:
        return CONFIG_CACHE[gid]
    cfg = {}
    p = _find_record(gid, records_dirs)
    if p:
        try:
            cfg = json.load(open(p)).get("info", {}).get("config", {})
        except Exception:
            cfg = {}
    v = np.array(F.featurize_config(cfg), np.float32)
    CONFIG_CACHE[gid] = v
    return v


def load_game(shard_dir, gid, raw=False, records_dirs=None):
    """Generator: liefert je Sample dict{map|map_u8, own, opp, mask, label(dict), atype, config}.
    raw=True: Karte bleibt uint8 (Schluessel "map_u8") — 4× kleiner im Shuffle-Buffer,
    Dequantisierung erst beim Batch-Collate. raw=False: fertige float-Karte ("map").
    records_dirs: wo die Original-Records liegen (fuer den Config/Modifier-Vektor)."""
    meta_path = os.path.join(shard_dir, f"{gid}.meta.zst")
    maps_path = os.path.join(shard_dir, f"{gid}.maps")
    cfg_vec = config_vec_for(gid, records_dirs)   # einmal je Spiel
    lines = zstd.decompress(open(meta_path, "rb").read()).decode().split("\n")
    with open(maps_path, "rb") as mf:
        for line in lines:
            if not line:
                continue
            s = json.loads(line)
            # --- Karte lesen (laengen-praefigierter zstd-Block) ---
            ln = struct.unpack("<I", mf.read(4))[0]
            block = zstd.decompress(mf.read(ln))
            u8 = np.frombuffer(block, dtype=np.uint8).copy()
            assert u8.size == MAPLEN, f"{u8.size} != {MAPLEN}"
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
            out = {"own": np.array(own_vec, np.float32),
                   "opp": np.array(opp_mat, np.float32), "mask": np.array(mask, bool),
                   "label": label, "atype": int(act.atype),
                   "config": cfg_vec,                # Modifier-Vektor (je Spiel gleich)
                   "win": float(s.get("win", 0)),   # Ziel fuer den Wert-Kopf
                   # Stichprobengewicht = Kehrwert der Aufnahmewahrscheinlichkeit.
                   # Echte Zuege: 1. Nichtstun ist unterabgetastet (jeder K-te
                   # intentlose Tick), also w=K — damit kann das Training den
                   # echten Prior herstellen, ohne neu zu materialisieren.
                   # Shards ohne das Feld (alter Pool) → 1.0.
                   "w": float(s.get("w", 1.0))}
            if raw:
                out["map_u8"] = u8                 # dequantisieren erst beim Collate
            else:
                out["map"] = dequantize(u8)
            yield out


if __name__ == "__main__":
    # Verifikation: ein Shard laden, Formen + Label-Sauberkeit pruefen
    shard_dir, gid = sys.argv[1], sys.argv[2]
    reppath = os.path.join(os.path.dirname(__file__), "..", "data", "reputation.json")
    print("Reputation geladen:", load_reputation(reppath), "Spieler")
    n = 0; clean = 0; unresolved_heads = 0
    mp_min, mp_max = 9, -9
    import collections
    atypes = collections.Counter(); wsum = collections.Counter()
    for s in load_game(shard_dir, gid):
        n += 1
        assert s["map"].shape == (18, 90, 180)
        assert s["own"].shape == (F.OWN_DIM,)
        assert s["opp"].shape == (F.MAX_OPP, F.OPP_DIM)
        mp_min = min(mp_min, float(s["map"].min())); mp_max = max(mp_max, float(s["map"].max()))
        atypes[AC.A(s["atype"]).name] += 1
        wsum[AC.A(s["atype"]).name] += s["w"]
        # sauber = alle aktiven Koepfe des Typs haben ein Label (kein UNRESOLVED gedroppt)
        need = set(AC.HEAD_SCHEMA.get(AC.A(s["atype"]), ()))
        got = set(s["label"].keys()) - {"atype"}
        if need == got: clean += 1
        else: unresolved_heads += 1
    print(f"Samples: {n}")
    print(f"  Karte-Wertebereich: [{mp_min:.2f}, {mp_max:.2f}] (soll ~[-1,1])")
    print(f"  vollstaendige Labels: {clean} ({100*clean/max(n,1):.1f}%), mit gedropptem Kopf: {unresolved_heads}")
    print(f"  own={F.OWN_DIM} opp={F.OPP_DIM}  Aktionstypen: {dict(atypes.most_common(6))}")
    tot_w = sum(wsum.values()) or 1
    print(f"  NO_OP roh: {atypes['NO_OP']} ({100*atypes['NO_OP']/max(n,1):.1f}%), "
          f"gewichtet: {100*wsum['NO_OP']/tot_w:.1f}%")
