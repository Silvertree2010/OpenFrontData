"""Streaming-Lader für die Ausgabe des Materialisierers v2 (Format 2, DESIGN.md §5).

Aufbau:
  finde_partien()  fertige Partien. Fertig heisst: die .ok besteht die
                   Grössenprüfung (reader.ok_problems), nie "eine .meta.zst ist da".
  PartieLeser      liest EINE Partie sequenziell: Metazeile, Kartenblock, Zellblock.
                   Die Blöcke bleiben komprimiert (~12 KB statt 292 KB je Karte),
                   bis der Batch gebaut wird. So kann der Mischpuffer gross sein.
  Strom            IterableDataset für den DataLoader. Worker w nimmt jede N-te
                   Partie, hält K Partien gleichzeitig offen, zieht zufällig aus
                   ihnen und schiebt die Samples durch einen Reservoir-Mischpuffer.
  sammle()         Liste von Samples → Batch aus CPU-Tensoren. Die Karte bleibt
                   uint8, dequantisiert wird auf der GPU (4× weniger Kopierlast).

Labels entstehen hier aus der Metazeile, wie im alten dataset.py per
actions.encode. Abweichungen, alle aus der Spezifikation (ZIELWAHL_ENTWURF §6):
  - Kachel-Labels (coarse/fine) aus res_tile statt aus dem rohen Klick.
    res_kind 2 (ungültig) fällt aus dem räumlichen Verlust. MIRV hat keinen.
  - Boot und Nukes bekommen den Zielspieler dst_owner als target (Entwurf §4.1).
  - Ein act-Sample, das actions.encode nicht kennt (z. B. toggle_pause), wird
    verworfen. Das alte dataset.py machte daraus stillschweigend NO_OP.
"""
from __future__ import annotations

import json
import operator
import os
import random
import struct
import sys
from collections import Counter, deque

import numpy as np
import torch
from torch.utils.data import IterableDataset, get_worker_info

