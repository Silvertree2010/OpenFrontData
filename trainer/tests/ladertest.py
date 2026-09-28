"""Ladertest auf echten Daten (CPU, klein). Prüft:
  1. zstd mit fester Ausgabegrösse == reader.zdec (Karte und Zellen)
  2. Karte und Zellen im Batch == reader.Game.maps()/cells() für dieselbe Zeile
  3. Strom mit 2 Workern: jedes Sample genau einmal, emittiert + verworfen == .ok samples
  4. Auflösungsraten: Angriffsziel in oppIds, Boot/Nuke-Ziel über dst_owner

    python trainer/tests/ladertest.py ~/of-mat2-out/s0 [anzahl_partien]
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from torch.utils.data import DataLoader  # noqa: E402

import daten as D  # noqa: E402
import reader as R  # noqa: E402

import argparse  # noqa: E402

_ap = argparse.ArgumentParser()
_ap.add_argument("ordner")
_ap.add_argument("anzahl", nargs="?", type=int, default=8)
_ap.add_argument("--partien-liste", default=None, help="wie im Trainer: nur diese gids")
_ap.add_argument("--deckel", type=int, default=0, help="wie im Trainer: höchstens N Samples je Partie")
_ap.add_argument("--zusatz", default=None, help="Ordner mit <gid>.zusatz.zst: prüft Abschnitt 7 (Zusatzdateien)")
_a = _ap.parse_args()
ordner = os.path.expanduser(_a.ordner)
anz = _a.anzahl
alle = D.finde_partien([ordner])
# kleine Partien zuerst, damit der Test schnell bleibt
gids = sorted(alle, key=lambda g: (alle[g][1], g))[:anz]
fehler = []


def pruefe(name, bed, info=""):
    print(f"{'ok  ' if bed else 'FEHL'} {name} {info}", flush=True)
    if not bed:
        fehler.append(name)


# 1 + 2: gegen reader.Game
nach_groesse = sorted(alle, key=lambda g: (alle[g][1], g))
mitte = nach_groesse[len(nach_groesse) // 2:]           # mittelgrosse Partie, nicht die kleinste
g0 = next(g for g in mitte if os.path.exists(R.path_of(ordner, g, "cells")))
game = R.Game(ordner, g0)
maps_ref = list(game.maps())
cells_ref = list(game.cells())
z = Counter()
leser = D.PartieLeser(ordner, g0, D.LaderOpt(), z)
samples = []
while True:
    s = leser.naechstes()
    if s is D.ENDE:
        break
    if s is not None:
        samples.append(s)
pruefe("zstd Karte == reader.zdec", all(D._zdec_n(s["map"], R.MAP_BYTES) == maps_ref[s["id"][1]] for s in samples[:50]))
b = D.sammle(samples)
idx = [s["id"][1] for s in samples]
gleich = all(np.array_equal(b["map_u8"][k].numpy().reshape(-1), np.frombuffer(maps_ref[i], np.uint8))
             for k, i in enumerate(idx))
pruefe("Batch-Karte == reader.maps()", gleich, f"({len(idx)} Samples, Partie {g0})")
meta = game.meta()
ok_z = True
nz = 0
for k, i in enumerate(idx):
    c = meta[i].get("cell", -1)
    if samples[k]["cell"] is not None:
        nz += 1
        o, f, l = cells_ref[c]
        ok_z &= (np.array_equal(b["legal"][k].numpy(), np.frombuffer(l, np.uint8)) and
                 np.array_equal(b["own_frac"][k].numpy(), np.frombuffer(f, np.uint8)) and
                 np.array_equal(b["owner"][k].numpy().astype(np.uint16), np.frombuffer(o, "<u2")))
pruefe("Batch-Zellen == reader.cells()", ok_z and nz > 0, f"({nz} räumliche Samples)")

# 3: Strom mit 2 Workern
ds = D.Strom([(g, ordner) for g in gids], batch=64, puffer=500, offen=3, seed=3, opt=D.LaderOpt())
dl = DataLoader(ds, batch_size=None, num_workers=2)
ids, fertig, zz = [], [], Counter()
for bb in dl:
    fertig += bb["fertig"]
    zz.update(bb["zaehler"])
    if not bb.get("leer"):
        ids += [tuple(x) for x in bb["ids"]]        # der DataLoader macht aus Tupeln Listen
soll = sum(alle[g][1] for g in gids)
verw = sum(v for k, v in zz.items() if k.startswith("verworfen"))
pruefe("kein Sample doppelt", len(ids) == len(set(ids)), f"{len(ids)} emittiert")
pruefe("emittiert + verworfen == .ok samples", len(ids) + verw == soll, f"{len(ids)} + {verw} gegen {soll}")
pruefe("alle Partien als fertig gemeldet", sorted(fertig) == sorted(gids))
print("Zähler:", dict(zz))

# 4: Auflösungsraten
angr = zz.get("act", 0)
print(f"Ziel (D0): gegner {zz.get('ziel_gegner', 0)}, neutral {zz.get('ziel_neutral', 0)}, "
      f"nicht in Top 24 {zz.get('ziel_nicht_in_top24', 0)}, eigen {zz.get('ziel_eigen', 0)}")
n_att = n_att_ok = 0
for g in gids[:4]:
    lz = D.PartieLeser(ordner, g, D.LaderOpt(), Counter())
    while True:
        s = lz.naechstes()
        if s is D.ENDE:
            break
        if s is not None and s["lab"][D.HI["atype"]] == 1:
            n_att += 1
            n_att_ok += s["lab"][D.HI["target"]] != D.IGNORE
print(f"Angriffe mit aufgelöstem target: {n_att_ok} von {n_att}")
# 4b: schnelle Gegner-Featurisierung == featurize.featurize_opps (Original, nach _rep)
import copy  # noqa: E402
import featurize as FZ  # noqa: E402

rep = D._rep_tabelle(os.path.expanduser("~/projects/openfront-ai/data/reputation.json"))
diff, n_opp = 0.0, 0
for g in [g0] + gids[:4]:
    for m in R.Game(ordner, g).meta():
        opps = copy.deepcopy(m.get("opps") or [])
        for o in opps:
            o["_rep"] = rep.get(f"{o.get('user') or '?'}\x1f{o.get('clan') or ''}", 0.5)
        ref_m, ref_k = FZ.featurize_opps(opps)
        neu_m, neu_k = D.gegner_features(m.get("opps") or [], rep)
        diff = max(diff, float(np.abs(np.asarray(ref_m, np.float32) - neu_m).max()))
        n_opp += 1
        if not np.array_equal(np.asarray(ref_k, bool), neu_k):
            diff = float("inf")
pruefe("schnelle Gegner-Features == featurize_opps", diff == 0.0, f"(max. Abweichung {diff}, {n_opp} Samples)")

# 5: L-set gegen Brute Force aus allen Metazeilen der Partie; Samples sonst unverändert
import actions as AC  # noqa: E402
import tabellen as T  # noqa: E402

lz = D.PartieLeser(ordner, g0, D.LaderOpt(lset=True), Counter())
mit = []
while True:
    s = lz.naechstes()
    if s is D.ENDE:
        break
    if s is not None:
        mit.append(s)
pruefe("Samples mit L-set sonst gleich", len(mit) == len(samples) and
       all(np.array_equal(x["lab"], y["lab"]) and x["map"] == y["map"] for x, y in zip(mit, samples)))


def typ_zelle(m):
    it = m.get("intent") or {}
    g = T.gruppe(it)
    if g is None or T.R_KACHELN[g] is None or m.get("kind") == "noop":
        return None
    tile = m.get("res_tile", -1) if m.get("res_kind", 2) in (0, 1) else -1
    if tile is None or tile < 0 or m.get("cell", -1) < 0:
        return None
    return (it.get("unit") if it.get("type") == "build_unit" else it.get("type"),
            AC.tile_encode(tile, m["mapW"], m["mapH"])[0])


tz = [typ_zelle(m) for m in meta]
ok_l, n_l, n_nicht_leer = True, 0, 0
for s in mit:
    i = s["id"][1]
    if s["lset"] is None:
        continue
    n_l += 1
    m = meta[i]
    soll = []
    for j, mj in enumerate(meta):
        if j == i or tz[j] is None or mj["sid"] != m["sid"] or tz[j][0] != tz[i][0]:
            continue
        if abs(mj["tick"] - m["tick"]) <= D.LSET_TICKS and tz[j][1] != tz[i][1] and tz[j][1] not in soll:
            soll.append(tz[j][1])
    soll = soll[:D.LSET_K]
    ist = [c for c in s["lset"].tolist() if c >= 0]
    n_nicht_leer += bool(ist)
    ok_l &= ist == soll
pruefe("L-set == Brute Force", ok_l and n_l > 0, f"({n_l} räumliche Samples, {n_nicht_leer} mit weiteren Zellen)")

# 6: wie im vollen Lauf — Partienliste und Deckel auf echten Daten
if _a.partien_liste:
    with open(os.path.expanduser(_a.partien_liste)) as f:
        liste = {z.strip() for z in f if z.strip() and not z.startswith("#")}
    alle_l = D.finde_partien([ordner], nur=liste)
    pruefe("Liste greift", bool(alle_l) and set(alle_l) <= liste, f"({len(alle_l)} von {len(liste)} im Ordner)")
    train_g = sorted(g for g in alle_l if not R.is_val(g))
    val_g = sorted(g for g in alle_l if R.is_val(g))
    pruefe("train und val disjunkt", bool(val_g) and set(val_g).isdisjoint(train_g),
           f"({len(train_g)} train, {len(val_g)} val)")
    nach_groesse = sorted(train_g, key=lambda g: alle_l[g][1])
    wahl = nach_groesse[-max(1, anz // 2):] + nach_groesse[:anz // 2]      # grosse und kleine Partien
    soll = sum(alle_l[g][1] for g in wahl)
    ds = D.Strom([(g, alle_l[g][0]) for g in wahl], 64, 500, 3, 5, D.LaderOpt(deckel=_a.deckel))
    ids2, z2 = [], Counter()
    for bb in DataLoader(ds, batch_size=None, num_workers=2):
        z2.update(bb["zaehler"])
        if not bb.get("leer"):
            ids2 += [tuple(x) for x in bb["ids"]]
    verw = sum(v for k, v in z2.items() if k.startswith("verworfen"))
    pruefe("mit Deckel: kein Sample doppelt", len(ids2) == len(set(ids2)), f"{len(ids2)} emittiert")
    pruefe("mit Deckel: emittiert + Deckel + verworfen = Samples",
           len(ids2) + z2["deckel_verworfen"] + verw == soll,
           f"{len(ids2)} + {z2['deckel_verworfen']} + {verw} von {soll}")
    pruefe("keine Val-Partie im Trainingsstrom", not any(R.is_val(g) for g, _ in ids2))
    je = Counter(g for g, _ in ids2)
    zu_viel = [(g, je[g], alle_l[g][1]) for g in wahl if _a.deckel and je[g] > 1.3 * _a.deckel]
    pruefe("Deckel greift bei grossen Partien", not zu_viel, str(zu_viel[:3]))
    print("Partien im Deckel-Lauf:", [(g, alle_l[g][1], je[g]) for g in wahl])

# 7: Zusatzdateien als fester Bestandteil (--zusatz)
if _a.zusatz:
    zdir = os.path.expanduser(_a.zusatz)
    da = {n[:-len(".zusatz.zst")] for n in os.listdir(zdir) if n.endswith(".zusatz.zst")}
    mit = [g for g in sorted(alle, key=lambda g: (alle[g][1], g)) if g in da][:anz]
    ohne = [g for g in sorted(alle) if g not in da][:2]
    print(f"Zusatz: {len(mit)} Partien mit Datei, {len(ohne)} ohne (müssen übersprungen werden)")
    ds = D.Strom([(g, ordner) for g in mit + ohne], 64, 500, 3, 7, D.LaderOpt(zusatz=zdir))
    meta_von = {g: R.Game(ordner, g).meta() for g in mit}
    ids3, z3 = [], Counter()
    n_e = e_gleich = e_ausser = a_ausser = 0
    import zusatz_felder as ZF  # noqa: E402
    import featurize as FZ2  # noqa: E402
    for bb in DataLoader(ds, batch_size=None, num_workers=2):
        z3.update(bb["zaehler"])
        if bb.get("leer"):
            continue
        ids3 += [tuple(x) for x in bb["ids"]]
        pruefe_form = bb["einheiten"].shape[1:] == (ZF.MAX_EINHEIT, ZF.DIM_EINHEIT) and \
            bb["angriffe"].shape[1:] == (ZF.MAX_ANGRIFF, ZF.DIM_ANGRIFF) and \
            bb["opp"].shape[-1] == FZ2.OPP_DIM + ZF.DIM_OPP and bb["own"].shape[-1] == FZ2.OWN_DIM + ZF.DIM_GLOBAL
        if not pruefe_form:
            pruefe("Zusatz: Formen im Batch", False)
            break
        for k, (g, i) in enumerate(bb["ids"]):
            m = meta_von[g][int(i)]
            nu = min(len(m.get("ownUnitIds") or []), ZF.MAX_EINHEIT)
            na = min(len(m.get("ownAttackIds") or []), ZF.MAX_ANGRIFF)
            belegt_e = (bb["einheiten"][k, :, 0] > 0).nonzero().flatten()
            belegt_a = (bb["angriffe"][k, :, 0] > 0).nonzero().flatten()
            n_e += 1
            e_gleich += int(len(belegt_e) == nu)
            e_ausser += int(bool(len(belegt_e)) and int(belegt_e.max()) >= nu)
            a_ausser += int(bool(len(belegt_a)) and int(belegt_a.max()) >= na)
    soll3 = sum(alle[g][1] for g in mit)
    verw3 = sum(v for k, v in z3.items() if k.startswith("verworfen"))
    pruefe("Zusatz: Partien ohne Datei übersprungen und gezählt",
           z3.get("zusatz_fehlt", 0) == len(ohne) and not any(g in ohne for g, _ in ids3),
           f"({z3.get('zusatz_fehlt', 0)} gezählt)")
    pruefe("Zusatz: kein Sample doppelt", len(ids3) == len(set(ids3)), f"{len(ids3)} emittiert")
    pruefe("Zusatz: emittiert + verworfen == Samples", len(ids3) + verw3 == soll3, f"{len(ids3)} + {verw3} von {soll3}")
    pruefe("Zusatz: Einheiten nur auf Plätzen der ownUnitIds", e_ausser == 0, f"({e_ausser} von {n_e} Samples daneben)")
    pruefe("Zusatz: Zahl besetzter Einheitenplätze == ownUnitIds (gekappt 128)", e_gleich >= 0.99 * n_e,
           f"({e_gleich} von {n_e} gleich)")
    pruefe("Zusatz: Angriffe nur auf Plätzen der ownAttackIds", a_ausser == 0, f"({a_ausser} daneben)")
    leer = {k: v for k, v in z3.items() if k.startswith("own_ref_leer")}
    okz = {k: v for k, v in z3.items() if k.startswith("own_ref_ok")}
    pruefe("Zusatz: own_ref zeigt nie ins Leere", not leer, f"ok {okz}, leer {leer}, "
           f"kein Kriegsschiff {z3.get('own_ref_kein_kriegsschiff', 0)}")

print("\n" + ("ALLES OK" if not fehler else f"{len(fehler)} FEHLER: {fehler}"))
sys.exit(1 if fehler else 0)
