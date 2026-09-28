"""Trockenlauf Ende-zu-Ende auf der CPU, ohne echte Daten und ohne GPU.

Schreibt synthetische Partien im Format 2 (hdr.json, .ok mit Grössen, meta.zst,
maps, cells) in einen Temp-Ordner und lässt train.main mit einem Mini-Netz
laufen: Eval am Start und zwischendurch, Log, Board nur lokal, Checkpoint,
Fortsetzen und Epochenende. Prüft danach Checkpoint, Zähler und Metrikdatei.
Dauert Sekunden.

    python trainer/tests/trockenlauf.py
"""
from __future__ import annotations

import json
import os
import random
import string
import struct
import sys
import tempfile
from collections import Counter

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HIER))
sys.path.insert(0, HIER)

import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn as nn  # noqa: E402

import daten as D  # noqa: E402
import netze as N  # noqa: E402
import tabellen as T  # noqa: E402
import actions as AC  # noqa: E402
import featurize as F  # noqa: E402
import reader as R  # noqa: E402


# ---------------------------------------------------------------- Mini-Netz
class _Modul(nn.Module):
    def __init__(self, ks):
        super().__init__()
        d = 18 + F.OWN_DIM
        self.c = nn.Conv2d(18, 1, 1)
        self.k = nn.ModuleDict({h: nn.Linear(d, k) for h, k in ks.items() if h != "coarse"})
        self.v = nn.Linear(d, 1)

    def forward(self, m, own, mask):
        x = torch.cat([m.mean((2, 3)), own], 1)
        out = {h: l(x) for h, l in self.k.items()}
        voll = torch.cat([mask, torch.ones(mask.shape[0], 2, dtype=torch.bool, device=mask.device)], 1)
        out["target"] = out["target"].masked_fill(~voll, -1e9)
        out["coarse"] = self.c(m).flatten(1)
        out["value"] = self.v(x).squeeze(-1)
        return out


class Trocken(N.NetzAdapter):
    name = "trocken"

    def __init__(self):
        self.kopf_groessen = dict(AC.HEAD_SIZES)
        self.modul = _Modul(self.kopf_groessen)

    def vorwaerts(self, b):
        return self.modul(b["map"], b["own"], b["opp_mask"])


def baue():
    return Trocken()


# ---------------------------------------------------------------- Daten
def _zc(b: bytes) -> bytes:
    try:
        import zstandard as zs
        return zs.ZstdCompressor().compress(b)
    except ImportError:
        from compression import zstd
        return zstd.compress(b)


def _blk(b: bytes) -> bytes:
    z = _zc(b)
    return struct.pack("<I", len(z)) + z


