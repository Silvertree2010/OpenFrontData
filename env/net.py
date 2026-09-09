"""
Das Policy-Value-Netz.

Fünf Bausteine, wie geplant:
  MapEncoder      CNN über die 17 Karten-Ebenen → räumliche Merkmale (für den
                  Grob-Kachel-Kopf) + ein globaler Vektor
  OppEncoder      Set-Transformer über ≤24 Gegner → je-Gegner-Einbettung
                  (für den Ziel-Zeiger) + maskierte Zusammenfassung
  OwnEncoder      MLP über die eigenen Zahlen
  Core            fusioniert alles → gemeinsamer Zustand
  Köpfe           die 13 Entscheidungen + Wert

Zwei Kniffe, die die Aufgabe erst tragbar machen:
  • Grob-Kachel = 1×1-Faltung über die 90×180-Merkmalskarte → Hitzekarte.
    "Wohin" fällt natürlich aus dem CNN, kein Sonderbau.
  • Ziel = Zeiger: Punktprodukt zwischen einer Abfrage und den Gegner-
    Einbettungen, statt fester Ausgabeplätze. So ist die Gegnerzahl egal.
  • Fein-Kachel ist autoregressiv auf die Grobzelle bedingt (AlphaStar-Stil):
    beim Training per Lehrer-Vorgabe, beim Spielen per argmax der Grobkarte.
"""
from __future__ import annotations

import sys
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, "env")
import actions as AC

# Formen der Beobachtung (aus obs.ts)
NUM_MAP_CH = 18                  # = len(obs.CHANNELS); bei Kanal-Änderung mitziehen
GH, GW = 90, 180                 # Kartenraster (Höhe, Breite) — passt zu COARSE_H/W
from featurize import OWN_DIM, OPP_DIM, CONFIG_DIM   # Dims aus dem Featurizer (single source of truth)
MAX_OPP = AC.MAX_OPP             # 24

EMB = 160                        # Einbettungsbreite (Gegner/Zeiger)
CORE = 768


class MapEncoder(nn.Module):
    """CNN. Erhält die volle 90×180-Auflösung für den Grob-Kopf und liefert
    zusätzlich einen global gepoolten Vektor."""
    def __init__(self, ch=NUM_MAP_CH):
        super().__init__()
        self.stem = nn.Sequential(
            nn.Conv2d(ch, 96, 3, padding=1), nn.GroupNorm(8, 96), nn.ReLU(inplace=True),
            nn.Conv2d(96, 128, 3, padding=1), nn.GroupNorm(8, 128), nn.ReLU(inplace=True),
        )  # (B,128,90,180) — DIESE Karte speist den Grob-Kachel-Kopf
        self.down = nn.Sequential(
            nn.Conv2d(128, 192, 3, stride=2, padding=1), nn.GroupNorm(8, 192), nn.ReLU(inplace=True),  # 45×90
            nn.Conv2d(192, 256, 3, stride=2, padding=1), nn.GroupNorm(8, 256), nn.ReLU(inplace=True), # 23×45
            nn.Conv2d(256, 320, 3, stride=2, padding=1), nn.GroupNorm(8, 320), nn.ReLU(inplace=True), # 12×23
        )
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.gdim = 320

    def forward(self, m):
        spatial = self.stem(m)              # (B,128,90,180)
        g = self.pool(self.down(spatial)).flatten(1)   # (B,320)
        return spatial, g


class OppEncoder(nn.Module):
    """Set-Transformer über die Gegner. Reihenfolge- und zahl-unabhängig dank
    Maske. Gibt je-Gegner-Einbettungen (für den Zeiger) + Zusammenfassung."""
    def __init__(self, d_in=OPP_DIM, emb=EMB, heads=4, layers=3):
        super().__init__()
        self.inp = nn.Linear(d_in, emb)
        layer = nn.TransformerEncoderLayer(emb, heads, emb * 2, batch_first=True, dropout=0.0)
        self.enc = nn.TransformerEncoder(layer, layers)

    def forward(self, opp, mask):
        # opp: (B,N,d_in)  mask: (B,N) True = gültiger Gegner
        x = self.inp(opp)
        pad = ~mask                                   # True = ignorieren
        # Zeilen ganz ohne gueltigen Gegner (mask komplett False) lassen den
        # Nested-Tensor-Schnellpfad von TransformerEncoder in eval() mit
        # "to_padded_tensor: at least one constituent tensor should have
        # non-zero numel" abstuerzen, weil dort *jede* Position als Padding
        # markiert waere. Wir geben dem Encoder fuer solche Zeilen eine
        # entschaerfte Maske (eine Position als "gueltig" vorgetaeuscht);
        # deren Encoder-Ausgabe ist fuer das Ergebnis irrelevant, denn unten
        # wird mit der ORIGINALEN mask gewichtet (Gewicht 0 -> Beitrag 0) und
        # der Zeiger-Kopf maskiert die Zeile ohnehin mit -1e9. Fuer alle
        # anderen Zeilen aendert sich nichts (bitgleich).
        empty = pad.all(dim=1)                         # (B,) keine gueltigen Gegner
        if empty.any():
            pad = pad.clone()
            pad[empty, 0] = False
        x = self.enc(x, src_key_padding_mask=pad)     # (B,N,emb)
        # maskierter Mittelwert als Zusammenfassung (mit der ORIGINALEN mask)
        w = mask.float().unsqueeze(-1)
        summary = (x * w).sum(1) / w.sum(1).clamp(min=1)
        return x, summary


