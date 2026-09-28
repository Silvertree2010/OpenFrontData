#!/usr/bin/env python3
"""Auswertung der Arena-JSONL: Kennzahlen zusammenfassen und zwei Seiten gepaart vergleichen.

  # eine Datei, netz gegen nichtstun (Standard)
  python3 arena/auswertung.py lauf.jsonl

  # zwei Läufe (z. B. zwei Checkpoints) gepaart, beide Seite netz
  python3 arena/auswertung.py alt.jsonl neu.jsonl --a-seite netz --b-seite netz

  # als JSON für Skripte; --json-aus schreibt dasselbe samt Verlaufskurven in eine Datei
  python3 arena/auswertung.py lauf.jsonl --json
  python3 arena/auswertung.py a.jsonl b.jsonl --json-aus lauf.spielweise.json

Gepaart heisst: verglichen werden nur Partien, deren Spiel-ID in beiden Mengen vorkommt.
Dieselbe Spiel-ID ist dieselbe Karte, dieselben Nationen, dieselben Bots und dieselbe
Startkachel — der Unterschied zwischen den Zeilen ist dann die Seite und sonst nichts.

Test: Vorzeichentest (exakt, Binomial). Er setzt nur voraus, dass die Partien voneinander
unabhängig sind, und nicht, dass die Differenzen irgendwie verteilt sind. Zusätzlich der
Wilcoxon-Vorzeichenrangtest in der Normalnäherung — der nutzt auch die Grösse der
Unterschiede, ist aber bei unter etwa 15 Paaren nur ein Anhaltspunkt. Bei wenigen Partien
kann kein Test viel sagen: mit 5 Paaren ist p ≥ 0,0625, ganz gleich wie klar das Bild ist.
Wer eine Aussage will, braucht 20 Partien und mehr.

Spielweise (Zeilen mit "spielweise", arena.ts ab 14.09.): rund 40 Kennzahlen, wie eine KI
spielt. Viele Kennzahlen heisst viele Gelegenheiten für einen Zufallsbefund (A/A 14.09.: bei
10 Kennzahlen hatte ein gleiches Netz in 20–25 % der Läufe irgendein p < 0,05). Darum trägt
jeder gepaarte Vergleich der Spielweise ein Holm-korrigiertes p über die ganze Familie
(alle Zeilen der Tabelle, Platz eingeschlossen): p_holm < 0,05 hält die familienweise
Fehlerrate bei 5 %, gleich wie viele Kennzahlen es sind (tests/holm_test.py prüft das unter H0).
Grundlage ist der Vorzeichentest. Die Hauptkennzahl bleibt der Platz; alle anderen beschreiben.
"""
from __future__ import annotations

import argparse
import json
import math
import sys

# Name → (Pfad in der Zeile, "gross ist besser"?)
KENNZAHLEN = [
    ("gebiet@500", lambda z: z["gebiet"].get("500"), True),
    ("gebiet@1000", lambda z: z["gebiet"].get("1000"), True),
    ("gebiet@2000", lambda z: z["gebiet"].get("2000"), True),
    ("gebiet@3000", lambda z: z["gebiet"].get("3000"), True),
    ("gebiet_rel@1000", lambda z: (z.get("gebiet_rel") or {}).get("1000"), True),
    ("ueberleben_ticks", lambda z: z["ueberleben_ticks"], True),
    ("platz", lambda z: z["platz"], False),
    ("handlungen_je_1000", lambda z: z["handlungen_je_1000"], True),
    ("truppen_max", lambda z: max([v["truppen"] for v in z["verlauf"]], default=0), True),
    ("gold_max", lambda z: max([v["gold"] for v in z["verlauf"]], default=0), True),
]