def schreibe_partie(d, gid, n, rng):
    W, H = rng.choice([(1800, 900), (1360, 1360), (600, 1200)])
    players = [{"sid": s, "clientID": f"CL{s}", "playerID": f"p{s}", "name": f"n{s}", "team": None,
                "type": "HUMAN"} for s in (1, 2, 3)]
    hdr = {"format": 2, "gid": gid, "W": W, "H": H, "map": "Test", "players": players,
           "config": {"gameMode": "Free For All", "disabledUnits": []}, "spawnEndTick": 0, "tier1": False}
    metas, maps, cells = [], bytearray(), bytearray()
    kinds, intents, res = Counter(), Counter(), Counter()
    ci = 0
    for i in range(n):
        m = {"turn": i * 10, "tick": i * 10, "clientID": "CL1", "sid": 1, "mapW": W, "mapH": H,
             "troops": 1000, "gold": 5000, "oppIds": ["p2", "p3"], "ownUnitIds": [5], "ownAttackIds": [],
             "own": {"troops": 1000, "tiles": 50}, "opps": [{"troops": 500, "user": "x"}, {"troops": 700}],
             "w": 1, "w_tick": 1.0, "win": i % 2, "kind": "act", "allies": [], "team": None, "cfg": {}}
        k = i % 10
        if k == 0:
            m.update(kind="noop", intent={"type": "no_op"}, w=200, w_tick=200)
        elif k == 1:
            m.update(intent={"type": "attack", "targetID": "p2", "troops": 300}, w=3, merged=2)
        elif k == 2:
            m.update(intent={"type": "toggle_pause"})          # muss verworfen werden
        else:
            tile = rng.randrange(W * H)
            intent = {3: {"type": "build_unit", "unit": "City", "tile": tile},
                      4: {"type": "boat", "dst": tile, "troops": 100},
                      5: {"type": "spawn", "tile": tile},
                      6: {"type": "build_unit", "unit": "Atom Bomb", "tile": tile},
                      7: {"type": "move_warship", "unitIds": [5], "tile": tile},
                      8: {"type": "build_unit", "unit": "Port", "tile": tile},
                      9: {"type": "build_unit", "unit": "Hydrogen Bomb", "tile": tile}}[k]
            m.update(intent=intent, click=tile, dst_owner=2 if k in (4, 6, 9) else 0, res_tile=tile,
                     res_kind=2 if (i % 17 == 0) else 0, res_dt=1, res_unit_id=-1, cell=ci)
            if k == 5:
                m["kind"] = "spawn"
            g = T.gruppe(intent)
            x, y = tile % W, tile // W
            c = (y * 90 // H) * 180 + (x * 180 // W)
            legal = np.zeros(16200, np.uint8)
            legal[rng.sample(range(16200), 300)] |= 1 << T.BIT[g]
            legal[c] |= 1 << T.BIT[g]
            owner = np.zeros(16200, np.uint16)
            owner[max(0, c - 40):c + 40] = 2
            ownf = np.zeros(16200, np.uint8)
            cells += _blk(owner.astype("<u2").tobytes() + ownf.tobytes() + legal.tobytes())
            ci += 1
        mp = np.zeros(18 * 90 * 180, np.uint8)
        mp[rng.sample(range(mp.size), 500)] = rng.randrange(256)
        maps += _blk(mp.tobytes())
        metas.append(json.dumps(m))
        it = m.get("intent") or {}
        kinds[m["kind"]] += 1
        intents[it.get("type", "?")] += 1
        if m.get("res_kind") == 0:
            res[it.get("unit") if it.get("type") == "build_unit" else it.get("type")] += 1
    dateien = {f"{gid}.maps": bytes(maps), f"{gid}.cells": bytes(cells),
               f"{gid}.meta.zst": _zc("\n".join(metas).encode()), f"{gid}.hdr.json": json.dumps(hdr).encode()}
    for name, b in dateien.items():
        with open(os.path.join(d, name), "wb") as f:
            f.write(b)
    ok = {"format": 2, "files": {k: len(v) for k, v in dateien.items()}, "samples": n, "spatial": ci,
          "by_kind": dict(kinds), "by_intent": dict(intents),
          "res": {k: {"ok": v, "invalid": 0, "late": 0} for k, v in res.items()},
          "valid_until": None, "legal_bit1": True}
    with open(os.path.join(d, f"{gid}.ok"), "w") as f:
        json.dump(ok, f)


def gids(rng, n_val, n_train):
    val, train = [], []
    while len(val) < n_val or len(train) < n_train:
        g = "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(8))
        (val if R.is_val(g) else train).append(g) if (len(val) < n_val if R.is_val(g) else len(train) < n_train) else None
    return val, train


# ---------------------------------------------------------------- Lauf
if __name__ == "__main__":
    import train as TR
    rng = random.Random(5)
    tmp = tempfile.mkdtemp(prefix="trocken-")
    out = os.path.join(tmp, "s0")
    os.makedirs(out)
    val, tr = gids(rng, 3, 12)
    for g in val + tr:
        schreibe_partie(out, g, rng.randrange(20, 60), rng)
    ck = os.path.join(tmp, "ck.pt")
    mp = os.path.join(tmp, "logs", "metrics.jsonl")
    nw = os.environ.get("TROCKEN_WORKERS", "2")      # 0 = ohne Worker-Prozesse
    netz = os.environ.get("TROCKEN_NETZ", "trockenlauf:baue")   # z. B. d0
    extra = os.environ.get("TROCKEN_ARGS", "").split()       # z. B. --raum-verlust lset
    basis = extra + ["--daten", tmp, "--netz", netz, "--geraet", "cpu", "--workers", nw,
             "--batch", "16", "--puffer", "64", "--offen", "2", "--ckpt", ck, "--kein-push",
             "--metrik-pfad", mp, "--log-alle", "2", "--ckpt-alle", "2", "--eval-allg", "200"]
    print("=== A: 4 Schritte, Eval am Start und alle 2 Schritte", flush=True)
    TR.main(basis + ["--schritte", "4", "--frisch", "--eval-start", "--eval-alle", "2"])
    z = torch.load(ck, map_location="cpu", weights_only=True)
    fehler = []
    if z["gstep"] != 4 or z["epoch"] != 0 or not z["epoch_liste"]:
        fehler.append(f"A: gstep {z['gstep']} epoch {z['epoch']}")
    print("=== B: fortsetzen bis Epochenende", flush=True)
    TR.main(basis + ["--epochen", "1", "--eval-alle", "0"])
    z = torch.load(ck, map_location="cpu", weights_only=True)
    if z["epoch"] != 1 or z["epoch_liste"] is not None or z["gstep"] <= 4:
        fehler.append(f"B: gstep {z['gstep']} epoch {z['epoch']}")
    zz = z["zaehler"]
    if not zz.get("verworfen_unbekannt") or not zz.get("raum_res_ungueltig"):
        fehler.append(f"Zähler: {zz}")
    if not os.path.exists(ck[:-3] + "_e0.pt"):
        fehler.append("kein Epochen-Snapshot")
    arten = set()
    with open(mp) as f:
        for zeile in f:
            arten.add(json.loads(zeile)["kind"])
    if not {"start", "step", "val", "snap", "end"} <= arten:
        fehler.append(f"Metrik-Arten {arten}")
    print(f"Checkpoint: gstep {z['gstep']}, epoch {z['epoch']}; Metrik-Arten {sorted(arten)}")
    print(f"Zähler: {zz}")
    print("\n" + ("TROCKENLAUF OK" if not fehler else f"FEHLER: {fehler}"))
    sys.exit(1 if fehler else 0)
