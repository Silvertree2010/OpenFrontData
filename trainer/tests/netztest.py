"""Netztest D0 auf der CPU, winzig (B=4). Prüft Formen, Gradientenfluss, dass die
Zellfakten und die Lehrer-Labels nicht in den atype-Kopf lecken, und dass der
Zeiger auf Typ und Zielspieler reagiert.

    python trainer/tests/netztest.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch  # noqa: E402

import daten as D  # noqa: E402
import netz_d0 as ND  # noqa: E402
import tabellen as T  # noqa: E402
import verlust as V  # noqa: E402
import actions as AC  # noqa: E402
import featurize as F  # noqa: E402

torch.manual_seed(0)
torch.set_num_threads(2)
fehler = []


def pruefe(name, bed, info=""):
    print(f"{'ok  ' if bed else 'FEHL'} {name} {info}")
    if not bed:
        fehler.append(name)


B, NZ = 4, 16200


def batch():
    lab = {h: torch.full((B,), D.IGNORE, dtype=torch.long) for h in D.HEADS}
    lab["atype"] = torch.tensor([2, 3, 1, 0])                 # Bau, Boot, Angriff, Nichtstun
    lab["unit_type"] = torch.tensor([0, D.IGNORE, D.IGNORE, D.IGNORE])
    lab["target"] = torch.tensor([D.IGNORE, 1, 0, D.IGNORE])
    lab["magnitude"] = torch.tensor([D.IGNORE, 3, 4, D.IGNORE])
    lab["coarse"] = torch.tensor([500, 7000, D.IGNORE, D.IGNORE])
    legal = torch.zeros(B, NZ, dtype=torch.uint8)
    legal[0, 400:600] = 2
    legal[1, 6000:8000] = 8
    mask = torch.zeros(B, 24, dtype=torch.bool)
    mask[:, :5] = True
    return {"map": torch.randn(B, 18, 90, 180), "own": torch.randn(B, F.OWN_DIM),
            "opp": torch.randn(B, 24, F.OPP_DIM), "opp_mask": mask, "config": torch.randn(B, F.CONFIG_DIM),
            "labels": lab, "w": torch.ones(B), "wt": torch.ones(B), "win": torch.tensor([1.0, 0, 1, 0]),
            "g": torch.tensor([T.GID["bau"], T.GID["boot"], -1, -1]), "bit": torch.tensor([1, 3, 0, 0]),
            "legal": legal, "owner": torch.randint(0, 5, (B, NZ)).to(torch.int16),
            "own_frac": torch.randint(0, 255, (B, NZ)).to(torch.uint8),
            "xy": torch.tensor([[100.0, 50], [900, 400], [0, 0], [0, 0]]),
            "wh": torch.tensor([[1800.0, 900]] * B), "dst": torch.tensor([0, 2, 0, 0]),
            "lset": torch.full((B, 16), -1)}


ad = ND.D0Adapter()
net = ad.modul
print(f"D0: {sum(p.numel() for p in net.parameters()) / 1e6:.2f} Mio Parameter")
b = batch()
out = ad.vorwaerts(b)
pruefe("alle Köpfe da, Formen stimmen",
       all(out[h].shape == (B, k) for h, k in ad.kopf_groessen.items()) and out["value"].shape == (B,))
pruefe("coarse 0 für nicht-räumliche Zeilen", float(out["coarse"][2:].detach().abs().max()) == 0.0)
tot, logs, info = V.berechne(out, b, V.VerlustOpt(adv_beta=0.0))
pruefe("Verlust endlich", bool(torch.isfinite(tot)))
logs["coarse"].backward(retain_graph=True)
gk = net.enc.d1[0].weight.grad
pruefe("Kachel-Verlust erreicht den Kodierer", gk is not None and float(gk.abs().sum()) > 0)
pruefe("Kachel-Verlust erreicht den Kontext h", float(net.ctx[0].weight.grad.abs().sum()) > 0)
net.zero_grad()
tot.backward()
ohne = [n for n, p in net.named_parameters() if p.grad is None]
pruefe("jeder Parameter bekommt einen Gradienten", not ohne, str(ohne[:5]))

with torch.no_grad():
    ref = ad.vorwaerts(b)
    b2 = batch()
    for k in ("map", "own", "opp", "config"):
        b2[k] = b[k]
    b2["legal"] = torch.randint(0, 255, (B, NZ)).to(torch.uint8)
    b2["owner"] = torch.randint(0, 9, (B, NZ)).to(torch.int16)
    b2["own_frac"] = torch.randint(0, 255, (B, NZ)).to(torch.uint8)
    b2["labels"]["atype"] = torch.tensor([3, 2, 4, 5])
    b2["labels"]["target"] = torch.tensor([3, 2, 1, 0])
    o2 = ad.vorwaerts(b2)
    pruefe("atype hängt nicht an Zellfakten oder Labels", torch.equal(ref["atype"], o2["atype"]))
    pruefe("value hängt nicht an Zellfakten oder Labels", torch.equal(ref["value"], o2["value"]))
    pruefe("unit_type ist auf atype bedingt", not torch.equal(ref["unit_type"], o2["unit_type"]))
    b3 = batch()
    for k in ("map", "own", "opp", "config", "owner", "own_frac"):
        b3[k] = b[k]
    b3["labels"]["target"] = torch.tensor([D.IGNORE, 3, 0, D.IGNORE])     # Boot: anderer Gegner
    b3["dst"] = torch.tensor([0, 4, 0, 0])
    o3 = ad.vorwaerts(b3)
    pruefe("Zeiger reagiert auf den Zielspieler (Boot)", not torch.equal(ref["coarse"][1], o3["coarse"][1]))
    pruefe("Bau-Zeile bleibt gleich, wenn sich nur das Boot ändert", torch.equal(ref["coarse"][0], o3["coarse"][0]))
    b4 = batch()
    for k in ("map", "own", "opp", "config", "owner", "own_frac", "legal"):
        b4[k] = b[k]
    b4["labels"]["unit_type"] = torch.tensor([5, D.IGNORE, D.IGNORE, D.IGNORE])
    o4 = ad.vorwaerts(b4)
    pruefe("Zeiger reagiert auf unit_type", not torch.equal(ref["coarse"][0], o4["coarse"][0]))
    zi = ND.D0Netz.ziel_index(b)
    pruefe("Zielindex: Bau SELBST, Boot Gegner 1, Angriff 0, Nichtstun KEINS",
           zi.tolist() == [ND.Z_SELBST, 1, 0, ND.Z_KEINS], str(zi.tolist()))

# Überlauf: 3 räumliche Zeilen, aber S = ceil(0,4·4) = 2 Zeiger-Plätze
b5 = batch()
b5["labels"]["atype"][2] = 3
b5["labels"]["coarse"][2] = 7100
b5["g"][2] = T.GID["boot"]
b5["legal"][2, 6000:8000] = 8
b5["bit"][2] = 3
o5 = ad.vorwaerts(b5)
tot5, _, info5 = V.berechne(o5, b5, V.VerlustOpt(adv_beta=0.0))
pruefe("Überlauf: genau eine räumliche Zeile ohne Logits, gezählt, Verlust endlich",
       int(info5["ueberlauf"].sum()) == 1 and not bool(info5["ok"][2]) and bool(torch.isfinite(tot5)),
       str(o5["coarse_da"].tolist()))
b5["zeiger_voll"] = True
o6 = ad.vorwaerts(b5)
pruefe("zeiger_voll: alle Zeilen haben Logits", bool(o6["coarse_da"].all()))

# ---------------------------------------------------------------- D1
import netz_d1 as N1  # noqa: E402
import zusatz_felder as ZF  # noqa: E402

MW, CA, DU = int(AC.A.MOVE_WARSHIP), int(AC.A.CANCEL_ATTACK), int(AC.A.DELETE_UNIT)


def batch1():
    b = batch()
    b["own"] = torch.randn(B, F.OWN_DIM + ZF.DIM_GLOBAL)
    b["opp"] = torch.randn(B, 24, F.OPP_DIM + ZF.DIM_OPP)
    e = torch.zeros(B, ZF.MAX_EINHEIT, ZF.DIM_EINHEIT)
    e[:, :5, 0] = torch.tensor([1.0, 2, 2, 12, 16])            # 5 besetzte Plätze
    e[:, :5, 1:3] = torch.rand(B, 5, 2)
    e[:, :5, 6] = torch.rand(B, 5)
    an = torch.zeros(B, ZF.MAX_ANGRIFF, ZF.DIM_ANGRIFF)
    an[:, :2, 0] = 0.5                                          # 2 besetzte Angriffe
    an[:, :2, 1] = torch.tensor([1.0, 25])                      # Gegnerplatz 1, kein Spieler
    b["einheiten"], b["angriffe"] = e, an
    b["labels"]["atype"] = torch.tensor([MW, CA, DU, 0])
    b["labels"]["own_ref"] = torch.tensor([2, 1, 4, D.IGNORE])
    return b


ad1 = N1.D1Adapter()
net1 = ad1.modul
print(f"D1: {sum(p.numel() for p in net1.parameters()) / 1e6:.2f} Mio Parameter")
c1 = batch1()
o1 = ad1.vorwaerts(c1)
pruefe("D1: alle Köpfe da, own_ref (B,128)",
       all(o1[h].shape == (B, k) for h, k in ad1.kopf_groessen.items()) and o1["own_ref"].shape == (B, 128))
besetzt = o1["own_ref"].detach() > N1.LEER / 2
pruefe("D1: move_warship zeigt nur auf die 5 besetzten Einheitenplätze", besetzt[0].nonzero().flatten().tolist() == [0, 1, 2, 3, 4])
pruefe("D1: cancel_attack zeigt nur auf die 2 besetzten Angriffe", besetzt[1].nonzero().flatten().tolist() == [0, 1])
t1, l1, _ = V.berechne(o1, c1, V.VerlustOpt(adv_beta=0.0))
pruefe("D1: Verlust endlich, own_ref gelernt", bool(torch.isfinite(t1)) and "own_ref" in l1 and float(l1["own_ref"]) > 0)
t1.backward()
# Wie bei D0: jeder Parameter hängt am Verlust. Nicht "Gradient ≠ 0": die Residualzweige des
# Kodierers starten als Identität (Norm-Gewicht 0), ihr Gradient ist zu Beginn exakt 0.
ohne1 = [n for n, p in net1.named_parameters() if p.grad is None]
pruefe("D1: jeder Parameter bekommt einen Gradienten", not ohne1, str(ohne1[:5]))
neu1 = [n for n, p in net1.named_parameters() if n.split(".")[0] in ("einh", "r_qu", "r_bu", "r_qa", "r_ba", "kern")]
null1 = [n for n in neu1 if float(dict(net1.named_parameters())[n].grad.abs().sum()) == 0]
pruefe("D1: die neuen Teile (Einheiten-Kodierer, Zeiger, Kern) lernen: Gradient ≠ 0", bool(neu1) and not null1,
       f"({len(neu1)} Tensoren, ohne Gradient {null1[:5]})")
with torch.no_grad():
    r1 = ad1.vorwaerts(c1)
    c2 = batch1()
    for k in ("map", "own", "opp", "config", "einheiten", "angriffe"):
        c2[k] = c1[k]
    c2["labels"]["atype"] = torch.tensor([DU, DU, MW, CA])
    c2["labels"]["own_ref"] = torch.tensor([0, 3, 1, 0])
    o2_ = ad1.vorwaerts(c2)
    pruefe("D1: atype hängt nicht an Labels", torch.equal(r1["atype"], o2_["atype"]))
    b2_ = (o2_["own_ref"] > N1.LEER / 2)
    pruefe("D1: dieselbe Zeile als delete_unit zeigt auf Einheiten statt Angriffe",
           b2_[1].nonzero().flatten().tolist() == [0, 1, 2, 3, 4])
    c3 = batch1()
    for k in ("map", "own", "opp", "config", "angriffe"):
        c3[k] = c1[k]
    c3["einheiten"] = c1["einheiten"].clone()
    c3["einheiten"][:, 2, 1:3] = torch.tensor([0.9, 0.9])      # Kriegsschiff auf Platz 2 woanders
    o3_ = ad1.vorwaerts(c3)
    pruefe("D1: Zeiger reagiert auf die Einheitenliste (Ort von Platz 2)",
           not torch.isclose(r1["own_ref"][0, 2], o3_["own_ref"][0, 2]))
    c4 = batch1()
    for k in ("map", "own", "opp", "config", "einheiten", "angriffe"):
        c4[k] = c1[k]
    del c4["einheiten"]
    try:
        ad1.vorwaerts(c4)
        pruefe("D1: ohne einheiten im Batch klare Fehlermeldung", False)
    except KeyError as e:
        pruefe("D1: KeyError nennt die Zusatzfelder", "einheiten" in str(e))

print("\n" + ("ALLES OK" if not fehler else f"{len(fehler)} FEHLER: {fehler}"))
sys.exit(1 if fehler else 0)