# Spielweise: (Schlüssel in spielweise.summe, Beschriftung, Bereich, Richtung)
# Richtung +1: mehr ist besser, −1: weniger ist besser, 0: Stil (kein besser/schlechter).
# "platz" und "ueberleben" kommen aus der Zeile selbst. Die Menschen-Referenz (menschen.py)
# liefert dieselben Schlüssel; wo sie eine Kennzahl nicht ableiten kann, steht dort null.
SPIELWEISE = [
    ("platz", "Platz", "Gebiet", -1),
    ("ueberleben", "Überleben (Ticks)", "Gebiet", +1),
    ("gebiet_max", "Gebiet, Maximum (Anteil Land)", "Gebiet", +1),
    ("komponenten", "Getrennte Gebietsstücke, Median", "Gebiet", 0),
    ("groesste", "Anteil im grössten Stück, Median", "Gebiet", 0),
    ("raster_max", "Rasterfelder mit Land (16×16), Maximum", "Gebiet", 0),
    ("raster_anteil_max", "Anteil der Land-Rasterfelder, Maximum", "Gebiet", 0),
    ("streuung_rel", "Streuung (1 = kompakte Scheibe), Median", "Gebiet", 0),
    ("kueste", "Küstenanteil, Median", "Gebiet", 0),
    ("gold_min", "Gold pro Minute (Einkommen)", "Wirtschaft", +1),
    ("gold_ausgegeben", "Gold ausgegeben", "Wirtschaft", 0),
    ("ausgabe_quote", "Anteil des Einkommens ausgegeben", "Wirtschaft", 0),
    ("gold_max", "Gold-Bestand, Maximum", "Wirtschaft", 0),
    ("handel_anteil", "Einkommen aus Handel und Zügen", "Wirtschaft", 0),
    ("gebaut_city", "Gebaut: City", "Strukturen", 0),
    ("gebaut_port", "Gebaut: Port", "Strukturen", 0),
    ("gebaut_factory", "Gebaut: Factory", "Strukturen", 0),
    ("gebaut_defense", "Gebaut: Defense Post", "Strukturen", 0),
    ("gebaut_sam", "Gebaut: SAM Launcher", "Strukturen", 0),
    ("gebaut_silo", "Gebaut: Missile Silo", "Strukturen", 0),
    ("gebaut_warship", "Gebaut: Warship", "Strukturen", 0),
    ("strukturen_max", "Bauwerke gleichzeitig, Maximum", "Strukturen", 0),
    ("strukturen_verloren", "Bauwerke/Schiffe verloren", "Strukturen", 0),
    ("aufgewertet", "Aufwertungen", "Strukturen", 0),
    ("truppen_max", "Truppen, Maximum", "Militär", +1),
    ("truppen_quote", "Truppen / Truppen-Max, Median", "Militär", 0),
    ("angriffe_je_1000", "Angriffe gesendet je 1000 Ticks", "Militär", 0),
    ("angriffe_erhalten_je_1000", "Angriffe erhalten je 1000 Ticks", "Militär", 0),
    ("angriffe_ein_aktiv", "Laufende Angriffe auf mich, Median", "Militär", 0),
    ("boote", "Truppenboote gesendet", "Militär", 0),
    ("nukes", "Nukes gestartet", "Militär", 0),
    ("anfragen_gesendet", "Allianzanfragen gesendet", "Diplomatie", 0),
    ("anfragen_erhalten", "Allianzanfragen erhalten", "Diplomatie", 0),
    ("allianzen_neu", "Allianzen geschlossen", "Diplomatie", 0),
    ("allianzen_max", "Allianzen gleichzeitig, Maximum", "Diplomatie", 0),
    ("verrat", "Verrat (Bündnis gebrochen)", "Diplomatie", 0),
    ("verraten_worden", "Verraten worden", "Diplomatie", 0),
    ("embargos", "Embargos verhängt", "Diplomatie", 0),
]
# Verlaufskurven (Spalten von spielweise.verlauf), Median über die Partien je Tick
KURVEN = [("gold_min", "Gold pro Minute"), ("strukturen", "Bauwerke"), ("truppen", "Truppen"),
          ("gebiet", "Gebiet (Anteil Land)"), ("raster_anteil", "Gebietsverteilung: Anteil Land-Rasterfelder"),
          ("komponenten", "Getrennte Gebietsstücke")]


def sw_wert(z: dict, key: str):
    """Kennzahl der Spielweise aus einer Arena-Zeile (None, wenn nicht gemessen)."""
    if key == "platz":
        return z.get("platz")
    if key == "ueberleben":
        return z.get("ueberleben_ticks")
    s = (z.get("spielweise") or {}).get("summe") or {}
    return s.get(key)


def quantil(v: list[float], q: float):
    if not v:
        return None
    s = sorted(v)
    if len(s) == 1:
        return s[0]
    i = q * (len(s) - 1)
    u = int(math.floor(i))
    return s[u] + (s[min(u + 1, len(s) - 1)] - s[u]) * (i - u)


def median(v):
    return quantil(v, 0.5)


def binom_p(k: int, n: int) -> float:
    """Zweiseitiger exakter Binomialtest gegen p=1/2."""
    if n == 0:
        return 1.0
    k = min(k, n - k)
    einseitig = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * einseitig)