HIER = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HIER)
for _p in (os.path.join(REPO, "materializer", "py"), os.path.join(REPO, "env"), HIER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import actions as AC          # noqa: E402  (env/)
import featurize as F         # noqa: E402  (env/)
import reader as R            # noqa: E402  (materializer/py/)
import tabellen as T          # noqa: E402
import zusatz_felder as ZF    # noqa: E402

HEADS = list(AC.HEAD_SIZES.keys())
HI = {h: i for i, h in enumerate(HEADS)}
NH = len(HEADS)
IGNORE = -100
NZ = R.GH * R.GW                      # 16200 Zellen
MAP_SHAPE = (R.MAP_CHANNELS, R.GH, R.GW)
RAUM_ATYPES = {int(AC.A.BUILD_UNIT), int(AC.A.BOAT), int(AC.A.SPAWN), int(AC.A.MOVE_WARSHIP)}
ENDE = object()
LSET_TICKS = 300                      # Entwurf §6: ±300 Ticks
LSET_K = 16                           # höchstens so viele weitere Zellen je Sample

# ---------------------------------------------------------------- Gegner-Featurisierung
# Vektorisierte Fassung von featurize.featurize_opps, gleiche Werte (ladertest prüft
# das auf echten Daten). Das Original ruft 19 Lambdas je Gegner auf und war im Profil
# fast die Hälfte der Laderzeit. Reihenfolge und Art müssen OPP_FEATURES entsprechen.
_OPP_SPEC = [("troops", "log", 16), ("gold", "log", 18), ("tiles", "log", 16),
             ("ally", "f", 0), ("sameTeam", "f", 0), ("traitor", "f", 0), ("human", "f", 0),
             ("bordersMe", "f", 0), ("contactShare", "c", 0), ("borderToMe", "c", 0),
             ("borderToOther", "c", 0), ("borderToUnowned", "c", 0), ("annexProof", "f", 0),
             ("allyTicksLeft", "c", 0), ("isLeader", "f", 0), ("hasSilo", "f", 0), ("hasSam", "f", 0),
             ("inClan", "f", 0), ("reputation", "rep", 0)]
assert [n for n, _, _ in _OPP_SPEC] == [n for n, _ in F.OPP_FEATURES], "OPP_FEATURES hat sich geändert"
_OPP_KEYS = [n for n, _, _ in _OPP_SPEC[:-1]]
_OPP_GET = operator.itemgetter(*_OPP_KEYS)
_LOG_J = [j for j, (_, a, _) in enumerate(_OPP_SPEC) if a == "log"]
_LOG_S = np.array([s for _, a, s in _OPP_SPEC if a == "log"], np.float64)
_CLIP_J = [j for j, (_, a, _) in enumerate(_OPP_SPEC) if a in ("c", "rep")]


def gegner_features(opps: list, rep: dict):
    """(MAX_OPP, OPP_DIM) float32 und Maske, wie F.featurize_opps nach _rep-Injektion."""
    n = min(len(opps), F.MAX_OPP)
    mat = np.zeros((F.MAX_OPP, F.OPP_DIM), np.float32)
    mask = np.zeros(F.MAX_OPP, bool)
    if n:
        mask[:n] = True
        zeilen = []
        for o in opps[:n]:
            try:
                z = list(_OPP_GET(o))                   # schnell, wenn alle Schlüssel da sind
            except KeyError:
                z = [o.get(k, 0) for k in _OPP_KEYS]
            z.append(rep.get(f"{o.get('user') or '?'}\x1f{o.get('clan') or ''}", 0.5))
            zeilen.append(z)
        roh = np.array(zeilen, np.float64)
        roh[:, _LOG_J] = np.log1p(np.fmax(roh[:, _LOG_J], 0.0)) / _LOG_S   # fmax wie max(0.0, x) bei NaN
        roh[:, _CLIP_J] = np.clip(roh[:, _CLIP_J], 0.0, 1.0)
        mat[:n] = roh
    return mat, mask


# ---------------------------------------------------------------- zstd
# Karten- und Zellblöcke haben eine bekannte Grösse. Mit max_output_size geht
# decompress auch bei Frames ohne Inhaltsgrösse im Kopf und ist schneller als
# decompressobj. Kontext je Prozess (nach fork nicht teilen).
_DCTX = {"pid": None, "ctx": None}


def _zdec_n(b: bytes, n: int) -> bytes:
    try:
        import zstandard as zs
    except ImportError:               # Python ≥ 3.14 ohne Paket: Rückfall auf reader
        return R.zdec(b)
    if _DCTX["pid"] != os.getpid():
        _DCTX["pid"], _DCTX["ctx"] = os.getpid(), zs.ZstdDecompressor()
    return _DCTX["ctx"].decompress(b, max_output_size=n)


def _block(f) -> bytes:
    kopf = f.read(4)
    if len(kopf) != 4:
        raise ValueError("Längenfeld abgeschnitten")
    (ln,) = struct.unpack("<I", kopf)
    b = f.read(ln)
    if len(b) != ln:
        raise ValueError("Block abgeschnitten")
    return b


# ---------------------------------------------------------------- Partien finden
def finde_partien(wurzeln: list[str], nur: set | None = None) -> dict[str, tuple[str, int]]:
    """gid → (Ordner, Samples) für alle fertigen Partien mit Samples.
    Eine Wurzel ist ein Ausgabeordner oder ein Ordner mit Ausgabeordnern (s0…s7).
    nur: nur diese gids berücksichtigen (Partienliste, waehle_partien.py)."""
    ordner = []
    for w in wurzeln:
        w = os.path.expanduser(w)
        namen = os.listdir(w)
        if any(n.endswith(".ok") for n in namen):
            ordner.append(w)
        else:
            ordner += sorted(os.path.join(w, n) for n in namen if os.path.isdir(os.path.join(w, n)))
    out: dict[str, tuple[str, int]] = {}
    for d in ordner:
        for n in os.listdir(d):
            if not n.endswith(".ok") or n[:-3] in out:
                continue
            gid = n[:-3]
            if nur is not None and gid not in nur:
                continue
            if R.ok_problems(d, gid):
                continue                  # (noch) nicht fertig
            ok = R._json(R.path_of(d, gid, "ok")) or {}
            if ok.get("samples", 0) > 0:
                out[gid] = (d, int(ok["samples"]))
    return out


# ---------------------------------------------------------------- Optionen
class LaderOpt:
    """Wie aus Metazeilen Samples werden. Wird in die Worker gepickelt."""

    def __init__(self, raum_label: str = "res", ziel_d0: bool = True, reputation: str | None = None,
                 lset: bool = False, schicht: dict | None = None, schicht_erhalten: bool = True,
                 deckel: int = 0, epoche: int = 0, saat: int = 0, zusatz: str | None = None,
                 spieler: dict | None = None):
        assert raum_label in ("res", "klick")
        self.raum_label = raum_label
        self.ziel_d0 = ziel_d0
        self.reputation = reputation
        self.lset = lset                      # weitere Label-Zellen für den L-set-Verlust sammeln
        self.schicht = schicht or {}          # Gruppen-Index → Vervielfachung im Strom
        self.schicht_erhalten = schicht_erhalten   # Gewichte durch k teilen: Ziel bleibt gleich
        # Deckel: höchstens N Samples je Partie. Jedes Sample wird mit p = min(1, N/Samples)
        # behalten, also über die ganze Partie verteilt (nicht die ersten N, das verschöbe
        # alles in die Frühphase). Die Gewichte bleiben, wie sie sind: der Deckel soll den
        # Einfluss langer Partien senken, nicht nur ihre Samples ausdünnen.
        self.deckel = deckel
        self.epoche = epoche                  # Ziehung je Epoche neu, siehe PartieLeser
        self.saat = saat                      # --seed des Laufs: andere Saat, andere Teilmenge
        # Ordner mit <gid>.zusatz.zst (zusatz/src/lauf.ts): die neuen Beobachtungsfelder je
        # Gegnerplatz werden HINTEN an die opp-Merkmale gehängt. Fehlt die Datei oder hat sie
        # weniger Felder, sind die fehlenden Werte 0 — alte Checkpoints bleiben gültig.
        self.zusatz = zusatz
        # Spielerfilter (train.py --spieler): gid → Menge von smallIDs. Nur Samples dieser
        # Spieler werden geliefert; alle anderen Zeilen werden gelesen (Gleichschritt der
        # Blöcke) und verworfen. None = alle Spieler (Standard, Verhalten unverändert).
        self.spieler = spieler


class ZusatzFehlt(Exception):
    """Partie ohne vollständige Zusatzdatei: wird übersprungen und gezählt, nie aufgefüllt."""


_REP = {"pfad": None, "tab": {}}


def _rep_tabelle(pfad):
    if _REP["pfad"] != pfad:
        tab = {}
        if pfad and os.path.exists(pfad):
            with open(pfad) as f:
                tab = json.load(f)
        _REP["pfad"], _REP["tab"] = pfad, tab
    return _REP["tab"]


# ---------------------------------------------------------------- eine Partie
class PartieLeser:
    """Liest eine Partie Zeile für Zeile. naechstes() liefert ein Sample (dict),
    None für ein verworfenes Sample oder ENDE. Zähler landen in z."""

    def __init__(self, ordner: str, gid: str, opt: LaderOpt, z: Counter):
        g = R.Game(ordner, gid)                   # wirft, wenn die .ok nicht besteht
        self.gid, self.opt, self.z = gid, opt, z
        with open(R.path_of(ordner, gid, "meta.zst"), "rb") as f:
            txt = R.zdec(f.read()).decode("utf-8")
        self.zeilen = [x for x in txt.split("\n") if x]
        if len(self.zeilen) != g.n:
            raise ValueError(f"{gid}: {len(self.zeilen)} Metazeilen, .ok sagt {g.n}")
        self.fm = open(R.path_of(ordner, gid, "maps"), "rb")
        pc = R.path_of(ordner, gid, "cells")
        self.fc = open(pc, "rb") if os.path.exists(pc) else None
        self.i = 0
        self.naechste_zelle = 0
        # Metazeilen werden genau einmal geparst. Für L-set wird bis t+300 vorausgelesen,
        # die vorausgelesenen dicts liegen in vorrat, die räumlichen Kurzsätze in fenster.
        self.vorrat: dict[int, dict] = {}
        self.geparst = 0
        self.letzter_tick = -1
        self.fenster: deque = deque()
        hdr = g.hdr or {}
        self.cfg = np.asarray(F.featurize_config(hdr.get("config") or {}), np.float32)
        # smallID → Spieler-IDs; oppIds sind playerIDs, zur Sicherheit auch clientIDs
        self.sid_ids = {p["sid"]: (p.get("playerID"), p.get("clientID")) for p in hdr.get("players", [])}
        self.bit1 = g.ok.get("legal_bit1", True) is not False
        self.rep = _rep_tabelle(opt.reputation)
        sp = getattr(opt, "spieler", None)
        self.nur_sid = None if sp is None else sp.get(gid, frozenset())
        # Deckel: Behaltewahrscheinlichkeit über die ganze Partie. Die Ziehung hängt an
        # (gid, Epoche), ist also je Epoche eine andere Teilmenge; über mehrere Epochen
        # sieht das Training so mehr von den langen Partien, je Epoche aber nie mehr als N.
        self.zus_o = self.zus_g = self.zus_e = self.zus_a = None
        self.zus_no = self.zus_ng = self.zus_ne = self.zus_na = 0
        if opt.zusatz:
            # Fester Bestandteil: ohne vollständige Zusatzdatei wird die Partie übersprungen
            # (ZusatzFehlt, im Strom gezählt), nie mit Nullen aufgefüllt.
            pz = os.path.join(os.path.expanduser(opt.zusatz), f"{gid}.zusatz.zst")
            if not os.path.exists(pz):
                raise ZusatzFehlt(f"{gid}: keine Zusatzdatei")
            try:
                kopf, wo, wg, we, wa = R.zusatz_lesen(pz)
            except Exception as e:                    # abgeschnitten oder kaputt: wie fehlend
                raise ZusatzFehlt(f"{gid}: Zusatzdatei unlesbar ({type(e).__name__}: {e})") from e
            if kopf is None:
                raise ZusatzFehlt(f"{gid}: keine Zusatzdatei")
            if kopf.get("samples") != g.n:
                raise ZusatzFehlt(f"{gid}: Zusatzdatei hat {kopf.get('samples')} Zeilen, .ok sagt {g.n}")
            self.zus_no, self.zus_ng, self.zus_ne, self.zus_na = ZF.passt(kopf)
            if (self.zus_no, self.zus_ng, self.zus_ne, self.zus_na) != ZF.DIMS:
                raise ZusatzFehlt(f"{gid}: Zusatzdatei unvollständig {(self.zus_no, self.zus_ng, self.zus_ne, self.zus_na)}")
            self.zus_o, self.zus_g = ZF.normiere_opp(wo), ZF.normiere_global(wg)
            self.zus_e, self.zus_a = ZF.normiere_einheit(we), ZF.normiere_angriff(wa)
        self.p_behalt = min(1.0, opt.deckel / g.n) if (opt.deckel and g.n > opt.deckel) else 1.0
        self.drng = random.Random((R.sha1_int(gid, 0, 12) << 8) ^ (opt.epoche * 2654435761)
                                  ^ (opt.saat * 40503))

    def schliessen(self):
        for f in (self.fm, self.fc):
            if f is not None:
                f.close()

    def naechstes(self):
        if self.i >= len(self.zeilen):
            if self.fm.read(1):
                raise ValueError(f"{self.gid}: mehr Kartenblöcke als Metazeilen")
            return ENDE
        i = self.i
        self.i += 1
        mblock = _block(self.fm)                  # immer lesen, sonst Versatz
        m = self._hole(i)
        c = m.get("cell", -1)
        cblock = None
        if c is not None and c >= 0:
            if c != self.naechste_zelle or self.fc is None:
                raise ValueError(f"{self.gid}: Zellindex {c}, erwartet {self.naechste_zelle}")
            cblock = _block(self.fc)
            self.naechste_zelle += 1
        if self.nur_sid is not None and m.get("sid") not in self.nur_sid:
            self.z["fremd_verworfen"] += 1     # anderer Spieler; Blöcke sind gelesen
            return None
        if self.p_behalt < 1.0 and self.drng.random() >= self.p_behalt:
            self.z["deckel_verworfen"] += 1    # Blöcke sind gelesen, der Gleichschritt bleibt
            return None
        try:
            return self._sample(m, mblock, cblock, i)
        except Exception as e:                    # ein kaputtes Sample kippt die Partie nicht
            self.z["verworfen_fehler"] += 1
            if self.z["verworfen_fehler"] == 1:
                self.z["erster_fehler:" + f"{type(e).__name__}: {e}"[:120]] += 1
            return None

    # -------------------------------------------------------- Metazeilen, L-set
    def _parse(self):
        j = self.geparst
        m = json.loads(self.zeilen[j])
        self.geparst += 1
        self.vorrat[j] = m
        self.letzter_tick = m.get("tick", 0)
        if self.opt.lset:
            tz = self._typ_zelle(m)
            if tz is not None:
                self.fenster.append((self.letzter_tick, j, m.get("sid"), tz[0], tz[1]))

    def _hole(self, i):
        while self.geparst <= i:
            self._parse()
        return self.vorrat.pop(i)

    def _typ_zelle(self, m):
        """(Typschlüssel, Zelle) eines räumlichen Samples mit gültigem Kachel-Label, sonst None."""
        it = m.get("intent") or {}
        g = T.gruppe(it)
        if g is None or T.R_KACHELN[g] is None or m.get("kind") == "noop":
            return None
        if self.opt.raum_label == "klick":
            tile = m.get("click", -1)
        else:
            tile = m.get("res_tile", -1) if m.get("res_kind", 2) in (0, 1) else -1
        if tile is None or tile < 0 or m.get("cell", -1) < 0:
            return None
        typ = it.get("unit") if it.get("type") == "build_unit" else it.get("type")
        return typ, AC.tile_encode(int(tile), int(m["mapW"]), int(m["mapH"]))[0]

    def _lset(self, i, m):
        """Entwurf §6 L-set: aufgelöste gleichartige Ziele desselben Spielers in ±300
        Ticks (ohne die eigene Zelle). Ob sie zum Zeitpunkt t legal sind, prüft der
        Verlust mit der Maske des Samples."""
        tz = self._typ_zelle(m)
        if tz is None:
            return None
        t = m.get("tick", 0)
        while self.geparst < len(self.zeilen) and self.letzter_tick <= t + LSET_TICKS:
            self._parse()
        while self.fenster and self.fenster[0][0] < t - LSET_TICKS:
            self.fenster.popleft()
        sid, out = m.get("sid"), []
        for tk, j, s, typ, c in self.fenster:
            if tk > t + LSET_TICKS:
                break
            if j != i and s == sid and typ == tz[0] and c != tz[1] and c not in out:
                out.append(c)
                if len(out) == LSET_K:
                    break
        a = np.full(LSET_K, -1, np.int64)
        a[:len(out)] = out
        return a

    # -------------------------------------------------------- Sample bauen
    def _sample(self, m, mblock, cblock, i):
        z, opt = self.z, self.opt
        lab = np.full(NH, IGNORE, np.int64)
        kind = m.get("kind")
        it = m.get("intent") or {}
        W, H = int(m["mapW"]), int(m["mapH"])
        grp, xy, zelle = -1, (0.0, 0.0), None
        ctx = AC.Context(map_w=W, map_h=H, troops=m.get("troops", 0), gold=m.get("gold", 0),
                         opp_ids=m.get("oppIds") or [], own_unit_ids=m.get("ownUnitIds") or [],
                         own_attack_ids=m.get("ownAttackIds") or [])
        # Trajektorien aus der Arena (trajektorie.py) tragen in "lab" die Köpfe, die die
        # Verhaltenspolitik wirklich gewählt hat; ihre Log-Wahrscheinlichkeit steht in rl.logp_*.
        # Sie gelten statt der aus dem Intent zurückgerechneten Labels (siehe unten).
        rl_lab = m.get("lab")
        if kind == "noop":
            lab[HI["atype"]] = int(AC.A.NO_OP)
            z["noop"] += 1
        else:
            act = AC.encode(it, ctx)
            if act.atype == AC.A.NO_OP and rl_lab is None:   # unbekannter Typ / unbekannte Einheit
                z["verworfen_unbekannt"] += 1
                return None
            lab[HI["atype"]] = int(act.atype)
            for h in AC.HEAD_SCHEMA.get(AC.A(act.atype), ()):
                v = getattr(act, h)
                if v is not None and v >= 0:      # UNRESOLVED (-1) → nicht supervidieren
                    lab[HI[h]] = int(v)
            z["act"] += 1
            if int(act.atype) in RAUM_ATYPES:
                g = T.gruppe(it)
                lab[HI["coarse"]] = lab[HI["fine"]] = IGNORE    # Klick-Label ersetzen
                if g is not None:
                    grp = T.GID[g]
                    z["raum_" + g] += 1
                    if T.R_KACHELN[g] is None:
                        z["raum_ohne_verlust_" + g] += 1
                    else:
                        if opt.raum_label == "klick":
                            tile = m.get("click", -1)
                        else:
                            tile = m.get("res_tile", -1) if m.get("res_kind", 2) in (0, 1) else -1
                        if tile is None or tile < 0:
                            z["raum_res_ungueltig"] += 1
                        elif cblock is None:
                            z["raum_ohne_zellen"] += 1
                        else:
                            c, f = AC.tile_encode(int(tile), W, H)
                            lab[HI["coarse"]], lab[HI["fine"]] = c, f
                            xy = (float(tile % W), float(tile // W))
                            zelle = cblock
                    if opt.ziel_d0 and g in T.ZIEL_GRUPPEN:
                        self._ziel(m, ctx, lab)
        if rl_lab is not None:
            lab[:] = IGNORE
            for h, v in rl_lab.items():
                lab[HI[h]] = int(v)
            if lab[HI["coarse"]] == IGNORE:           # kein ausgeführter Zellzeiger: keine Zelle
                zelle = None
            z["rl_zeilen"] += 1
        # Kopfgrenzen prüfen: ein Label ausserhalb der Klassen würde CE sprengen
        for h, j in HI.items():
            v = lab[j]
            if v != IGNORE and not (0 <= v < AC.HEAD_SIZES[h]):
                z["label_ausser_bereich_" + h] += 1
                lab[j] = IGNORE
        opp_mat, mask = gegner_features(m.get("opps") or [], self.rep)
        own_vec = np.asarray(F.featurize_own(m.get("own") or {}), np.float32)
        einheiten = angriffe = None
        if opt.zusatz:                        # Datei ist vollständig (PartieLeser prüft das)
            opp_mat = np.concatenate([opp_mat, self.zus_o[i]], 1)
            own_vec = np.concatenate([own_vec, self.zus_g[i]])
            # Kopien, keine Sichten: eine Sicht hielte im Mischpuffer die ganze Partie im Speicher
            einheiten, angriffe = self.zus_e[i].copy(), self.zus_a[i].copy()
            self._own_ref_pruefen(lab, einheiten, angriffe)
        g_name = T.GRUPPEN[grp] if grp >= 0 else None
        bit = (T.BIT if self.bit1 else T.BIT_OHNE_B1)[g_name] if g_name else 0
        return {"map": mblock, "cell": zelle, "own": own_vec, "opp": opp_mat, "mask": mask,
                "einheiten": einheiten, "angriffe": angriffe,
                "cfg": self.cfg, "lab": lab,
                "w": float(m.get("w", 1.0)), "wt": float(m.get("w_tick", 1.0)),
                "win": float(m.get("win", 0) or 0), "g": grp, "bit": bit,
                # RL-Trajektorien: Endbelohnung R der Episode (Ziel des Wertkopfs, rl_train.py)
                "ret": float((m.get("rl") or {}).get("R", 0.0) or 0.0),
                "xy": xy, "wh": (float(W), float(H)), "dst": int(m.get("dst_owner") or 0),
                "lset": self._lset(i, m) if (opt.lset and zelle is not None) else None,
                "sid": m.get("sid"), "id": (self.gid, i)}

    def _own_ref_pruefen(self, lab, einheiten, angriffe):
        """own_ref muss auf einen besetzten Platz zeigen (Einheit: art > 0, Angriff: truppen > 0),
        sonst IGNORE — der Zeiger soll nie lernen, ins Leere zu zeigen. Gezählt je Aktionstyp.
        Den Index definiert actions.encode: cancel_attack in ownAttackIds, sonst ownUnitIds."""
        ART_KRIEGSSCHIFF = 2                  # TYP_LISTE in zusatz/src/felder.ts: 1 Boot, 2 Kriegsschiff
        j = int(lab[HI["own_ref"]])
        if j == IGNORE:
            return
        at = int(lab[HI["atype"]])
        if at == int(AC.A.CANCEL_ATTACK):
            ok = j < ZF.MAX_ANGRIFF and angriffe[j, 0] > 0
        else:
            ok = j < ZF.MAX_EINHEIT and einheiten[j, 0] > 0
            if ok and at == int(AC.A.MOVE_WARSHIP) and int(round(float(einheiten[j, 0]))) != ART_KRIEGSSCHIFF:
                self.z["own_ref_kein_kriegsschiff"] += 1
        self.z[f"own_ref_{'ok' if ok else 'leer'}_{AC.A(at).name.lower()}"] += 1
        if not ok:
            lab[HI["own_ref"]] = IGNORE

    def _ziel(self, m, ctx, lab):
        """Entwurf D0: Zielspieler = Besitzer der Klickkachel (dst_owner)."""
        d = m.get("dst_owner")
        if d is None:
            return
        if d == 0:
            lab[HI["target"]] = AC.TGT_NEUTRAL
            self.z["ziel_neutral"] += 1
            return
        if d == m.get("sid"):
            self.z["ziel_eigen"] += 1
            return
        for pid in self.sid_ids.get(d, ()):
            if pid is not None and pid in ctx.opp_ids:
                j = ctx.opp_ids.index(pid)
                if j < AC.MAX_OPP:
                    lab[HI["target"]] = j
                    self.z["ziel_gegner"] += 1
                    return
        self.z["ziel_nicht_in_top24"] += 1


# ---------------------------------------------------------------- Batch bauen
def sammle(liste: list[dict]) -> dict:
    """Samples → CPU-Tensoren. Karte uint8, Zellfakten nur für räumliche Zeilen,
    sonst Nullen (legal 0 heisst: keine Maske, die Zeile zählt nicht)."""
    B = len(liste)
    maps = np.empty((B,) + MAP_SHAPE, np.uint8)
    legal = np.zeros((B, NZ), np.uint8)
    owner = np.zeros((B, NZ), np.int16)
    ownf = np.zeros((B, NZ), np.uint8)
    lset = np.full((B, LSET_K), -1, np.int64)
    for i, s in enumerate(liste):
        if s.get("lset") is not None:
            lset[i] = s["lset"]
        roh = s.get("roh", False)                 # Inferenz (spielen.py): Blöcke schon entpackt
        maps[i] = np.frombuffer(s["map"] if roh else _zdec_n(s["map"], R.MAP_BYTES), np.uint8).reshape(MAP_SHAPE)
        if s["cell"] is not None:
            raw = s["cell"] if roh else _zdec_n(s["cell"], R.CELL_BYTES)
            owner[i] = np.frombuffer(raw, "<u2", count=NZ).astype(np.int16)
            ownf[i] = np.frombuffer(raw, np.uint8, count=NZ, offset=2 * NZ)
            legal[i] = np.frombuffer(raw, np.uint8, count=NZ, offset=3 * NZ)
    t = torch.from_numpy
    return {
        "map_u8": t(maps),
        "own": t(np.stack([s["own"] for s in liste])),
        "opp": t(np.stack([s["opp"] for s in liste])),
        "opp_mask": t(np.stack([s["mask"] for s in liste])),
        "config": t(np.stack([s["cfg"] for s in liste])),
        "lab": t(np.stack([s["lab"] for s in liste])),
        "w": t(np.array([s["w"] for s in liste], np.float32)),
        "wt": t(np.array([s["wt"] for s in liste], np.float32)),
        "win": t(np.array([s["win"] for s in liste], np.float32)),
        "ret": t(np.array([s.get("ret", 0.0) for s in liste], np.float32)),
        "g": t(np.array([s["g"] for s in liste], np.int64)),
        "bit": t(np.array([s["bit"] for s in liste], np.int64)),
        "xy": t(np.array([s["xy"] for s in liste], np.float32)),
        "wh": t(np.array([s["wh"] for s in liste], np.float32)),
        "dst": t(np.array([s["dst"] for s in liste], np.int64)),
        "legal": t(legal), "owner": t(owner), "own_frac": t(ownf), "lset": t(lset),
        "ids": [s["id"] for s in liste],
        # eigene Einheiten und Angriffe (nur mit --zusatz), Reihenfolge wie own_ref
        **({"einheiten": t(np.stack([s["einheiten"] for s in liste])),
            "angriffe": t(np.stack([s["angriffe"] for s in liste]))}
           if liste[0].get("einheiten") is not None else {}),
    }


def auf_geraet(b: dict, dev) -> dict:
    """CPU-Batch → Gerät. Dequantisierung wie env/dataset.dequantize (u8/127,5 − 1),
    damit alte Checkpoints dieselben Eingaben sehen."""
    g = {k: v.to(dev, non_blocking=True) for k, v in b.items()
         if torch.is_tensor(v) and k not in ("map_u8", "lab")}
    g["map"] = b["map_u8"].to(dev, non_blocking=True).float().div_(127.5).sub_(1.0)
    lab = b["lab"].to(dev, non_blocking=True)
    g["labels"] = {h: lab[:, j] for h, j in HI.items()}
    return g


# ---------------------------------------------------------------- Strom
class Strom(IterableDataset):
    """Samples aus vielen Partien, gemischt. Jeder Batch meldet in "fertig" die
    Partien, die dieser Worker seit dem letzten Batch vollständig in seinen Puffer
    gelesen hat. Der Trainer merkt sie sich für das Fortsetzen: fertig gelesene
    Partien werden nach einem Neustart übersprungen. Was beim Abbruch noch im
    Puffer lag, ist verloren (höchstens puffer Samples je Worker)."""

    def __init__(self, partien: list[tuple[str, str]], batch: int, puffer: int, offen: int,
                 seed: int, opt: LaderOpt):
        self.partien, self.batch, self.puffer, self.offen = partien, batch, puffer, offen
        self.seed, self.opt = seed, opt

    def _kopien(self, s, z):
        """Geschichteter Sampler (Entwurf §6, seltene Typen): ein Sample einer Gruppe
        mit Faktor k geht k-mal in den Mischpuffer. Mit schicht_erhalten tragen die
        Kopien je w/k und w_tick/k, das Lernziel bleibt also gleich, nur die
        Varianz für seltene Typen sinkt. Sonst werden sie k-fach angehoben."""
        k = self.opt.schicht.get(s["g"], 1) if s["g"] >= 0 else 1
        if k <= 1:
            return (s,)
        z["schicht_kopien"] += k - 1
        if self.opt.schicht_erhalten:
            s = dict(s, w=s["w"] / k, wt=s["wt"] / k)
        return (s,) * k

    def __iter__(self):
        wi = get_worker_info()
        wid, nw = (wi.id, wi.num_workers) if wi else (0, 1)
        meine = self.partien[wid::nw]
        rng = random.Random(self.seed * 7919 + wid)
        z: Counter = Counter()
        offen: list[PartieLeser] = []
        quelle = iter(meine)
        buf: list[dict] = []
        cur: list[dict] = []
        fertig: list[str] = []

        def batch_raus():
            nonlocal cur, fertig
            b = sammle(cur)
            b["fertig"], b["zaehler"] = fertig, dict(z)
            cur, fertig = [], []
            z.clear()          # leeren, nicht neu binden: offene PartieLeser zählen in dasselbe Objekt
            return b

        while True:
            while len(offen) < self.offen:
                try:
                    gid, d = next(quelle)
                except StopIteration:
                    break
                try:
                    offen.append(PartieLeser(d, gid, self.opt, z))
                    z["partien"] += 1
                except ZusatzFehlt:
                    z["zusatz_fehlt"] += 1              # übersprungen, nie aufgefüllt; keine Meldung je Partie
                    fertig.append(gid)
                except Exception as e:
                    z["partie_fehler"] += 1
                    print(f"[lader] Partie {gid} übersprungen: {e}", flush=True)
                    fertig.append(gid)
            if not offen:
                break
            j = rng.randrange(len(offen))
            try:
                s = offen[j].naechstes()
            except Exception as e:                # Datei kaputt: Rest der Partie verwerfen
                z["partie_fehler"] += 1
                print(f"[lader] Partie {offen[j].gid} abgebrochen: {e}", flush=True)
                s = ENDE
            if s is ENDE:
                fertig.append(offen[j].gid)
                offen.pop(j).schliessen()
                continue
            if s is None:
                continue
            for s in self._kopien(s, z):
                if len(buf) < self.puffer:
                    buf.append(s)
                    continue
                k = rng.randrange(self.puffer)
                cur.append(buf[k])
                buf[k] = s
                if len(cur) == self.batch:
                    yield batch_raus()
        rng.shuffle(buf)
        for s in buf:
            cur.append(s)
            if len(cur) == self.batch:
                yield batch_raus()
        if cur:
            yield batch_raus()
        elif fertig or z:                 # nur Meldungen, keine Samples (z. B. leere Partien)
            yield {"leer": True, "fertig": fertig, "zaehler": dict(z)}


# ---------------------------------------------------------------- Eval-Auswahl
def _reservoir(lst, s, n, k, rng):
    if len(lst) < k:
        lst.append(s)
    else:
        j = rng.randrange(n)
        if j < k:
            lst[j] = s


def _eval_partie(arg):
    """Eine Val-Partie: bis zu k_allg zufällige Samples jeder Art und je Gruppe
    bis zu 12 räumliche Samples mit gültigem Label (Entwurf §8). Deterministisch."""
    gid, d, opt, k_allg = arg
    rng = random.Random(R.sha1_int(gid, 16, 24))
    z: Counter = Counter()
    allg: list[dict] = []
    raum: dict[int, list] = {}
    n_raum: Counter = Counter()
    n = 0
    try:
        leser = PartieLeser(d, gid, opt, z)
        while True:
            s = leser.naechstes()
            if s is ENDE:
                break
            if s is None:
                continue
            n += 1
            _reservoir(allg, s, n, k_allg, rng)
            if s["cell"] is not None:
                n_raum[s["g"]] += 1
                _reservoir(raum.setdefault(s["g"], []), s, n_raum[s["g"]], T.MAX_JE_PARTIE_TYP, rng)
        leser.schliessen()
    except ZusatzFehlt:
        z["zusatz_fehlt"] += 1                   # Val-Partie ohne Zusatzdatei: nicht im Eval-Set
    except Exception as e:
        z["partie_fehler"] += 1
        print(f"[eval] Partie {gid} übersprungen: {e}", flush=True)
    return gid, allg, raum, dict(n_raum), dict(z)


def waehle_eval(partien: list[tuple[str, str]], opt: LaderOpt, n_proc: int,
                k_allg: int = 32, allg_max: int = 8000, seed: int = 0) -> dict:
    """Festes Eval-Set aus Val-Partien: "allg" (alle Köpfe) und "raum" (geschichtet
    nach EVAL_SCHICHTEN, höchstens 12 je Partie und Gruppe). "anteile" zählt ALLE
    gültigen räumlichen Labels der Val-Partien je Gruppe, für das gewichtete Mittel."""
    import multiprocessing as mp
    args = [(gid, d, opt, k_allg) for gid, d in partien]
    if n_proc > 1 and len(args) > 1:
        with mp.get_context("fork").Pool(n_proc) as pool:
            res = pool.map(_eval_partie, args, chunksize=1)
    else:
        res = [_eval_partie(x) for x in args]
    allg, anteile, z, je_gruppe = [], Counter(), Counter(), {}
    for gid, a_, raum, n_raum, zz in sorted(res, key=lambda r: r[0]):
        allg += a_
        anteile.update(n_raum)
        z.update(zz)
        for g, lst in raum.items():
            je_gruppe.setdefault(g, []).extend(lst)
    rng = random.Random(seed)
    rng.shuffle(allg)
    allg = allg[:allg_max]
    raum_out, soll_ist = [], {}
    for name, (gs, soll) in T.EVAL_SCHICHTEN.items():
        kand = [s for g in gs for s in je_gruppe.get(T.GID[g], [])]
        kand.sort(key=lambda s: s["id"])
        rng.shuffle(kand)
        raum_out += kand[:soll]
        soll_ist[name] = (soll, min(soll, len(kand)))
    return {"allg": allg, "raum": raum_out,
            "anteile": {T.GRUPPEN[g]: n for g, n in anteile.items()},
            "schichten": soll_ist, "zaehler": dict(z), "partien": len(partien)}
