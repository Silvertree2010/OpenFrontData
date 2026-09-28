"""Serverseitiger Test der D0-Spielregel mit echten materialisierten Samples.

Aus jedem Sample wird die Anfrage gebaut, die der Viewer schickt (Karte und Zellfakten
roh als base64, own/opps/config/ctx wie im Materialisierer, JSON hin und zurück).
Geprüft wird:
  1. Eingaben: Die Tensoren aus der Anfrage sind bitgleich zu denen des Trainer-Laders.
  2. Vorgabe der echten Labels: Server und Trainer-Eval (adapter.vorwaerts auf dem
     Lader-Batch) liefern dieselben Logits; die Entscheidung des Servers (argmax je Kopf,
     maskierter Zell-argmax) ist die des Trainer-Eval.
  3. Freie Entscheidung (Schwelle 0): Die Kette aus argmax-Schritten des Servers ist der
     argmax eines einzigen Trainer-Vorwärtslaufs mit genau diesen Wahlen als Labels.

  python trainer/tests/inftest.py --ckpt checkpoints/d0_rauch.pt --daten ~/of-mat2-out

D1 (Netz steht im Checkpoint, braucht --zusatz): die Anfrage trägt zusätzlich zusatz_b64 aus
der Offline-Datei der Zeile (float16 → float32, verlustfrei). Dazu geprüft:
  4. own_ref: auf Zeilen mit own_ref-Label löst der Server den gewählten Platz auf eine
     Einheit bzw. einen Angriff aus ctx auf, und genau diese id steht im Intent.
  5. Ohne zusatz_b64 lehnt der Server ab (nie mit Nullen auffüllen).
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

import numpy as np  # noqa: E402

import daten as D  # noqa: E402
import netze as N  # noqa: E402
import reader as R  # noqa: E402
import spielen as SP  # noqa: E402
import tabellen as T  # noqa: E402
import verlust as V  # noqa: E402
import actions as AC  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--ckpt", required=True)
ap.add_argument("--daten", nargs="+", default=["~/of-mat2-out"])
ap.add_argument("--reputation", default=None)
ap.add_argument("--partien", type=int, default=6)
ap.add_argument("--je", type=int, default=16, help="Samples je Partie (halb räumlich)")
ap.add_argument("--threads", type=int, default=4)
ap.add_argument("--zusatz", default=None, help="Ordner mit <gid>.zusatz.zst (Pflicht für D1)")
a = ap.parse_args()
torch.set_num_threads(a.threads)

zk = torch.load(a.ckpt, map_location="cpu", weights_only=True)
NETZ = zk.get("netz", "d0")
D1 = NETZ == "d1"
if D1 and not a.zusatz:
    sys.exit("D1-Checkpoint braucht --zusatz")
ad = N.baue_netz(NETZ)
ad.modul.load_state_dict(zk["model"])
ad.modul.train()
rep = D._rep_tabelle(a.reputation)
ent = SP.Entscheider(ad, "cpu", rep)
alle = D.finde_partien(a.daten)
val = sorted(g for g in alle if R.is_val(g))
if a.zusatz:
    val = [g for g in val if os.path.exists(os.path.join(os.path.expanduser(a.zusatz), f"{g}.zusatz.zst"))]
val = val[:a.partien]
LOPT = D.LaderOpt(reputation=a.reputation, zusatz=a.zusatz if D1 else None)
ZUS = {"gid": None}


def zusatz_zeile(gid, i):
    """Offline-Zeile als Nutzlast wie vom Client: float16 → float32 (verlustfrei), base64."""
    if ZUS["gid"] != gid:
        _, wo, wg, we, wa = R.zusatz_lesen(os.path.join(os.path.expanduser(a.zusatz), f"{gid}.zusatz.zst"))
        ZUS.update(gid=gid, b=(wo, wg, we, wa))
    wo, wg, we, wa = ZUS["b"]
    f = np.concatenate([wo[i].ravel(), wg[i].ravel(), we[i].ravel(), wa[i].ravel()]).astype("<f4")
    return {"zusatz_b64": base64.b64encode(f.tobytes()).decode(), "zusatz_sig": SP.ZUSATZ_SIG}
rng = random.Random(1)
z = Counter()
maxdiff = 0.0
fehler = []


def anfrage(m, mblock, cblock):
    o = {"map_b64": base64.b64encode(D._zdec_n(mblock, R.MAP_BYTES)).decode(),
         "own": m["own"], "opps": m["opps"], "config": m["cfg"], "sid": m["sid"], "allies": m.get("allies", []),
         "ctx": {"mapW": m["mapW"], "mapH": m["mapH"], "troops": m["troops"], "gold": m["gold"],
                 "oppIds": m["oppIds"], "ownUnitIds": m["ownUnitIds"], "ownAttackIds": m["ownAttackIds"],
                 "oppSids": [x.get("id") for x in m["opps"]]}}
    if cblock is not None:
        o["cells_b64"] = base64.b64encode(D._zdec_n(cblock, R.CELL_BYTES)).decode()
    return json.loads(json.dumps(o))                      # wie über HTTP


def trainer_lauf(s, labels=None, g=None, bit=None, dst=None):
    b = D.auf_geraet(D.sammle([s]), "cpu")
    b["zeiger_voll"] = True
    if labels is not None:
        for h in D.HEADS:
            b["labels"][h][0] = labels.get(h, D.IGNORE)
        b["g"][0], b["bit"][0], b["dst"][0] = g, bit, dst
    with torch.no_grad():
        return ad.vorwaerts(b), b


def masked_argmax(out, b):
    m = V.zellmaske(b)[0]
    return int(out["coarse"].float()[0].masked_fill(~m, float("-inf")).argmax())


def notiere(name, bed, info=""):
    z[name + (" ok" if bed else " FEHL")] += 1
    if not bed and len(fehler) < 10:
        fehler.append(f"{name} {info}")


for gid in val:
    d = alle[gid][0]
    G = R.Game(d, gid)
    meta, mbl, cbl = G.meta(), list(G.map_blocks()), list(G.cell_blocks())
    lz = D.PartieLeser(d, gid, LOPT, Counter())
    samples = {}
    while True:
        s = lz.naechstes()
        if s is D.ENDE:
            break
        if s is not None:
            samples[s["id"][1]] = s
    raum = sorted(i for i, s in samples.items() if s["cell"] is not None)
    rest = sorted(i for i, s in samples.items() if meta[i].get("cell", -1) < 0)
    wahl = rng.sample(raum, min(a.je // 2, len(raum))) + rng.sample(rest, min(a.je - a.je // 2, len(rest)))
    oref = sorted(i for i, s in samples.items() if s["lab"][D.HI["own_ref"]] != D.IGNORE)
    if D1:
        wahl += rng.sample(oref, min(4, len(oref)))          # own_ref-Zeilen gezielt dazu
    for i in wahl:
        s, m = samples[i], meta[i]
        c = m.get("cell", -1)
        o = anfrage(m, mbl[i], cbl[c] if c >= 0 else None)
        if D1:
            o.update(zusatz_zeile(gid, i))
        # 1. Eingaben bitgleich
        out_t, bt = trainer_lauf(s)
        bs, _ = SP.anfrage_zu_batch(o, rep, "cpu", D1)
        for k in ("map", "own", "opp", "opp_mask", "config", "legal", "owner", "own_frac", "wh") + (
                ("einheiten", "angriffe") if D1 else ()):
            notiere(f"Eingabe {k}", torch.equal(bt[k], bs[k]), f"{gid}/{i}")
        if D1 and i in oref:
            # 4. own_ref auf eine konkrete Einheit bzw. einen Angriff
            lab = {h: int(s["lab"][j]) for h, j in D.HI.items() if s["lab"][j] != D.IGNORE}
            vg = {h: lab[h] for h in ("atype", "unit_type", "target") if h in lab}
            dec = ent.entscheide(o, 0.0, vorgabe=vg, allianz_filter=False)
            orf = dec.get("own_ref") or {}
            ab = lab["atype"] == int(AC.A.CANCEL_ATTACK)
            liste = m["ownAttackIds"] if ab else m["ownUnitIds"]
            werte = []
            for v in (dec.get("intent") or {}).values():
                werte += v if isinstance(v, list) else [v]
            rid, pl = orf.get("id"), orf.get("platz", -1)
            ok = rid is not None and 0 <= pl < len(liste) and rid == liste[pl] and rid in werte
            notiere("own_ref aufgelöst", ok, f"{gid}/{i} {orf} {dec.get('intent')}")
            z["own_ref wie Mensch" if orf.get("platz") == lab["own_ref"] else "own_ref anders als Mensch"] += 1
        if not D1 and a.zusatz:
            # D0 mit Zusatzfeldern in der Anfrage (so schickt die Erweiterung immer): gleiche Entscheidung
            o2 = dict(o, **zusatz_zeile(gid, i))
            d1_, d2_ = ent.entscheide(o, 0.0, allianz_filter=False), ent.entscheide(o2, 0.0, allianz_filter=False)
            notiere("D0 ignoriert Zusatzfelder", json.dumps(d1_, sort_keys=True) == json.dumps(d2_, sort_keys=True),
                    f"{gid}/{i}")
        if D1 and not z["ohne zusatz abgelehnt ok"]:
            # 5. ohne Zusatzfelder: Fehler, kein Auffüllen
            try:
                SP.anfrage_zu_batch({k: v for k, v in o.items() if not k.startswith("zusatz")}, rep, "cpu", True)
                notiere("ohne zusatz abgelehnt", False)
            except ValueError:
                notiere("ohne zusatz abgelehnt", True)
        # 2. Vorgabe der echten Labels
        lab = {h: int(s["lab"][j]) for h, j in D.HI.items() if s["lab"][j] != D.IGNORE}
        out_s, b2 = ent.vorwaerts_vorgabe(o, lab, s["g"], s["bit"], s["dst"])
        for h in out_t:
            if h == "coarse_da":
                continue
            maxdiff = max(maxdiff, float((out_t[h].float() - out_s[h].float()).abs().max()))
        for h in lab:
            if h in ("coarse", "fine", "atype"):
                continue
            notiere(f"argmax {h}", int(out_t[h][0].argmax()) == int(out_s[h][0].argmax()), f"{gid}/{i}")
        vg = {"atype": lab["atype"]}
        if lab["atype"] != 0:
            vg.update({h: lab[h] for h in ("unit_type", "target") if h in lab})
            if s["g"] >= 0 and T.GRUPPEN[s["g"]] in T.ZIEL_GRUPPEN:
                vg["target"] = lab.get("target", D.IGNORE)
                vg["dst"] = s["dst"]
            dec = ent.entscheide(o, 0.0, vorgabe=vg, allianz_filter=False)
            for h in ("magnitude", "own_ref", "emoji", "quickchat", "embargo_start", "rocket_up", "amount"):
                if h in dec.get("wahl", {}):
                    notiere(f"Entscheidung {h} = Trainer", dec["wahl"][h] == int(out_t[h][0].argmax()), f"{gid}/{i}")
            if s["cell"] is not None and "coarse" in lab:
                notiere("Entscheidung Zelle = Trainer-Eval", dec.get("kandidaten", [None])[0] == masked_argmax(out_t, bt),
                        f"{gid}/{i} {dec.get('kandidaten')} {masked_argmax(out_t, bt)}")
        # 3. freie Kette (braucht Zellfakten)
        if s["cell"] is not None:
            dec = ent.entscheide(o, 0.0, allianz_filter=False)
            w = dec["wahl"]
            aa = w["atype"]
            gname = SP.gruppe_von(aa, w.get("unit_type"))
            gi = T.GID[gname] if gname else -1
            dst = dec["zielSid"] if (gname in T.ZIEL_GRUPPEN and dec["zielSid"] is not None) else 0
            labels = {h: w[h] for h in ("atype", "unit_type", "target") if h in w}
            out3, b3 = trainer_lauf(s, labels, gi, T.BIT[gname] if gname else 0, dst)
            notiere("frei atype", int(out3["atype"][0, 1:].argmax()) + 1 == aa)
            if "unit_type" in w:
                notiere("frei unit_type", int(out3["unit_type"][0].argmax()) == w["unit_type"])
            if "target" in w:
                notiere("frei target", int(out3["target"][0].argmax()) == w["target"])
            if dec.get("kandidaten") and gname and T.R_KACHELN[gname] is not None:
                notiere("frei Zelle", masked_argmax(out3, b3) == dec["kandidaten"][0], f"{gid}/{i}")
            z["frei " + dec["atype"]] += 1
    print(f"[inftest] {gid}: {len(wahl)} Samples", flush=True)

for k in sorted(z):
    print(f"  {k}: {z[k]}")
print(f"max. Logit-Abweichung Server gegen Trainer (Vorgabe): {maxdiff:.3g}")
ok = not any(k.endswith("FEHL") for k in z) and maxdiff < 1e-4
print("\n" + ("ALLES OK" if ok else f"FEHLER: {fehler}"))
sys.exit(0 if ok else 1)