def wilcoxon_p(d: list[float]) -> float | None:
    """Vorzeichenrangtest, Normalnäherung mit Stetigkeitskorrektur und Bindungskorrektur."""
    d = [x for x in d if x != 0]
    n = len(d)
    if n < 6:
        return None
    paare = sorted(enumerate(d), key=lambda t: abs(t[1]))
    raenge = [0.0] * n
    i = 0
    bindungen: list[int] = []
    while i < n:
        j = i
        while j + 1 < n and abs(paare[j + 1][1]) == abs(paare[i][1]):
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            raenge[paare[k][0]] = r
        bindungen.append(j - i + 1)
        i = j + 1
    w_plus = sum(r for r, x in zip(raenge, d) if x > 0)
    mu = n * (n + 1) / 4
    var = n * (n + 1) * (2 * n + 1) / 24 - sum(t ** 3 - t for t in bindungen) / 48
    if var <= 0:
        return None
    z = (abs(w_plus - mu) - 0.5) / math.sqrt(var)
    return min(1.0, 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))))


def holm(p: list[float | None]) -> list[float | None]:
    """Holm-Bonferroni: korrigierte p in der Reihenfolge der Eingabe (None bleibt None)."""
    idx = sorted((i for i, x in enumerate(p) if x is not None), key=lambda i: p[i])
    m = len(idx)
    aus: list[float | None] = [None] * len(p)
    lauf = 0.0
    for r, i in enumerate(idx):
        lauf = max(lauf, min(1.0, (m - r) * p[i]))
        aus[i] = lauf
    return aus


def lade(pfad: str) -> list[dict]:
    with open(pfad) as f:
        return [json.loads(l) for l in f if l.strip()]


def nach_seite(zeilen: list[dict], seite: str | None) -> dict[str, dict]:
    aus = {}
    for z in zeilen:
        if seite is not None and z["seite"] != seite:
            continue
        aus[z["spiel_id"]] = z
    return aus


def quartile(v: list) -> dict:
    v = [x for x in v if x is not None]
    return {"median": median(v), "p25": quantil(v, 0.25), "p75": quantil(v, 0.75), "n": len(v)}


def kurven(zeilen: list[dict], quelle=lambda z: (z.get("spielweise") or {}).get("verlauf")) -> dict:
    """Je Kurve: [[t, median, p25, p75, n], …] über alle Partien, die bei t noch leben."""
    aus = {}
    for key, _ in KURVEN:
        je_t: dict[int, list[float]] = {}
        for z in zeilen:
            v = quelle(z) or {}
            for t, x in zip(v.get("t") or [], v.get(key) or []):
                if x is not None:
                    je_t.setdefault(int(t), []).append(float(x))
        aus[key] = [[t, median(x), quantil(x, 0.25), quantil(x, 0.75), len(x)] for t, x in sorted(je_t.items())]
    return aus


def beschreibe(zeilen: list[dict]) -> dict:
    aus: dict = {"partien": len(zeilen)}
    for name, hol, _ in KENNZAHLEN:
        v = [hol(z) for z in zeilen]
        v = [x for x in v if x is not None]
        aus[name] = {"median": median(v), "p25": quantil(v, 0.25), "p75": quantil(v, 0.75),
                     "n": len(v)}
    aus["siege"] = sum(1 for z in zeilen if z.get("sieg"))
    aus["gruende"] = {}
    for z in zeilen:
        g = z["abbruchgrund"].split(":")[0]
        aus["gruende"][g] = aus["gruende"].get(g, 0) + 1
    aus["serverfehler"] = sum(z.get("serverfehler", 0) for z in zeilen)
    aus["ohne_kachel"] = sum(z.get("ohne_kachel", 0) for z in zeilen)
    aus["anfragen"] = sum(z.get("anfragen", 0) for z in zeilen)
    aus["nichtstun"] = sum(z.get("nichtstun", 0) for z in zeilen)
    akt: dict[str, int] = {}
    for z in zeilen:
        for k, n in (z.get("aktionen") or {}).items():
            akt[k] = akt.get(k, 0) + n
    aus["aktionen"] = dict(sorted(akt.items(), key=lambda t: -t[1]))
    mit = [z for z in zeilen if z.get("spielweise")]
    if mit:
        aus["spielweise"] = {k: quartile([sw_wert(z, k) for z in mit]) for k, *_ in SPIELWEISE}
        aus["spielweise_partien"] = len(mit)
        ms = [z.get("ms_spielweise") for z in mit if z.get("ms_spielweise") is not None]
        anteil = [z["ms_spielweise"] / z["ms"] for z in mit if z.get("ms_spielweise") is not None and z.get("ms")]
        aus["spielweise_kosten"] = {"ms_median": median(ms), "anteil_median": median(anteil),
                                    "anteil_max": max(anteil) if anteil else None}
    return aus


