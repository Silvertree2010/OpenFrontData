"""Belohnung fürs RL: Endplatz als eigentliche Belohnung, dazu potenzialbasierte Formung (PBRS).

Eigentliche Belohnung (bestimmt, was optimal ist):
    R = p = (von − platz) / (von − 1)          einmal am Ende der Episode, p ∈ [0, 1]
Episode eines KI-Spielers: erste Entscheidung bis Tod, Partieende oder Ticklimit. Der Platz
kommt aus der Arena (wer länger lebt, steht vorn; bei gleichem Ende entscheidet das Gebiet),
das Überleben steckt also schon in R.

Formung (Ng, Harada, Russell 1999):
    F(s, s') = γ·Φ(s') − Φ(s),     Φ(Ende) = 0
    Φ(s) = lebt · ( c + w_u·u + w_g·f(g) )
    u    = tot / (spieler − 1)          Anteil der schon ausgeschiedenen Mitspieler
    f(g) = log2(1 + g·n) / log2(1 + n)  Gebietsanteil g auf Log-Skala, n = spieler; f(0)=0,
                                        f(1)=1, f(1/n) = 1/log2(1+n) (fairer Anteil ≈ 0,15)
Φ hängt nur vom Zustand ab und ist am Ende 0 — dann ändert die Formung die optimale Politik
nicht (tests/belohnungtest.py prüft das an Zufalls-MDPs, samt Gegenprobe mit einer
Nicht-Potenzial-Formung, die sie ändert).

Rückgabe je Entscheidung t (Monte Carlo, Belohnung r_t zwischen Entscheidung t und t+1):
    r_t = γ·Φ(s_{t+1}) − Φ(s_t)   (t < T−1),    r_{T−1} = R − Φ(s_{T−1})
    G_t = Σ_k γ^(k−t)·r_k  =  γ^(T−1−t)·R − Φ(s_t)            (teleskopiert)
Mit γ = 1 ist G_t = R − Φ(s_t): Φ wirkt genau wie eine zustandsabhängige Baseline. Deshalb
braucht der erste Lauf keinen Kritiker, und der Vorteil je Schritt ist A_t = G_t (AWR zentriert
ohnehin je Batch, verlust.adv_gewicht). Je besser Φ(s) den erwarteten Endplatz trifft, desto
kleiner die Varianz von A_t — das ist das Kriterium für die Gewichte.

Gewichte: siehe GEWICHTE unten (Begründung dort).

Ziele (Schalter --ziel, seit 16.09.): welche Endbelohnung R gilt.
    platz   R = p                                     (Version 2, Standard)
    sieg    R = 0,5·p + 0,5·s                          s = 1 bei Engine-Sieg, sonst 0
    gebiet  R = 0,5·min(1, land/land_max) + 0,5·s      land = eigene Kacheln am Ende (tot = 0),
                                                      land_max = Kacheln des grössten Lebenden
Grund: Mit p über 73 Spieler ist Platz 1 = 1,0 und Platz 4 = 0,958 — ein Sieg ist kaum mehr wert
als Top 5. Ziel des Users: 90 % Siege gegen Nations Medium.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import numpy as np

VERSION = 2   # 2: R aus dem Platz ohne Tribes (platz_von), sonst gleich


@dataclass(frozen=True)
class Gewichte:
    """Φ = lebt·(c + w_u·u + w_g·f(g)).

    Gemessen (trajektorie.py gewichte, A/A-Lauf aa20 am 14.09.: bc3 gegen Nations Hard, World,
    40 Bots, Ziehen Top-4, je Lauf 20 Episoden / ~1600 Entscheidungen): Kleinste Quadrate
    R ≈ c + w_u·u + w_g·f auf Lauf A → (0,51; 0,37; 0,22), R² 0,50; auf Lauf B → (0,49; 0,40;
    0,15), R² 0,55. Gesetzt ist die gerundete Mitte (0,50; 0,40; 0,20). Auf dem jeweils anderen
    Lauf senkt das Var(A_t) gegenüber Var(R) um gut die Hälfte (siehe unten). Die ersten
    Schätzwerte (0,20; 0,70; 0,55) senkten sie gar nicht — zu viel Gewicht, Φ überkorrigiert.
    Falsche Gewichte kosten nur Varianz, nie die Richtung: Φ ist ein Potenzial, die optimale
    Politik bleibt dieselbe. Neu anpassen, wenn das Netz deutlich länger überlebt (heute: tot im
    Median nach ~1000–1500 Ticks, u ist bei den meisten Entscheidungen klein). Einordnung:
      - u ist eine sichere Untergrenze: wer lebt, während ein Anteil u schon draussen ist, wird
        mindestens so gut platziert (p ≥ u·(spieler−1)/(von−1) ≈ u). w_u nahe 1 ist also die
        natürliche Grösse.
      - f(g) sagt voraus, wie weit es darüber hinaus nach oben geht: grosses Gebiet → lange
        leben → vorn landen.
      - c ist für lebende Zustände eine Konstante und ändert keinen Vorteil innerhalb eines
        Laufs; es bestimmt nur den Sprung −Φ beim Tod (mit γ < 1 wichtig).
    """
    c: float = 0.50
    w_u: float = 0.40
    w_g: float = 0.20


GEWICHTE = Gewichte()
GAMMA = 1.0

ZIELE = ("platz", "sieg", "gebiet")
# Φ je Ziel. Φ soll E[R | s] treffen; mit einem anderen R passen die Platz-Gewichte nicht mehr.
# Gemessen 16.09. auf rl3-Spuren (Medium, 400 Bots; anpassen auf i92, prüfen auf i93, je 160
# Episoden / ~40k Entscheidungen, feste Kandidaten über trajektorie.merkmale): u ist im Median
# 0,96 — die Bots sterben früh, u trägt nichts mehr, darum w_u = 0. Var(A) gegenüber Var(R) auf i93:
#   platz  gesetzt (0,50; 0,40; 0,20) +6 %  ← passt auf Medium/400 nicht mehr
#          (0,75; 0; 0,25)            −24 %  → für neue Läufe über --phi, platz bleibt für rl3 gleich
#   sieg   (0,35; 0; 0,45)            −35 %  (Anpassung 0,33; 0,46)
#   gebiet noch ohne Spuren mit land_ende: vorerst wie sieg, nach den ersten Iterationen prüfen
# c ändert Var(A) nicht (Konstante für lebende Zustände), nur den Sprung beim Tod.
GEWICHTE_JE_ZIEL = {"platz": GEWICHTE,
                    "sieg": Gewichte(0.35, 0.0, 0.45),
                    "gebiet": Gewichte(0.35, 0.0, 0.45)}


def gewichte_fuer(ziel: str) -> Gewichte:
    if ziel not in ZIELE:
        raise ValueError(f"unbekanntes Ziel {ziel!r}, erlaubt {ZIELE}")
    return GEWICHTE_JE_ZIEL[ziel]


def gewichte_aus_text(text: str) -> Gewichte:
    """'c,w_u,w_g' → Gewichte (Schalter --phi)."""
    teile = [float(x) for x in text.split(",")]
    if len(teile) != 3:
        raise ValueError(f"--phi braucht drei Zahlen c,w_u,w_g, nicht {text!r}")
    return Gewichte(*teile)


def f_gebiet(g, n):
    """Gebietsanteil g ∈ [0,1] auf Log-Skala, normiert mit der Spielerzahl n: [0,1]."""
    g = np.clip(np.asarray(g, np.float64), 0.0, 1.0)
    n = np.maximum(np.asarray(n, np.float64), 2.0)
    return np.log2(1.0 + g * n) / np.log2(1.0 + n)


def anteil_tot(tot, spieler):
    """Anteil der Mitspieler, die schon ausgeschieden sind: [0,1]."""
    spieler = np.maximum(np.asarray(spieler, np.float64), 2.0)
    return np.clip(np.asarray(tot, np.float64) / (spieler - 1.0), 0.0, 1.0)


def potenzial(gebiet, tot, spieler, lebt=True, gew: Gewichte = GEWICHTE):
    """Φ(s). Tote und Endzustände haben Φ = 0."""
    phi = gew.c + gew.w_u * anteil_tot(tot, spieler) + gew.w_g * f_gebiet(gebiet, spieler)
    return np.where(np.asarray(lebt, bool), phi, 0.0)


def platz_von(e: dict) -> tuple[float, float]:
    """Platz und Feldgrösse für R. Seit Version 2 ohne Tribes (PlayerType.Bot), wenn die Arena es
    liefert: Bei 400 Bots scheiden die fast von selbst aus, und der Platz unter allen presst gute
    und mittlere Partien auf R 0,90–0,99 — rl1/rl2 lernten damit kaum (15.09.). Ältere Spuren ohne
    die Felder fallen auf den Platz unter allen zurück."""
    if e.get("platz_ohne_bots") and e.get("von_ohne_bots"):
        return float(e["platz_ohne_bots"]), float(e["von_ohne_bots"])
    return float(e["platz"]), float(e["von"])


def platz_wert(platz, von):
    """Endplatz → R ∈ [0,1]: 1 = Sieger, 0 = Letzter."""
    von = np.maximum(np.asarray(von, np.float64), 2.0)
    return np.clip((von - np.asarray(platz, np.float64)) / (von - 1.0), 0.0, 1.0)


def endwert(e: dict, ziel: str = "platz") -> float:
    """Endbelohnung R ∈ [0,1] eines KI-Spielers aus seinem Ergebnis (hdr.json → ki[]).
    Fehlen die Felder des Ziels, bricht es ab statt still auf den Platz zurückzufallen."""
    p = float(platz_wert(*platz_von(e)))
    if ziel == "platz":
        return p
    if "sieg" not in e:
        raise ValueError("Ziel braucht das Feld sieg")
    s = 1.0 if e["sieg"] else 0.0
    if ziel == "sieg":
        return 0.5 * p + 0.5 * s
    if ziel == "gebiet":
        if e.get("land_ende") is None or e.get("land_max_ende") is None:
            raise ValueError("Ziel gebiet braucht land_ende und land_max_ende (Arena ab 16.09.)")
        q = min(1.0, float(e["land_ende"]) / float(e["land_max_ende"])) if e["land_max_ende"] > 0 else 0.0
        return 0.5 * q + 0.5 * s
    raise ValueError(f"unbekanntes Ziel {ziel!r}, erlaubt {ZIELE}")


def rueckgaben(phi, R: float, gamma: float = GAMMA):
    """Eine Episode: Φ(s_0..s_{T−1}) der Entscheidungszustände, Endbelohnung R.
    Rückgabe (r, G): geformte Belohnung je Schritt und MC-Rückgabe je Schritt."""
    phi = np.asarray(phi, np.float64)
    T = len(phi)
    if T == 0:
        return np.zeros(0), np.zeros(0)
    naechstes = np.append(phi[1:], 0.0)          # Φ(Ende) = 0
    r = gamma * naechstes - phi
    r[-1] = R - phi[-1]                           # letzter Schritt: Endbelohnung, Φ(Ende) = 0
    G = np.empty(T)
    acc = 0.0
    for t in range(T - 1, -1, -1):
        acc = r[t] + gamma * acc
        G[t] = acc
    return r, G


def gae(werte, R: float, gamma: float = GAMMA, lam: float = 0.95):
    """Vorteil nach GAE (Schulman u. a. 2016) aus den aufgezeichneten Werten V(s_t):
        δ_t = γ·V(s_{t+1}) − V(s_t)   (t < T−1),   δ_{T−1} = R − V(s_{T−1})
        A_t = Σ_k (γλ)^(k−t) · δ_k  =  δ_t + γλ·A_{t+1}
    λ = 1 ergibt A_t = γ^(T−1−t)·R − V(s_t), also die Monte-Carlo-Rendite mit V als Basislinie;
    λ = 0 ergibt den reinen Ein-Schritt-Fehler. Kleineres λ heisst weniger Varianz und mehr
    Verzerrung — es zählt dann vor allem, was die nächsten Schritte am Wert ändern."""
    v = np.asarray(werte, np.float64)
    T = len(v)
    if T == 0:
        return np.zeros(0), np.zeros(0)
    naechstes = np.append(v[1:], 0.0)
    delta = gamma * naechstes - v
    delta[-1] = R - v[-1]
    A = np.empty(T)
    acc = 0.0
    for t in range(T - 1, -1, -1):
        acc = delta[t] + gamma * lam * acc
        A[t] = acc
    return delta, A


def episode(zeilen: list[dict], R: float, gew: Gewichte = GEWICHTE, gamma: float = GAMMA,
            lam: float | None = None) -> dict:
    """Zeilen EINES Spielers (Metazeilen mit rl.gebiet/tot/spieler), in Tick-Reihenfolge.

    lam = None: Vorteil ist die Monte-Carlo-Rendite mit Φ als Basislinie (A = G = R − Φ_t).
    lam gesetzt: Vorteil nach GAE aus rl.value (der Wertkopf der Verhaltenspolitik). G bleibt
    die MC-Rendite, denn der Wertkopf lernt weiter auf R."""
    rl = [z["rl"] for z in zeilen]
    phi = potenzial([x["gebiet"] for x in rl], [x["tot"] for x in rl], [x["spieler"] for x in rl],
                    True, gew)
    r, G = rueckgaben(phi, R, gamma)
    aus = {"phi": phi, "r": r, "G": G, "A": G.copy(), "R": R}
    if lam is not None:
        werte = [x.get("value") for x in rl]
        if any(w is None for w in werte):
            raise ValueError("GAE braucht rl.value je Entscheidung — die Spur hat keine Werte")
        delta, A = gae(werte, R, gamma, lam)
        aus.update(A=A, delta=delta, v=np.asarray(werte, np.float64))
    return aus


def fuer_partie(zeilen: list[dict], ergebnis: dict[str, dict], gew: Gewichte = GEWICHTE,
                gamma: float = GAMMA, ziel: str = "platz", lam: float | None = None) -> dict[str, dict]:
    """Alle KI-Spieler einer Partie. zeilen: alle Metazeilen (gemischt, Tick-Reihenfolge);
    ergebnis: clientID → hdr.json-Eintrag (platz, von, sieg, land_ende …). Rückgabe clientID →
    episode(...) plus "zeilen" (Indizes in zeilen), damit der Aufrufer die Werte zurückschreiben kann."""
    je: dict[str, list[int]] = {}
    for i, z in enumerate(zeilen):
        je.setdefault(z["clientID"], []).append(i)
    aus = {}
    for cid, idx in je.items():
        ep = episode([zeilen[i] for i in idx], endwert(ergebnis[cid], ziel), gew, gamma, lam)
        ep["zeilen"] = idx
        aus[cid] = ep
    return aus


FORMEL_R = {"platz": "R=(von-platz)/(von-1)",
            "sieg": "R=0.5*(von-platz)/(von-1)+0.5*sieg",
            "gebiet": "R=0.5*min(1,land_ende/land_max_ende)+0.5*sieg"}


def beschreibung(gew: Gewichte = GEWICHTE, gamma: float = GAMMA, ziel: str = "platz",
                 lam: float | None = None) -> dict:
    vorteil = ("A_t=G_t" if lam is None
               else f"A_t=GAE(lambda={lam}) aus rl.value; delta_t=gamma*V_(t+1)-V_t, letzter R-V")
    return {"version": VERSION, "ziel": ziel, "gamma": gamma, "vorteil": "mc" if lam is None else "gae",
            "lam_gae": lam, **asdict(gew),
            "formel": f"{FORMEL_R[ziel]}; Phi=lebt*(c+w_u*tot/(spieler-1)+w_g*log2(1+g*n)/log2(1+n)); "
                      f"G_t=gamma^(T-1-t)*R-Phi_t; {vorteil}"}


def anpassen(p, u, f) -> tuple[np.ndarray, float]:
    """Kleinste Quadrate p ≈ c + w_u·u + w_g·f. Rückgabe (c, w_u, w_g), R²."""
    X = np.stack([np.ones_like(u), u, f], 1)
    koef, *_ = np.linalg.lstsq(X, p, rcond=None)
    rest = p - X @ koef
    r2 = 1.0 - rest.var() / max(p.var(), 1e-12)
    return koef, float(r2)


if __name__ == "__main__":
    import json
    print(json.dumps(beschreibung(), indent=1))
    print("f(1/n), n=90:", float(f_gebiet(1 / 90, 90)), " f(0.1):", float(f_gebiet(0.1, 90)),
          " log2(91) =", math.log2(91))
