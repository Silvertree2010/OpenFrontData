"""Schnittstelle zwischen Trainer und Netz.

Der Trainer kennt nur NetzAdapter. Ein neues Netz steckt man ein, indem man
einen Adapter schreibt und ihn mit --netz modul:funktion wählt (die Funktion
gibt den Adapter zurück). Eingebaut: "alt" = env/net.py unverändert,
"d0" = netz_d0.py (Entwurf D0).

Vertrag
-------
vorwaerts(b) bekommt einen Batch auf dem Gerät (daten.auf_geraet) mit:
  map        float (B,18,90,180)   dequantisiert wie env/dataset.dequantize
  own        float (B,OWN_DIM)     opp float (B,24,OPP_DIM)   opp_mask bool (B,24)
  config     float (B,CONFIG_DIM)
  labels     dict Kopf → long (B,), IGNORE=-100 wo nicht supervidiert.
             Lehrer-Vorgabe ist erlaubt (coarse für den Fein-Kopf, atype/
             unit_type/target für einen bedingten Kachel-Kopf nach D0).
  legal      uint8 (B,16200)  legal-Bits je Zelle, Nullen bei nicht-räumlichen Zeilen
  bit        long (B,)        welches legal-Bit für diese Zeile gilt
  owner      int16 (B,16200)  owner_major (smallID)
  own_frac   uint8 (B,16200)  exakter Eigenanteil ×255
  xy, wh     float (B,2)      Label-Kachel (x,y) und Kartengrösse (W,H) in Kacheln
  g          long (B,)        räumliche Gruppe (tabellen.GRUPPEN), -1 = keine
  dst        long (B,)        dst_owner (smallID)
  lset       long (B,16)      weitere Label-Zellen für den L-set-Verlust, -1 = leer
und liefert ein dict mit Logits je Kopf aus kopf_groessen (Form (B,K)) plus
"value" (B,). Der Kachel-Kopf heisst "coarse" und hat 16200 Logits auf dem
90×180-Raster. Maskiert wird im Verlust; ein Netz darf selbst schon maskieren.
Zellfakten (legal/owner/own_frac) gibt es nur für räumliche Zeilen: ein Netz
darf sie nur dort verwenden, wo der Aktionstyp schon feststeht.
"""
from __future__ import annotations

import importlib

import torch
import torch.nn as nn

import daten as D  # noqa: F401  (setzt sys.path auf env/ und materializer/py/)
import actions as AC


class NetzAdapter:
    name = "?"
    kopf_groessen: dict[str, int] = {}
    modul: nn.Module
    cl = False

    def vorwaerts(self, b: dict) -> dict:
        raise NotImplementedError

    def speicherformat(self, channels_last: bool):
        """channels_last für Faltungen: Gewichte umstellen, Karte beim Vorwärtslauf auch."""
        if channels_last:
            self.modul.to(memory_format=torch.channels_last)
            self.cl = True

    def _karte(self, b):
        return b["map"].contiguous(memory_format=torch.channels_last) if self.cl else b["map"]

    def kompiliere(self, umfang: str = "alles"):
        """torch.compile an Ort und Stelle; die Schlüssel im state_dict bleiben gleich.
        umfang: "alles" oder ein netzeigener Teil (D0: "kodierer")."""
        self.modul.compile()

    def board_schichten(self) -> list[dict]:
        """Schichtliste für das Live-Board (optional)."""
        return []


class AltesNetz(NetzAdapter):
    """env/net.py, ohne Änderung. Die Eingaben des Formats 2 sind dieselben wie
    im alten Format (Karten byte-gleich, own/opp/config aus denselben Featurizern)."""
    name = "alt"

    def __init__(self):
        import net as N
        self.N = N
        self.modul = N.Net()
        self.kopf_groessen = dict(AC.HEAD_SIZES)

    def vorwaerts(self, b):
        idx = b["labels"]["coarse"].clamp(min=0)          # Lehrer-Vorgabe für den Fein-Kopf
        return self.modul(self._karte(b), b["own"], b["opp"], b["opp_mask"],
                          config_t=b["config"], coarse_idx=idx)

    def board_schichten(self):
        N = self.N
        return [
            {"id": "map_in", "name": "Karten-Eingang", "n": N.NUM_MAP_CH},
            {"id": "map_stem", "name": "Karten-CNN", "n": 128},
            {"id": "map_down", "name": "Karten-Tiefe", "n": 320},
            {"id": "opp", "name": "Gegner-Transformer", "n": N.EMB},
            {"id": "own", "name": "Eigene Zahlen", "n": 128},
            {"id": "cfg", "name": "Spielmodus", "n": 32},
            {"id": "core1", "name": "Kern 1", "n": N.CORE},
            {"id": "core2", "name": "Kern 2", "n": N.CORE},
        ]


def baue_netz(name: str, **kw) -> NetzAdapter:
    """kw geht an das Netz weiter, z. B. opp_dim für die Zusatzfelder (--zusatz)."""
    if name == "alt":
        if kw:
            raise ValueError(f"env/net.py nimmt keine Optionen ({sorted(kw)}); Zusatzfelder brauchen --netz d0")
        return AltesNetz()
    if name == "d0":
        import netz_d0
        return netz_d0.D0Adapter(**kw)
    if name == "d1":                    # D0 + Zusatzfelder, Einheiten-Kodierer, own_ref-Zeiger
        import netz_d1
        return netz_d1.D1Adapter(**kw)
    modul, _, fn = name.partition(":")
    return getattr(importlib.import_module(modul), fn or "baue")(**kw)