def vergleiche(a: dict[str, dict], b: dict[str, dict]) -> dict:
    ids = sorted(set(a) & set(b))
    aus: dict = {"paare": len(ids), "nur_a": len(set(a) - set(b)), "nur_b": len(set(b) - set(a)),
                 "kennzahlen": {}}
    for name, hol, gross_gut in KENNZAHLEN:
        d = []
        for i in ids:
            x, y = hol(a[i]), hol(b[i])
            if x is None or y is None:
                continue
            d.append(x - y)
        if not d:
            continue
        plus = sum(1 for x in d if x > 0)
        minus = sum(1 for x in d if x < 0)
        aus["kennzahlen"][name] = {
            "n": len(d), "median_differenz": median(d),
            "a_besser": plus if gross_gut else minus,
            "b_besser": minus if gross_gut else plus,
            "gleich": len(d) - plus - minus,
            "gross_ist_besser": gross_gut,
            "p_vorzeichen": binom_p(min(plus, minus), plus + minus),
            "p_wilcoxon": wilcoxon_p(d),
        }
    # Spielweise: gepaart, Holm über die ganze Familie
    sw = {}
    for key, *_ in SPIELWEISE:
        d = []
        for i in ids:
            if not (a[i].get("spielweise") and b[i].get("spielweise")):
                continue
            x, y = sw_wert(a[i], key), sw_wert(b[i], key)
            if x is None or y is None:
                continue
            d.append(float(x) - float(y))
        if not d:
            continue
        plus = sum(1 for x in d if x > 0)
        minus = sum(1 for x in d if x < 0)
        sw[key] = {"n": len(d), "median_differenz": median(d), "mittel_differenz": sum(d) / len(d),
                   "a_hoeher": plus, "b_hoeher": minus, "gleich": len(d) - plus - minus,
                   "p_vorzeichen": binom_p(min(plus, minus), plus + minus), "p_wilcoxon": wilcoxon_p(d)}
    for key, p in zip(sw, holm([q["p_vorzeichen"] for q in sw.values()])):
        sw[key]["p_holm"] = p
    if sw:
        aus["spielweise"] = sw
        aus["spielweise_familie"] = len(sw)
    return aus


def tabelle(bericht: dict) -> list[dict]:
    """Zeilen für Board und Text: Beschriftung, Richtung, Quartile je Seite, gepaarter Vergleich."""
    rows = []
    g = (bericht.get("gepaart") or {}).get("spielweise") or {}
    for key, label, bereich, richtung in SPIELWEISE:
        r = {"key": key, "label": label, "bereich": bereich, "richtung": richtung}
        for s in ("a", "b"):
            q = ((bericht.get(s) or {}).get("spielweise") or {}).get(key)
            if q:
                r[s] = q
        if key in g:
            r["gepaart"] = g[key]
        if len(r) > 4:
            rows.append(r)
    return rows


