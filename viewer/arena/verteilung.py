"""Gebietsverteilung wie spielweise.ts verteilung(), für Kachelzustände ausserhalb der Engine
(Menschen-Referenz aus own.zst). Gleiche Definitionen, gleiche Normierung:

  komponenten     getrennte Gebietsstücke, 4er-Nachbarschaft, kein Umlauf am Kartenrand
  groesste        Anteil der Kacheln im grössten Stück
  raster          Felder eines 16×16-Rasters (Zeile (y·16/H)|0, Spalte (x·16/W)|0) mit eigener Kachel
  raster_anteil   raster / Rasterfelder mit Land
  streuung        RMS-Abstand zum Schwerpunkt / Kartendiagonale
  streuung_rel    RMS-Abstand / √(n / 2π) (Kreisscheibe gleicher Fläche = 1)
  kueste          Anteil der eigenen Kacheln mit Land- und Uferbit (GameMap.isShore)

Geprüft gegen die TypeScript-Fassung: tests/verteilung_paritaet.py.
Nur numpy; scipy.ndimage beschleunigt die Komponenten, wenn vorhanden (sonst Tiefensuche).
"""
from __future__ import annotations

import math
import os

import numpy as np

RASTER = 16
LAND_BIT, UFER_BIT = 1 << 7, 1 << 6

_ndi = None
if os.environ.get("VERTEILUNG_OHNE_SCIPY") != "1":      # Test: beide Wege prüfen
    try:  # pragma: no cover - je nach Umgebung
        from scipy import ndimage as _ndi
    except Exception:  # noqa: BLE001
        _ndi = None


def raster_index(W: int, H: int) -> np.ndarray:
    """Rasterfeld je Kachel (ref = y·W + x), int16[W·H]; wie (y*16/H)|0 bzw. (x*16/W)|0 in TS."""
    gx = (np.arange(W, dtype=np.int64) * RASTER) // W
    gy = (np.arange(H, dtype=np.int64) * RASTER) // H
    return (gy[:, None] * RASTER + gx[None, :]).astype(np.int16).ravel()


def land_zellen(terrain: np.ndarray, W: int, H: int) -> int:
    land = (terrain & LAND_BIT) != 0
    return int(np.unique(raster_index(W, H)[land]).size)


def _komponenten_dfs(maske: np.ndarray, W: int, H: int) -> tuple[int, int]:
    """(Zahl, Grösse der grössten) per Tiefensuche über die eigenen Kacheln."""
    flach = maske.ravel()
    refs = np.flatnonzero(flach)
    gesehen = np.zeros(flach.size, dtype=bool)
    N = W * H
    komp = groesste = 0
    for s in refs.tolist():
        if gesehen[s]:
            continue
        komp += 1
        gesehen[s] = True
        stapel = [s]
        n = 0
        while stapel:
            r = stapel.pop()
            n += 1
            x = r % W
            for q, ok in ((r - 1, x > 0), (r + 1, x < W - 1), (r - W, r >= W), (r + W, r + W < N)):
                if ok and flach[q] and not gesehen[q]:
                    gesehen[q] = True
                    stapel.append(q)
        groesste = max(groesste, n)
    return komp, groesste


def komponenten(maske: np.ndarray, W: int, H: int) -> tuple[int, int]:
    m = maske.reshape(H, W)
    if _ndi is not None:
        lab, k = _ndi.label(m, structure=[[0, 1, 0], [1, 1, 1], [0, 1, 0]])
        if k == 0:
            return 0, 0
        return int(k), int(np.bincount(lab.ravel())[1:].max())
    return _komponenten_dfs(m, W, H)


def verteilung(maske: np.ndarray, W: int, H: int, terrain: np.ndarray | None, landzellen: int,
               rindex: np.ndarray | None = None) -> dict:
    """maske: bool[W·H] (eigene Kacheln); terrain: u8[W·H] Terrain-Byte oder None (dann kueste None)."""
    maske = np.asarray(maske, dtype=bool).ravel()
    refs = np.flatnonzero(maske)
    n = int(refs.size)
    if n == 0:
        return {"komponenten": 0, "groesste": 0.0, "raster": 0, "raster_anteil": 0.0,
                "streuung": 0.0, "streuung_rel": 0.0, "kueste": 0.0 if terrain is not None else None}
    x = (refs % W).astype(np.float64)
    y = (refs // W).astype(np.float64)
    ri = rindex if rindex is not None else raster_index(W, H)
    raster = int(np.unique(ri[refs]).size)
    mx, my = x.sum() / n, y.sum() / n
    varianz = max(0.0, float((x * x).sum() / n - mx * mx + (y * y).sum() / n - my * my))
    rms = math.sqrt(varianz)
    komp, groesste = komponenten(maske, W, H)
    kueste = None
    if terrain is not None:
        t = terrain[refs]
        kueste = float(np.count_nonzero(((t & LAND_BIT) != 0) & ((t & UFER_BIT) != 0)) / n)
    return {"komponenten": komp, "groesste": groesste / n, "raster": raster,
            "raster_anteil": raster / landzellen if landzellen > 0 else 0.0,
            "streuung": rms / math.sqrt(W * W + H * H), "streuung_rel": rms / math.sqrt(n / (2 * math.pi)),
            "kueste": kueste}


# ---------------------------------------------------------------- Kosten (Config.ts unitInfo)
# Für die Ausgabenschätzung der Menschen. numUnits = Σ min(unitsOwned, unitsConstructed) über die
# gelisteten Typen (costWrapper); hier genähert mit der Zahl der Einheiten dieser Typen, die der
# Spieler gerade besitzt (Stufen mitgezählt, wie unitsOwned). MIRV hängt an den MIRVs aller Spieler.
def kosten(typ: str, n: int, mirvs_gestartet: int = 0) -> int:
    if typ == "City" or typ == "Port" or typ == "Factory":
        return int(min(1_000_000, (2 ** n) * 125_000))
    if typ == "Warship":
        return min(1_000_000, (n + 1) * 250_000)
    if typ == "Defense Post":
        return min(250_000, (n + 1) * 50_000)
    if typ == "SAM Launcher":
        return min(3_000_000, (n + 1) * 1_500_000)
    if typ == "Missile Silo":
        return 1_000_000
    if typ == "Atom Bomb":
        return 750_000
    if typ == "Hydrogen Bomb":
        return 5_000_000
    if typ == "MIRV":
        return 25_000_000 + mirvs_gestartet * 15_000_000
    return 0


# Welche Typen in numUnits zählen (costWrapper-Argumente)
KOSTEN_TYPEN = {"City": ("City",), "Port": ("Port", "Factory"), "Factory": ("Factory", "Port"),
                "Warship": ("Warship",), "Defense Post": ("Defense Post",), "SAM Launcher": ("SAM Launcher",),
                "Missile Silo": ("Missile Silo",), "Atom Bomb": ("Atom Bomb",),
                "Hydrogen Bomb": ("Hydrogen Bomb",), "MIRV": ("MIRV",)}
