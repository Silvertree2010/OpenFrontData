#!/usr/bin/env python3
"""Menschen-Referenz für die Spielweise: dieselben Kennzahlen wie spielweise.ts, für echte
Menschen aus dem Materialisierer-Pool, ohne Engine-Replay.

  python arena/menschen.py --pool ~/of-mat2-out --zusatz ~/zusatz/alle \\
      --karten ~/openfront-client-arena/resources/maps --stichprobe 120 \\
      --ur-pool ~/of-ur/pool --ur-zusatz ~/of-ur/zusatz --ur-liste ~/of-ur/spieler_ur.tsv \\
      --aus ~/d1-lauf/logs/menschen_spielweise.json [--records DIR] [--jobs 6] [--max-partien 2]

Gruppen: "mensch" = alle Menschen in einer festen Stichprobe der Val-Partien (sha1(gid) % 25 == 0,
nur Free For All, sortiert nach sha1, die ersten --stichprobe) — der Durchschnittsmensch, AFK-
Spieler eingeschlossen. "ultimus_rex" = seine Partien aus spieler_ur.tsv (gid, sid).

Quellen je Partie (Materialisierer v2, DESIGN §5): meta.zst (Samples jedes Menschen alle ≤ 200
Ticks und bei jeder Aktion: Gold, Truppen, own-Vektor, Verbündete, Intent), zusatz.zst (Zeile für
Zeile deckungsgleich: laufende Angriffe auf mich), Tier 1 own.zst (Besitzer jeder Kachel in jedem
Tick) und units.zst (Entstehen, Besitzer, Stufe, Ende jeder Einheit), dazu das Terrain aus
resources/maps (Uferbit). Optional GameRecords (--records) für die exakten Engine-Stats am Ende.

Herkunft je Kennzahl (steht auch in der Ausgabe unter "herkunft"):
  exakt        Truppen, Truppen-Quote (own.troopsRatio = troops/maxTroops der Engine), Gold-Bestand,
               Allianzen gleichzeitig/geschlossen (allies je Sample), Verrat (own.betrayals),
               Gebiet und Gebietsverteilung (own.zst, gleiche Formeln wie die Arena:
               verteilung.py, tests/verteilung_paritaet.py), Strukturen gebaut/verloren/erobert/
               aufgewertet, Boote, Nukes (units.zst; mit --records aus den Engine-Stats)
  genähert     Gold pro Minute und ausgegebenes Gold ohne --records: Einkommen = Δ Bestand + Kosten
               der entstandenen und aufgewerteten Einheiten (Kostenformeln aus Config.ts, Stückzahl
               aus units.zst). Mit --records exakt aus stats.gold; die Ausgabe nennt dann das
               Verhältnis Schätzung/exakt als Selbstprüfung.
               Platz und Überleben: Tod auf 250 Ticks genau (Proben aus own.zst).
               Angriffe je 1000 Ticks: Angriffsbefehle (Σ w der attack-Samples, Folgeklicks mitgezählt),
               die Arena zählt ausgeführte Angriffe (stats.attack).
               Allianzanfragen erhalten: nur von Menschen (Anfragen von Nationen stehen in keinem Record).
  fehlt        Angriffe erhalten je 1000 Ticks und Verraten worden: Angriffe und Bündnisbrüche von
               Nationen und Bots entstehen in der Engine und stehen in keinem Record; ohne Replay
               nicht ableitbar (null). Einkommen aus Handel nur mit --records.

Unterschiede zur Arena, die jeder Vergleich mitdenken muss: Menschen spielen viele Karten und
Lobbygrössen (die Ausgabe nennt Karten, Spielerzahl und Bots), die Arena World Compact.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import gzip
import hashlib
import json
import math
import os
import sys
import time
import types

import numpy as np

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
import auswertung as AW  # noqa: E402
import verteilung as V  # noqa: E402

TAKT = 250
TICKS_JE_MINUTE = 600
CODE_TYP = {1: "City", 2: "Port", 3: "Factory", 4: "Defense Post", 5: "Missile Silo", 6: "SAM Launcher",
            7: "Warship", 8: "Transport", 9: "Atom Bomb", 10: "Hydrogen Bomb", 11: "MIRV", 12: "MIRV Warhead"}
TYP_CODE = {v: k for k, v in CODE_TYP.items()}
CODE_NAME = {1: "city", 2: "port", 3: "factory", 4: "defense", 5: "silo", 6: "sam", 7: "warship"}
STATS_NAME = {"city": "city", "port": "port", "factory": "fact", "defense": "defp", "silo": "silo",
              "sam": "saml", "warship": "wshp"}
NUKE_CODES = (9, 10, 11)
NUKE_UNITS = ("Atom Bomb", "Hydrogen Bomb", "MIRV")

HERKUNFT = {
    "exakt": ["truppen_max", "truppen_quote", "gold_max", "allianzen_max", "allianzen_neu", "verrat",
              "gebiet_max", "komponenten", "groesste", "raster_max", "raster_anteil_max", "streuung_rel",
              "kueste", "gebaut_city", "gebaut_port", "gebaut_factory", "gebaut_defense", "gebaut_sam",
              "gebaut_silo", "gebaut_warship", "strukturen_max", "strukturen_verloren", "aufgewertet",
              "boote", "nukes", "anfragen_gesendet", "embargos", "angriffe_ein_aktiv"],
    "genaehert": ["gold_min", "gold_ausgegeben", "ausgabe_quote", "platz", "ueberleben", "angriffe_je_1000",
                  "anfragen_erhalten"],
    "fehlt": ["angriffe_erhalten_je_1000", "verraten_worden", "handel_anteil"],
}


# ---------------------------------------------------------------- Umgebung (Leser, zstd)
def mat_py(pfad: str | None):
    """reader.py und tier1.py aus materializer/py laden; ohne compression.zstd (Python < 3.14)
    mit einer Attrappe über das Paket zstandard."""
    kand = [pfad, os.environ.get("MAT_PY"), "~/d1-lauf/materializer/py", "~/ur-dev/code/materializer/py",
            "~/mat-dev/netz-dev/materializer/py",
            os.path.join(HIER, "..", "..", "..", "openfront-ai-trainer", "materializer", "py")]
    for k in kand:
        if k and os.path.isfile(os.path.join(os.path.expanduser(k), "reader.py")):
            sys.path.insert(0, os.path.abspath(os.path.expanduser(k)))
            break
    try:
        import compression.zstd  # noqa: F401
    except ImportError:
        import zstandard as zs
        mod = types.ModuleType("compression")
        sub = types.ModuleType("compression.zstd")
        sub.decompress = lambda b: zs.ZstdDecompressor().decompressobj().decompress(b)
        mod.zstd = sub
        sys.modules["compression"], sys.modules["compression.zstd"] = mod, sub
    import reader
    import tier1
    return reader, tier1


def sha1_int(g: str) -> int:
    return int(hashlib.sha1(g.encode("ascii")).hexdigest()[:8], 16)


def finde_partien(wurzeln: list[str]) -> dict[str, str]:
    """gid → Ordner (erste Fundstelle) für alle Partien mit .ok."""
    aus = {}
    for w in wurzeln:
        for d, _, dateien in os.walk(os.path.expanduser(w)):
            for n in dateien:
                if n.endswith(".ok") and not n.endswith(".tmp"):
                    aus.setdefault(n[:-3], d)
    return aus


# ---------------------------------------------------------------- Karten und Records
class Karten:
    def __init__(self, wurzel: str | None):
        self.wurzel = os.path.expanduser(wurzel) if wurzel else None
        self.index: dict[str, tuple[str, dict]] | None = None
        self.cache: dict = {}

    def _baue(self):
        self.index = {}
        if not self.wurzel or not os.path.isdir(self.wurzel):
            return
        for d in os.listdir(self.wurzel):
            m = os.path.join(self.wurzel, d, "manifest.json")
            if os.path.isfile(m):
                try:
                    man = json.load(open(m))
                except ValueError:
                    continue
                self.index[str(man.get("name", d)).lower()] = (os.path.join(self.wurzel, d), man)

    def terrain(self, name: str, W: int, H: int):
        """Terrain-Bytes u8[W·H] der Karte in genau dieser Grösse (map, map4x oder map16x)."""
        k = (name, W, H)
        if k in self.cache:
            return self.cache[k]
        if self.index is None:
            self._baue()
        t = None
        e = self.index.get(str(name).lower())
        if e:
            d, man = e
            for datei, key in (("map.bin", "map"), ("map4x.bin", "map4x"), ("map16x.bin", "map16x")):
                info = man.get(key) or {}
                p = os.path.join(d, datei)
                if info.get("width") == W and info.get("height") == H and os.path.isfile(p):
                    roh = np.fromfile(p, dtype=np.uint8)
                    if roh.size == W * H:
                        t = roh
                    break
        self.cache[k] = t
        return t


class Records:
    """GameRecords (optional): gid → {clientID: stats}."""

    def __init__(self, wurzeln: list[str]):
        self.pfad: dict[str, str] = {}
        for w in wurzeln:
            for d, _, dateien in os.walk(os.path.expanduser(w)):
                for n in dateien:
                    for suf in (".json", ".json.gz", ".json.zst"):
                        if n.endswith(suf):
                            self.pfad.setdefault(n[: -len(suf)], os.path.join(d, n))

    def stats(self, gid: str, zdec) -> dict | None:
        p = self.pfad.get(gid)
        if not p:
            return None
        roh = open(p, "rb").read()
        if p.endswith(".gz"):
            roh = gzip.decompress(roh)
        elif p.endswith(".zst"):
            roh = zdec(roh)
        try:
            rec = json.loads(roh)
        except ValueError:
            return None
        spieler = (rec.get("info") or rec).get("players") or []
        return {s.get("clientID"): s.get("stats") or {} for s in spieler if s.get("clientID")}


def z(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- Einheiten (units.zst)
class Einheiten:
    """Einmal durch units.zst: je verfolgtem Spieler gebaut/verloren/erobert/aufgewertet, Boote,
    Nukes, Kosten der eigenen Bauten (für die Einkommensschätzung) und Bauwerke je Probe-Tick."""

    def __init__(self, zeilen, sids: set[int], proben: list[int]):
        self.sids = sids
        st: dict[int, list] = {}                                   # uid → [code, owner, level, im_bau]
        besitz = collections.defaultdict(lambda: np.zeros(13, np.int64))    # owner → Stückzahl je Code
        stufen = collections.defaultdict(lambda: np.zeros(13, np.int64))    # owner → unitsOwned je Code
        self.gebaut = {s: collections.Counter() for s in sids}
        self.verloren = {s: [] for s in sids}                     # (tick, Code, Art: ende|besitz)
        self.erobert = collections.Counter()
        self.aufgewertet = collections.Counter()
        self.boote = collections.Counter()
        self.nukes = collections.Counter()
        self.kosten = {s: [] for s in sids}                       # (tick, Gold)
        self.bau_je_probe = {s: {} for s in sids}                 # t → (Bauwerke Codes 1–6, Warships)
        mirvs = 0
        pi = 0

        def n_kosten(owner, typ):
            return int(sum(stufen[owner][TYP_CODE[t]] for t in V.KOSTEN_TYPEN.get(typ, ())))

        def schnappschuss(t):
            for s in sids:
                b = besitz[s]
                self.bau_je_probe[s][t] = (int(b[1:7].sum()), int(b[7]))

        for tick, uid, mask, code, flags, owner, level, pos, tgt in zeilen:
            while pi < len(proben) and proben[pi] < tick:
                schnappschuss(proben[pi])
                pi += 1
            alt = st.get(uid)
            typ = CODE_TYP.get(code, "")
            if mask & 1 or alt is None:                           # CREATE (oder erste Sichtung)
                if owner in sids and mask & 1:
                    if 1 <= code <= 7:
                        self.gebaut[owner][code] += 1
                        self.kosten[owner].append((tick, V.kosten(typ, n_kosten(owner, typ))))
                    elif code == 8:
                        self.boote[owner] += 1
                    elif code in NUKE_CODES:
                        self.nukes[owner] += 1
                        self.kosten[owner].append((tick, V.kosten(typ, n_kosten(owner, typ), mirvs)))
                if code == 11 and mask & 1:
                    mirvs += 1
                st[uid] = alt = [code, owner, level, flags & 1]
                besitz[owner][code] += 1
                stufen[owner][code] += 1 if flags & 1 else max(1, level)
            else:
                a_code, a_owner, a_level, a_bau = alt
                neu_bau = flags & 1
                if mask & 2 and owner != a_owner:                 # Besitzerwechsel (Eroberung)
                    if a_owner in sids and 1 <= code <= 7:
                        self.verloren[a_owner].append((tick, code, "besitz"))
                    if owner in sids and 1 <= code <= 6:
                        self.erobert[owner] += 1
                if mask & 4 and level > a_level and owner in sids and 1 <= code <= 6:
                    for _ in range(level - a_level):              # Kosten vor dem Umbuchen (wie die Engine)
                        self.aufgewertet[owner] += 1
                        self.kosten[owner].append((tick, V.kosten(typ, n_kosten(owner, typ))))
                # Bestand umbuchen: Zeilen tragen den vollen Zustand nach der Änderung
                besitz[a_owner][code] -= 1
                besitz[owner][code] += 1
                stufen[a_owner][code] -= 1 if a_bau else max(1, a_level)
                stufen[owner][code] += 1 if neu_bau else max(1, level)
                alt[:] = [code, owner, level, neu_bau]
            if mask & 64:                                         # END
                c, o, lv, b = st.pop(uid)
                besitz[o][c] -= 1
                stufen[o][c] -= 1 if b else max(1, lv)
                if o in sids and 1 <= c <= 7:
                    self.verloren[o].append((tick, c, "ende"))
        while pi < len(proben):
            schnappschuss(proben[pi])
            pi += 1


# ---------------------------------------------------------------- eine Partie
def partie(args) -> list[dict]:
    """Alle verfolgten Spieler einer Partie. args = (ordner, gid, sids|None, zusatzordner, optionen)."""
    ordner, gid, nur_sids, zusatz_dir, opt = args
    R, T1 = mat_py(opt.get("mat_py"))
    try:
        G = R.Game(ordner, gid)
    except ValueError as e:
        return [{"gid": gid, "fehler": str(e)}]
    hdr, meta = G.hdr, G.meta()
    cfg = hdr.get("config") or {}
    W, H, land = int(hdr["W"]), int(hdr["H"]), int(hdr.get("landTiles") or 1)
    spieler = {p["sid"]: p for p in hdr.get("players") or []}
    menschen = [s for s, p in spieler.items() if str(p.get("type", "")).upper() == "HUMAN" and p.get("clientID")]
    sids = set(nur_sids) if nur_sids else set(menschen)
    zeilen_je = collections.defaultdict(list)
    for i, m in enumerate(meta):
        zeilen_je[m.get("sid")].append(i)
    sids = {s for s in sids if zeilen_je.get(s)}
    if not sids:
        return []
    L = max((m.get("tick", 0) for m in meta), default=0)
    own_p, units_p = os.path.join(ordner, f"{gid}.own.zst"), os.path.join(ordner, f"{gid}.units.zst")
    own = T1.Own(own_p) if os.path.exists(own_p) else None
    if own is not None:
        L = max(L, own.max_tick)
    proben = list(range(TAKT, L + 1, TAKT))
    terrain = opt["karten"].terrain(hdr.get("map"), W, H) if opt.get("karten") else None
    rindex = V.raster_index(W, H)
    lz = V.land_zellen(terrain, W, H) if terrain is not None else None

    # Besitz aller Spieler je Probe (Platz, Tod) und Verteilung der verfolgten
    hist: dict[int, np.ndarray] = {}
    vert = {s: {} for s in sids}
    if own is not None:
        want = set(proben)
        for t, stz, _ in own.replay_ticks(want=want):
            a = np.frombuffer(stz, dtype="<u2") & 0x0FFF
            cnt = np.bincount(a, minlength=4096)
            hist[t] = cnt
            for s in sids:
                if cnt[s] > 0:
                    vert[s][t] = V.verteilung(a == s, W, H, terrain,
                                              lz if lz is not None else V.land_zellen(np.full(W * H, 0x80, np.uint8), W, H),
                                              rindex)
    einh = None
    if os.path.exists(units_p):
        einh = Einheiten(T1.Units(units_p).rows(), sids, proben)

    zus = (None,) * 5
    zp = os.path.join(zusatz_dir, f"{gid}.zusatz.zst") if zusatz_dir else None
    if zp and os.path.exists(zp):
        zus = R.zusatz_lesen(zp)
    kopf, zg = zus[0], zus[2]
    fi_ein = (kopf.get("felder_global") or []).index("angriffe_ein") if kopf and "angriffe_ein" in (
        kopf.get("felder_global") or []) else None
    if zg is not None and zg.shape[0] != len(meta):
        zg = None                                                 # nicht deckungsgleich: nicht benutzen
    rec = opt["records"].stats(gid, R.zdec) if opt.get("records") else None

    # Tod und Platz aller Spieler aus den Proben (auf 250 Ticks genau)
    tod, fl = {}, {}
    if hist:
        ts = sorted(hist)
        alle = np.zeros(4096, bool)
        for t in ts:
            alle |= hist[t] > 0
        for s in np.flatnonzero(alle):
            s = int(s)
            if s == 0:
                continue
            lebend = [t for t in ts if hist[t][s] > 0]
            letzte = lebend[-1]
            tod[s] = math.inf if letzte == ts[-1] else letzte
            fl[s] = int(hist[letzte][s])

    ids = {s: {spieler[s].get("playerID"), s} for s in sids if s in spieler}
    aus = []
    for s in sorted(sids):
        idx = zeilen_je[s]
        zm = [meta[i] for i in idx]
        tick = [int(m.get("tick", 0)) for m in zm]
        spawn = next((t for t, m in zip(tick, zm) if m.get("kind") == "spawn"), tick[0])
        own_v = [m.get("own") or {} for m in zm]
        gold = [z(m.get("gold")) for m in zm]
        truppen = [z(m.get("troops")) for m in zm]
        quote = [z(o.get("troopsRatio")) for o in own_v]
        allies = [set(m.get("allies") or []) for m in zm]
        # Ende: letzter Tick mit Gebiet (own.zst) bzw. letztes Sample
        ende = tick[-1]
        if s in tod:
            ende = L if tod[s] == math.inf else max(ende, int(tod[s]))
        ueberleben = max(0, ende - spawn)
        lebend_t = [t for t in proben if spawn <= t <= ende]

        def bei(t, reihe):
            i = bisect.bisect_right(tick, t) - 1
            return reihe[i] if i >= 0 else None

        # Einkommen: Δ Bestand + Kosten (genähert) je Intervall zwischen Proben
        kosten = sorted(einh.kosten[s]) if einh else []
        kt = [k[0] for k in kosten]
        kc = np.cumsum([k[1] for k in kosten]) if kosten else np.zeros(0)

        def kosten_bis(t):
            i = bisect.bisect_right(kt, t)
            return float(kc[i - 1]) if i > 0 else 0.0

        verlauf = {k: [] for k in ("t", "gold_min", "strukturen", "truppen", "gebiet", "raster_anteil", "komponenten")}
        vorher = (spawn, gold[0], 0.0)
        for t in lebend_t:
            i = bisect.bisect_right(tick, t) - 1
            if i < 0:
                continue
            tg, g = tick[i], gold[i]
            kb = kosten_bis(tg)
            dt = tg - vorher[0]
            gm = ((g - vorher[1]) + (kb - vorher[2])) * TICKS_JE_MINUTE / dt if dt > 0 else None
            vorher = (tg, g, kb) if dt > 0 else vorher
            v = vert[s].get(t)
            tiles = int(hist[t][s]) if t in hist else int(z(bei(t, [o.get("tiles") for o in own_v])))
            verlauf["t"].append(t)
            verlauf["gold_min"].append(None if gm is None else round(gm))
            verlauf["strukturen"].append(einh.bau_je_probe[s].get(t, (None,))[0] if einh else None)
            verlauf["truppen"].append(round(truppen[i]))
            verlauf["gebiet"].append(round(tiles / land, 6))
            verlauf["raster_anteil"].append(round(v["raster_anteil"], 6) if v and lz is not None else None)
            verlauf["komponenten"].append(v["komponenten"] if v else None)

        # Zusammenfassung. Einkommen genähert über die Spanne der Samples (Bestand und Kosten im
        # selben Fenster); exakt (Records) über die ganze Lebenszeit wie die Arena.
        einkommen_s = (gold[-1] - gold[0]) + (kosten_bis(tick[-1]) - kosten_bis(tick[0]))
        ausgegeben = float(kc[-1]) if len(kc) else None
        st = (rec or {}).get(spieler[s].get("clientID")) if rec else None
        einkommen_x = sum(z(x) for x in (st or {}).get("gold") or []) if st else None
        if einkommen_x is not None:
            einkommen, minuten = einkommen_x, ueberleben / TICKS_JE_MINUTE
        else:
            einkommen, minuten = einkommen_s, (tick[-1] - tick[0]) / TICKS_JE_MINUTE
        acts = [m for m in zm if m.get("kind") == "act"]
        typ = lambda m: (m.get("intent") or {}).get("type")  # noqa: E731
        angr = sum(z(m.get("w", 1)) for m in acts if typ(m) == "attack")
        neu = sum(len(b - a) for a, b in zip(allies, allies[1:]))
        erhalten = sum(1 for m in meta if m.get("sid") != s and m.get("kind") == "act" and typ(m) == "allianceRequest"
                       and (m.get("intent") or {}).get("recipient") in ids.get(s, set()))
        vv = [vert[s][t] for t in lebend_t if t in vert[s]]
        med = lambda a: float(np.median(a)) if a else None  # noqa: E731
        ein_aktiv = med([float(zg[i, fi_ein]) for i in idx]) if zg is not None and fi_ein is not None else None
        u = st.get("units") if st else None
        gebaut = {f"gebaut_{n}": (int(z((u.get(STATS_NAME[n]) or [0])[0])) if u is not None
                                  else (einh.gebaut[s][c] if einh else None)) for c, n in CODE_NAME.items()}
        verl = None
        if u is not None:
            verl = sum(int(z((u.get(k) or [0, 0, 0, 0])[3] if len(u.get(k) or []) > 3 else 0)) for k in STATS_NAME.values())
        elif einh:
            verl = sum(1 for (tk, c, art) in einh.verloren[s] if tk < ende - 1)   # nicht das Aufräumen beim Tod
        summe = {
            "platz": None, "von": None, "ueberleben": ueberleben,
            "gold_min": round(einkommen / minuten) if minuten > 0 else 0,
            "gold_verdient": round(einkommen), "gold_ausgegeben": ausgegeben,
            "ausgabe_quote": (round(ausgegeben / einkommen, 6) if ausgegeben is not None and einkommen > 0 else None),
            "gold_max": max(gold),
            "handel_anteil": (round(sum(z(x) for x in (st.get("gold") or [])[2:6]) / einkommen_x, 6)
                              if st and einkommen_x else None),
            **gebaut,
            "strukturen_max": max((v[0] for v in einh.bau_je_probe[s].values()), default=0) if einh else None,
            "strukturen_verloren": verl,
            "aufgewertet": einh.aufgewertet[s] if einh else None,
            "truppen_max": max(truppen), "truppen_quote": med([q for q in quote if q > 0]),
            "angriffe_je_1000": round(angr * 1000 / ueberleben, 4) if ueberleben > 0 else 0,
            "angriffe_erhalten_je_1000": None, "angriffe_ein_aktiv": ein_aktiv,
            "boote": (einh.boote[s] if einh else sum(z(m.get("w", 1)) for m in acts if typ(m) == "boat")),
            "nukes": (einh.nukes[s] if einh else sum(1 for m in acts if typ(m) == "build_unit"
                                                      and (m.get("intent") or {}).get("unit") in NUKE_UNITS)),
            "anfragen_gesendet": sum(1 for m in acts if typ(m) == "allianceRequest"),
            "anfragen_erhalten": erhalten, "allianzen_neu": neu, "allianzen_max": max(len(a) for a in allies),
            "verrat": int(max(z(o.get("betrayals")) for o in own_v)), "verraten_worden": None,
            "embargos": sum(1 for m in acts if typ(m) in ("embargo", "embargo_all")
                            and (m.get("intent") or {}).get("action") == "start"),
            "gebiet_max": max(verlauf["gebiet"], default=0),
            "komponenten": med([v["komponenten"] for v in vv]), "groesste": med([v["groesste"] for v in vv]),
            "raster_max": max((v["raster"] for v in vv), default=None),
            "raster_anteil_max": max((v["raster_anteil"] for v in vv), default=None) if lz is not None else None,
            "streuung_rel": med([v["streuung_rel"] for v in vv]),
            "kueste": med([v["kueste"] for v in vv if v["kueste"] is not None]),
        }
        if s in tod:
            mein = (tod[s], fl[s])
            summe["platz"] = 1 + sum(1 for o in tod if o != s and (tod[o] > mein[0] or (tod[o] == mein[0] and fl[o] > mein[1])))
            summe["von"] = len(tod)
        aus.append({"gid": gid, "sid": s, "karte": hdr.get("map"), "groesse": hdr.get("mapSize"),
                    "bots": cfg.get("bots"), "menschen": len(menschen), "L": L, "tier1": own is not None,
                    "units": einh is not None, "zusatz": zg is not None, "record": st is not None,
                    "einkommen_schaetzung": round(einkommen_s), "einkommen_exakt": einkommen_x,
                    "summe": summe, "verlauf": verlauf})
    return aus


# ---------------------------------------------------------------- Gruppen
def gruppe(name: str, eintraege: list[dict]) -> dict:
    ok = [e for e in eintraege if "summe" in e]
    zeilen = [{"platz": e["summe"]["platz"], "ueberleben_ticks": e["summe"]["ueberleben"],
               "spielweise": {"summe": e["summe"], "verlauf": e["verlauf"]}} for e in ok]
    sw = {k: AW.quartile([AW.sw_wert(zl, k) for zl in zeilen]) for k, *_ in AW.SPIELWEISE}
    pruef = [e["einkommen_schaetzung"] / e["einkommen_exakt"] for e in ok
             if e.get("einkommen_exakt") and e.get("record")]
    return {"name": name, "partien": len({e["gid"] for e in ok}), "spieler": len(ok),
            "fehler": [e for e in eintraege if "fehler" in e][:10],
            "spielweise": sw, "kurven": AW.kurven(zeilen),
            "kontext": {"karten": collections.Counter(e["karte"] for e in ok).most_common(8),
                        "bots_median": AW.median([e["bots"] for e in ok if e.get("bots") is not None]),
                        "menschen_median": AW.median([e["menschen"] for e in ok]),
                        "von_median": AW.median([e["summe"]["von"] for e in ok if e["summe"]["von"]]),
                        "mit_tier1": sum(e["tier1"] for e in ok), "mit_units": sum(e["units"] for e in ok),
                        "mit_zusatz": sum(e["zusatz"] for e in ok), "mit_record": sum(e["record"] for e in ok)},
            "pruefung_einkommen": ({"n": len(pruef), "schaetzung_durch_exakt": AW.quartile(pruef)} if pruef else None)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", nargs="+", default=["~/of-mat2-out"])
    ap.add_argument("--zusatz", default="~/zusatz/alle")
    ap.add_argument("--karten", default="~/openfront-client-arena/resources/maps")
    ap.add_argument("--stichprobe", type=int, default=120, help="Val-Partien für den Durchschnittsmenschen")
    ap.add_argument("--ur-pool", nargs="*", default=["~/of-ur/pool"])
    ap.add_argument("--ur-zusatz", default="~/of-ur/zusatz")
    ap.add_argument("--ur-liste", default="~/of-ur/spieler_ur.tsv")
    ap.add_argument("--records", nargs="*", default=[], help="Ordner mit GameRecords (optional, exakte Stats)")
    ap.add_argument("--mat-py", default=None, help="materializer/py (reader.py, tier1.py)")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--max-partien", type=int, default=0, help="Rauchtest: höchstens so viele je Gruppe")
    ap.add_argument("--aus", required=True)
    A = ap.parse_args(argv)
    t0 = time.time()
    R, _ = mat_py(A.mat_py)
    opt = {"mat_py": A.mat_py, "karten": Karten(A.karten),
           "records": Records(A.records) if A.records else None}

    auftraege = {"mensch": [], "ultimus_rex": []}
    pool = finde_partien(A.pool)
    val = []
    for g in sorted(pool, key=sha1_int):
        if not R.is_val(g):
            continue
        try:
            h = json.load(open(os.path.join(pool[g], f"{g}.hdr.json")))
        except (OSError, ValueError):
            continue
        if (h.get("config") or {}).get("gameMode") == "Free For All":
            val.append(g)
        if len(val) >= (A.max_partien or A.stichprobe):
            break
    for g in val:
        auftraege["mensch"].append((pool[g], g, None, os.path.expanduser(A.zusatz), opt))
    liste = os.path.expanduser(A.ur_liste)
    if os.path.isfile(liste):
        ur_pool = finde_partien(A.ur_pool)
        n = 0
        for zl in open(liste):
            t = zl.rstrip("\n").split("\t")
            if zl.startswith("#") or len(t) < 2 or t[0] not in ur_pool:
                continue
            auftraege["ultimus_rex"].append((ur_pool[t[0]], t[0], [int(t[1])], os.path.expanduser(A.ur_zusatz), opt))
            n += 1
            if A.max_partien and n >= A.max_partien:
                break
    ergebnis = {}
    for name, auf in auftraege.items():
        if not auf:
            continue
        if A.jobs > 1:
            import multiprocessing as mp
            opt_leicht = [(a[0], a[1], a[2], a[3], {**a[4]}) for a in auf]
            with mp.get_context("fork").Pool(A.jobs) as p:
                teile = p.map(partie, opt_leicht, chunksize=1)
        else:
            teile = [partie(a) for a in auf]
        eintraege = [e for t in teile for e in t]
        ergebnis[name] = gruppe({"mensch": "Durchschnittsmensch (Val-Stichprobe)",
                                 "ultimus_rex": "Ultimus_Rex"}[name], eintraege)
        print(f"[menschen] {name}: {ergebnis[name]['spieler']} Spieler in {ergebnis[name]['partien']} Partien, "
              f"{len(ergebnis[name]['fehler'])} Fehler, {time.time() - t0:.0f} s", flush=True)
    bericht = {"erstellt": round(time.time()), "gruppen": ergebnis, "herkunft": HERKUNFT,
               "kennzahlen": [list(x) for x in AW.SPIELWEISE], "takt": TAKT,
               "sekunden": round(time.time() - t0, 1)}
    with open(os.path.expanduser(A.aus), "w") as f:
        json.dump(bericht, f, ensure_ascii=False, default=lambda o: None if o != o else float(o))
    for name, g in ergebnis.items():
        print(f"\n=== {g['name']}: {g['spieler']} Spieler, {g['partien']} Partien, Kontext {g['kontext']}")
        for k, label, *_ in AW.SPIELWEISE:
            q = g["spielweise"].get(k) or {}
            if q.get("n"):
                print(f"  {label[:44]:<45}{AW.zeile(q['median'], 4):>10} [{AW.zeile(q['p25'], 3)}–{AW.zeile(q['p75'], 3)}]  n={q['n']}")
        if g.get("pruefung_einkommen"):
            print(f"  Selbstprüfung Einkommen (Schätzung/exakt): {g['pruefung_einkommen']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