def zeile(x, n=5):
    return "—" if x is None else (f"{x:.{n}g}" if isinstance(x, float) else str(x))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dateien", nargs="+", help="eine oder zwei JSONL-Dateien")
    ap.add_argument("--a-seite", default=None)
    ap.add_argument("--b-seite", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--json-aus", default=None,
                    help="Bericht samt Spielweise-Tabelle und Verlaufskurven als JSON in diese Datei")
    A = ap.parse_args(argv)

    if len(A.dateien) == 1:
        zeilen = lade(A.dateien[0])
        seiten = sorted({z["seite"] for z in zeilen})
        a_seite = A.a_seite or ("netz" if "netz" in seiten else seiten[0])
        b_seite = A.b_seite or next((s for s in seiten if s != a_seite), None)
        a = nach_seite(zeilen, a_seite)
        b = nach_seite(zeilen, b_seite) if b_seite else {}
        namen = (f"{A.dateien[0]}:{a_seite}", f"{A.dateien[0]}:{b_seite}")
    else:
        a = nach_seite(lade(A.dateien[0]), A.a_seite or "netz")
        b = nach_seite(lade(A.dateien[1]), A.b_seite or A.a_seite or "netz")
        namen = (f"{A.dateien[0]}:{A.a_seite or 'netz'}",
                 f"{A.dateien[1]}:{A.b_seite or A.a_seite or 'netz'}")

    bericht = {"a": {"name": namen[0], **beschreibe(list(a.values()))}}
    if b:
        bericht["b"] = {"name": namen[1], **beschreibe(list(b.values()))}
        bericht["gepaart"] = vergleiche(a, b)
    if A.json_aus:
        voll = dict(bericht, tabelle=tabelle(bericht),
                    kurven={"a": kurven(list(a.values())), **({"b": kurven(list(b.values()))} if b else {})})
        with open(A.json_aus, "w") as f:
            json.dump(voll, f, ensure_ascii=False)
    if A.json:
        print(json.dumps(bericht, ensure_ascii=False))
        return 0

    for k in ("a", "b"):
        if k not in bericht:
            continue
        s = bericht[k]
        print(f"\n=== {s['name']}  ({s['partien']} Partien)")
        for name, _, gross_gut in KENNZAHLEN:
            q = s[name]
            pfeil = "↑" if gross_gut else "↓"
            print(f"  {name:<20}{pfeil} Median {zeile(q['median']):>10}"
                  f"   p25 {zeile(q['p25']):>10}   p75 {zeile(q['p75']):>10}")
        print(f"  Siege {s['siege']}   Gründe {s['gruende']}")
        if s["anfragen"]:
            print(f"  Anfragen {s['anfragen']}, davon Nichtstun {s['nichtstun']}, "
                  f"ohne gültige Kachel {s['ohne_kachel']}, Serverfehler {s['serverfehler']}")
            print(f"  Aktionen {s['aktionen']}")

    g = bericht.get("gepaart")
    if g:
        print(f"\n=== gepaart: {namen[0]} gegen {namen[1]}   ({g['paare']} gemeinsame Partien"
              + (f", nur A {g['nur_a']}, nur B {g['nur_b']}" if g["nur_a"] or g["nur_b"] else "")
              + ")")
        print(f"  {'Kennzahl':<20}{'ΔMedian':>12}{'A besser':>10}{'B besser':>10}"
              f"{'p Vorz.':>10}{'p Wilc.':>10}")
        for name, q in g["kennzahlen"].items():
            print(f"  {name:<20}{zeile(q['median_differenz']):>12}{q['a_besser']:>10}"
                  f"{q['b_besser']:>10}{zeile(q['p_vorzeichen'], 3):>10}"
                  f"{zeile(q['p_wilcoxon'], 3):>10}")
        if g["paare"] < 20:
            print(f"  Achtung: {g['paare']} Paare. Der Vorzeichentest kann hier nicht unter "
                  f"p={binom_p(0, g['paare']):.3g} kommen — das reicht für kein Urteil.")

    # Spielweise (eigener Abschnitt; die Tabelle darüber bleibt im alten Format)
    rows = tabelle(bericht)
    if rows:
        fam = (g or {}).get("spielweise_familie")
        print(f"\n=== Spielweise" + (f" (gepaart, Holm über {fam} Kennzahlen)" if fam else ""))
        kopf = f"  {'Kennzahl':<44}{'A Median [p25–p75]':>28}"
        if g:
            kopf += f"{'B Median [p25–p75]':>28}{'ΔMedian':>11}{'A>B':>5}{'B>A':>5}{'p Holm':>9}"
        print(kopf)
        q3 = lambda q: f"{zeile(q['median'], 4)} [{zeile(q['p25'], 3)}–{zeile(q['p75'], 3)}]" if q else "—"  # noqa: E731
        for r in rows:
            t = f"  {r['label'][:43]:<44}{q3(r.get('a')):>28}"
            if g:
                p = r.get("gepaart") or {}
                t += (f"{q3(r.get('b')):>28}{zeile(p.get('median_differenz'), 4):>11}"
                      f"{p.get('a_hoeher', '—'):>5}{p.get('b_hoeher', '—'):>5}{zeile(p.get('p_holm'), 3):>9}")
            print(t)
        for k in ("a", "b"):
            ko = (bericht.get(k) or {}).get("spielweise_kosten")
            if ko and ko.get("anteil_median") is not None:
                print(f"  Messkosten {k.upper()}: Median {ko['ms_median']:.1f} ms je Partie, "
                      f"{100 * ko['anteil_median']:.2f} % der Partiezeit (max. {100 * ko['anteil_max']:.2f} %)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
