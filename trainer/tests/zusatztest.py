"""Zusatzdateien prüfen: deckungsgleich zu den Metazeilen, Werte plausibel, Lader hängt an.

Je Partie:
  1. Kopf: Format, gid, Samples = Metazeilen, beide Namenslisten wie trainer/zusatz_felder.py,
     Formen der zwei Blöcke.
  2. Deckungsgleichheit: Plätze jenseits der Gegnerliste sind 0; Samples desselben Spielers
     im selben Tick haben identische Werte (sie teilen sich den Zustand).
  3. Plausibilität an echten Zügen: wer ablehnt, hatte eine anliegende Anfrage; nach eigener
     Anfrage steht anfrage_aus; nach einem Bündnisbruch steht verrat_an_mir beim Brecher;
     wer angreift, hat angriff_von_mir und angriff_aus_summe > 0; Bündnis heisst nicht
     automatisch freundlich (abgemeldete Verbündete).
  4. Lader: mit --zusatz hängen die Gegnerfelder hinten an opp, die globalen an own.

    python trainer/tests/zusatztest.py --pool ~/of-mat2-out --zusatz ~/of-mat2-zusatz
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import daten as D  # noqa: E402
import featurize as F  # noqa: E402
import reader as R  # noqa: E402
import zusatz_felder as ZF  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--pool", default="~/of-mat2-out")
ap.add_argument("--zusatz", default="~/of-mat2-zusatz")
ap.add_argument("--reputation", default=None)
ap.add_argument("gids", nargs="*")
a = ap.parse_args()
pool, zdir = os.path.expanduser(a.pool), os.path.expanduser(a.zusatz)
gids = a.gids or sorted(f[:-len(".zusatz.zst")] for f in os.listdir(zdir) if f.endswith(".zusatz.zst"))
fehler, z = [], Counter()
J = {n: i for i, n in enumerate(ZF.NAMEN_OPP)}
G = {n: i for i, n in enumerate(ZF.NAMEN_GLOBAL)}
E = {n: i for i, n in enumerate(ZF.NAMEN_EINHEIT)}
A = {n: i for i, n in enumerate(ZF.NAMEN_ANGRIFF)}


def pruefe(name, bed, info=""):
    print(f"{'ok  ' if bed else 'FEHL'} {name} {info}")
    if not bed:
        fehler.append(name)


def ordner_von(gid):
    for n in sorted(os.listdir(pool)):
        if os.path.exists(os.path.join(pool, n, f"{gid}.ok")):
            return os.path.join(pool, n)
    raise FileNotFoundError(gid)


for gid in gids:
    d = ordner_von(gid)
    spiel = R.Game(d, gid)
    meta = spiel.meta()
    kopf, w, wg, we, wa = R.zusatz_lesen(os.path.join(zdir, f"{gid}.zusatz.zst"))
    ko, kg, ke, ka = ZF.passt(kopf)
    pruefe(f"{gid}: Kopf und Formen", kopf["format"] == 2 and kopf["gid"] == gid
           and kopf["samples"] == len(meta) and w.shape == (len(meta), 24, ko) and wg.shape == (len(meta), kg)
           and we.shape == (len(meta), ZF.MAX_EINHEIT, ke) and wa.shape == (len(meta), ZF.MAX_ANGRIFF, ka),
           f"({len(meta)} Zeilen, {ko}+{kg}+{ke}+{ka} Felder, {kopf['dtype']}, "
           f"{os.path.getsize(os.path.join(zdir, gid + '.zusatz.zst')) / len(meta):.0f} B/Sample)")
    leer_ok, gleich_ok = True, True
    jeTickSid = {}
    for i, m in enumerate(meta):
        n_opp = len(m.get("opps") or [])
        if n_opp < 24 and np.any(w[i, n_opp:] != 0):
            leer_ok = False
        s = jeTickSid.setdefault((m["tick"], m["sid"]), i)
        if s != i and not (np.array_equal(w[i], w[s]) and np.array_equal(wg[i], wg[s])):
            gleich_ok = False
    pruefe(f"{gid}: Plätze jenseits der Gegnerliste sind 0", leer_ok)
    pruefe(f"{gid}: gleicher Spieler und Tick, gleiche Werte", gleich_ok)

    sid_von_pid = {p["playerID"]: p["sid"] for p in (spiel.hdr.get("players") or [])}
    offene_anfrage, verraten = {}, {}
    for i, m in enumerate(meta):
        it = m.get("intent") or {}
        opp = m.get("oppIds") or []
        typ = it.get("type")
        if typ == "allianceReject" and it.get("requestor") in opp:
            z["ablehnung"] += 1
            z["ablehnung_ohne_anfrage"] += int(w[i, opp.index(it["requestor"]), J["anfrage_ein"]] != 1)
        if typ == "allianceRequest" and it.get("recipient") in opp:
            offene_anfrage[(m["sid"], it["recipient"])] = m["tick"]
        if typ == "breakAlliance" and it.get("recipient") in sid_von_pid:
            verraten.setdefault(sid_von_pid[it["recipient"]], {})[m["sid"]] = m["tick"]
        for (sid, pid), t0 in list(offene_anfrage.items()):
            if m["sid"] != sid or pid not in opp or m["tick"] <= t0:
                continue
            if m["tick"] - t0 > 10:                      # später kann sie angenommen, abgelaufen
                del offene_anfrage[(sid, pid)]           # oder durch einen Angriff verworfen sein
                continue
            j = opp.index(pid)
            z["spaeter_nach_anfrage"] += 1
            # gleich danach: Anfrage liegt an ODER das Bündnis kam schon zustande
            fehlt = int(w[i, j, J["anfrage_aus"]] != 1 and w[i, j, J["verbuendet"]] != 1)
            z["anfrage_aus_fehlt"] += fehlt
            # nicht angelegt: die Engine lehnt createAllianceRequest ab (Sperre, schon verbündet …)
            z["anfrage_nicht_erlaubt"] += fehlt and int(w[i, j, J["kann_anfragen"]] != 1)
            del offene_anfrage[(sid, pid)]
        for brecher_sid, t0 in (verraten.get(m["sid"]) or {}).items():
            if m["tick"] <= t0:
                continue
            p = [j for j, o in enumerate(m.get("opps") or []) if o.get("id") == brecher_sid]
            if p:
                z["spaeter_nach_bruch"] += 1
                z["verrat_fehlt"] += int(w[i, p[0], J["verrat_an_mir"]] != 1)
        if m.get("ownAttackIds"):
            z["mit_angriff"] += 1
            z["angriff_sichtbar"] += int(np.any(w[i, :, J["angriff_von_mir"]] > 0))
            z["angriff_global"] += int(wg[i, G["angriff_aus_summe"]] > 0)
        z["samples"] += 1
        z["anfrage_ein_da"] += int(np.any(w[i, :, J["anfrage_ein"]] > 0))
        z["verbuendet_da"] += int(np.any(w[i, :, J["verbuendet"]] > 0))
        z["verbuendet_unfreundlich"] += int(np.any((w[i, :, J["verbuendet"]] > 0) & (w[i, :, J["freundlich"]] == 0)))
        z["abgemeldet_da"] += int(np.any(w[i, :, J["abgemeldet"]] > 0))
        z["raketen_da"] += int(wg[i, G["raketen_ein"]] > 0)
        z["kosten_da"] += int(wg[i, G["kosten_city"]] > 0)
        z["gebunden_da"] += int(wg[i, G["gebundene_truppen"]] > 0)
        # Einheitenliste: Zahl der gefüllten Plätze und own_ref-Ziele
        n_u = min(len(m.get("ownUnitIds") or []), ZF.MAX_EINHEIT)
        gefuellt = int((we[i, :, E["art"]] > 0).sum())
        z["einheiten_plaetze"] += n_u
        z["einheiten_gefuellt"] += gefuellt
        z["einheiten_luecke"] += max(0, n_u - gefuellt)
        if typ == "cancel_attack" and it.get("attackID") in (m.get("ownAttackIds") or []):
            j = m["ownAttackIds"].index(it["attackID"])
            if j < ZF.MAX_ANGRIFF:
                z["own_ref_angriff"] += 1
                z["own_ref_angriff_leer"] += int(wa[i, j, A["truppen"]] <= 0)
        for schl in ("unitID", "unitId"):
            if typ in ("cancel_boat", "delete_unit", "upgrade_structure") and it.get(schl) in (m.get("ownUnitIds") or []):
                j = m["ownUnitIds"].index(it[schl])
                if j < ZF.MAX_EINHEIT:
                    z["own_ref_einheit"] += 1
                    z["own_ref_einheit_leer"] += int(we[i, j, E["art"]] <= 0)
        if typ == "move_warship" and (it.get("unitIds") or [None])[0] in (m.get("ownUnitIds") or []):
            j = m["ownUnitIds"].index(it["unitIds"][0])
            if j < ZF.MAX_EINHEIT:
                z["own_ref_warship"] += 1
                z["own_ref_warship_falsch"] += int(we[i, j, E["art"]] != 2)   # 2 = Warship in TYP_LISTE
        if wg[i, G["sam_alle"]] > 0:                     # SAM-Abdeckung in der Karte prüfen
            z["sam_samples"] += 1
            z["sam_alle"] += float(wg[i, G["sam_alle"]])
            z["sam_nicht_bereit"] += float(wg[i, G["sam_nicht_bereit"]])

    lz = D.PartieLeser(d, gid, D.LaderOpt(reputation=a.reputation, zusatz=zdir), Counter())
    n, passt = 0, True
    while n < 40:
        s = lz.naechstes()
        if s is D.ENDE:
            break
        if s is None:
            continue
        i = s["id"][1]
        soll_o = np.zeros((24, ZF.DIM_OPP), np.float32)
        soll_o[:, :ko] = ZF.normiere_opp(w[i])
        soll_g = np.zeros(ZF.DIM_GLOBAL, np.float32)
        soll_g[:kg] = ZF.normiere_global(wg[i])
        soll_e = np.zeros((ZF.MAX_EINHEIT, ZF.DIM_EINHEIT), np.float32)
        soll_e[:, :ke] = ZF.normiere_einheit(we[i])
        passt &= (s["opp"].shape == (24, F.OPP_DIM + ZF.DIM_OPP)
                  and s["own"].shape == (F.OWN_DIM + ZF.DIM_GLOBAL,)
                  and s["einheiten"].shape == (ZF.MAX_EINHEIT, ZF.DIM_EINHEIT)
                  and s["angriffe"].shape == (ZF.MAX_ANGRIFF, ZF.DIM_ANGRIFF)
                  and np.array_equal(s["opp"][:, F.OPP_DIM:], soll_o)
                  and np.array_equal(s["own"][F.OWN_DIM:], soll_g)
                  and np.array_equal(s["einheiten"], soll_e))
        n += 1
    lz.schliessen()
    pruefe(f"{gid}: Lader hängt normiert hinten an (opp und own)", passt and n > 0, f"({n} Samples)")

pruefe("Ablehnung hatte anliegende Anfrage", z["ablehnung_ohne_anfrage"] == 0,
       f"({z['ablehnung']} Ablehnungen, {z['ablehnung_ohne_anfrage']} ohne)")
# Toleranz: die Engine legt eine Anfrage nicht immer an (Sperre, schon verbündet, Ziel weg),
# und ein Angriff verwirft anliegende Anfragen des Ziels (AttackExecution.init).
pruefe("eigene Anfrage gleich danach sichtbar", z["anfrage_aus_fehlt"] <= 0.05 * max(1, z["spaeter_nach_anfrage"]),
       f"({z['spaeter_nach_anfrage']} Fälle, {z['anfrage_aus_fehlt']} ohne, davon "
       f"{z['anfrage_nicht_erlaubt']} mit kann_anfragen = 0)")
pruefe("Verrat später sichtbar", z["verrat_fehlt"] == 0,
       f"({z['spaeter_nach_bruch']} Fälle, {z['verrat_fehlt']} ohne)")
pruefe("eigener Angriff sichtbar (Gegnerplatz)", z["mit_angriff"] == 0 or z["angriff_sichtbar"] > 0.5 * z["mit_angriff"],
       f"({z['angriff_sichtbar']} von {z['mit_angriff']}; Rest zielt auf Herrenloses oder ausserhalb der Top 24)")
pruefe("eigener Angriff sichtbar (global)", z["mit_angriff"] == 0 or z["angriff_global"] > 0.9 * z["mit_angriff"],
       f"({z['angriff_global']} von {z['mit_angriff']})")
pruefe("Baukosten vorhanden", z["kosten_da"] > 0.9 * z["samples"], f"({z['kosten_da']} von {z['samples']})")
pruefe("Einheitenliste vollständig", z["einheiten_luecke"] <= 0.01 * max(1, z["einheiten_plaetze"]),
       f"({z['einheiten_gefuellt']} von {z['einheiten_plaetze']} Plätzen gefüllt)")
pruefe("own_ref zeigt auf einen Angriff", z["own_ref_angriff_leer"] == 0,
       f"({z['own_ref_angriff']} cancel_attack, {z['own_ref_angriff_leer']} ins Leere)")
pruefe("own_ref zeigt auf eine Einheit", z["own_ref_einheit_leer"] == 0,
       f"({z['own_ref_einheit']} Fälle, {z['own_ref_einheit_leer']} ins Leere)")
pruefe("move_warship zeigt auf ein Kriegsschiff", z["own_ref_warship_falsch"] == 0,
       f"({z['own_ref_warship']} Fälle, {z['own_ref_warship_falsch']} auf anderer Art)")
n = max(1, z["samples"])
print(f"Verteilung über {z['samples']} Samples: anliegende Anfrage {z['anfrage_ein_da'] / n:.1%}, "
      f"verbündet {z['verbuendet_da'] / n:.1%}, davon unfreundlich (abgemeldet) "
      f"{z['verbuendet_unfreundlich'] / n:.1%}, abgemeldeter Gegner {z['abgemeldet_da'] / n:.1%}, "
      f"Rakete im Anflug {z['raketen_da'] / n:.1%}, gebundene Truppen {z['gebunden_da'] / n:.1%}")
if z["sam_alle"]:
    print(f"SAM-Abdeckung: {z['sam_nicht_bereit'] / z['sam_alle']:.1%} der aktiven SAMs sind im Bau "
          f"oder im Nachladen und werden trotzdem in die Karte gestempelt "
          f"({z['sam_samples']} Samples mit SAM, im Mittel {z['sam_alle'] / max(1, z['sam_samples']):.1f} SAMs)")
print("\n" + ("ALLES OK" if not fehler else f"{len(fehler)} FEHLER: {fehler}"))
sys.exit(1 if fehler else 0)