class Net(nn.Module):
    def __init__(self):
        super().__init__()
        self.map = MapEncoder()
        self.opp = OppEncoder()
        self.own = nn.Sequential(nn.Linear(OWN_DIM, 128), nn.ReLU(inplace=True), nn.Linear(128, 128))
        self.cfg = nn.Sequential(nn.Linear(CONFIG_DIM, 32), nn.ReLU(inplace=True))  # Spiel-Modifier
        self.core = nn.Sequential(
            nn.Linear(320 + EMB + 128 + 32, CORE), nn.ReLU(inplace=True),
            nn.Linear(CORE, CORE), nn.ReLU(inplace=True),
        )
        H = AC.HEAD_SIZES
        # Kategorische Köpfe direkt aus dem Kern
        self.h_atype = nn.Linear(CORE, H["atype"])
        self.h_unit = nn.Linear(CORE, H["unit_type"])
        self.h_ownref = nn.Linear(CORE, H["own_ref"])
        self.h_mag = nn.Linear(CORE, H["magnitude"])
        self.h_emoji = nn.Linear(CORE, H["emoji"])
        self.h_qc = nn.Linear(CORE, H["quickchat"])
        self.h_emb = nn.Linear(CORE, H["embargo_start"])
        self.h_rocket = nn.Linear(CORE, H["rocket_up"])
        self.h_amount = nn.Linear(CORE, H["amount"])
        self.h_value = nn.Linear(CORE, 1)
        # Ziel = Zeiger: Abfrage · Gegner-Einbettung, plus 2 Sonderziele
        self.tgt_query = nn.Linear(CORE, EMB)
        self.tgt_special = nn.Linear(CORE, 2)   # NEUTRAL, ALLE
        # Grob-Kachel: 1×1-Faltung über die 90×180-Merkmalskarte → Hitzekarte
        self.coarse_head = nn.Conv2d(128, 1, 1)
        # Fein-Kachel: Kern ⊕ Merkmal der gewählten Grobzelle → 64
        self.fine_head = nn.Sequential(
            nn.Linear(CORE + 128, 256), nn.ReLU(inplace=True), nn.Linear(256, AC.NUM_FINE))

    def forward(self, map_t, own_t, opp_t, opp_mask, config_t=None, coarse_idx=None):
        spatial, gmap = self.map(map_t)              # (B,128,90,180), (B,320)
        opp_emb, opp_sum = self.opp(opp_t, opp_mask) # (B,N,EMB), (B,EMB)
        own = self.own(own_t)                        # (B,128)
        if config_t is None:                         # Rueckwaertskompat: neutraler Modifier
            config_t = map_t.new_zeros(map_t.shape[0], CONFIG_DIM)
        cfg = self.cfg(config_t)                     # (B,32)
        core = self.core(torch.cat([gmap, opp_sum, own, cfg], dim=1))  # (B,CORE)

        B = map_t.shape[0]
        out = {}
        out["atype"] = self.h_atype(core)
        out["unit_type"] = self.h_unit(core)
        out["own_ref"] = self.h_ownref(core)
        out["magnitude"] = self.h_mag(core)
        out["emoji"] = self.h_emoji(core)
        out["quickchat"] = self.h_qc(core)
        out["embargo_start"] = self.h_emb(core)
        out["rocket_up"] = self.h_rocket(core)
        out["amount"] = self.h_amount(core)
        out["value"] = self.h_value(core).squeeze(-1)

        # Ziel-Zeiger
        q = self.tgt_query(core)                                  # (B,EMB)
        opp_logits = torch.einsum("be,bne->bn", q, opp_emb)       # (B,N)
        opp_logits = opp_logits.masked_fill(~opp_mask, -1e9)
        out["target"] = torch.cat([opp_logits, self.tgt_special(core)], dim=1)  # (B,N+2)

        # Grob-Kachel-Hitzekarte
        coarse_map = self.coarse_head(spatial).flatten(1)         # (B,90*180)
        out["coarse"] = coarse_map

        # Fein-Kachel, bedingt auf die Grobzelle (Lehrer-Vorgabe oder argmax)
        idx = coarse_idx if coarse_idx is not None else coarse_map.argmax(1)
        cy = (idx // GW).clamp(0, GH - 1)
        cx = (idx % GW).clamp(0, GW - 1)
        feat = spatial[torch.arange(B), :, cy, cx]                # (B,128)
        out["fine"] = self.fine_head(torch.cat([core, feat], dim=1))
        return out


def head_masks_for(atypes):
    """Für BC: welche Köpfe supervidiert werden, hängt am Aktionstyp
    (HEAD_SCHEMA). Gibt je Kopf eine Bool-Maske (B,) zurück."""
    heads = list(AC.HEAD_SIZES.keys())
    masks = {h: torch.zeros(len(atypes), dtype=torch.bool) for h in heads}
    for i, a in enumerate(atypes.tolist()):
        for h in AC.HEAD_SCHEMA.get(AC.A(a), ()):
            if h in masks:
                masks[h][i] = True
    return masks
