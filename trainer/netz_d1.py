"""Netz D1 = D0 + Zusatzfelder als erstklassige Eingänge.

Gegenüber D0 (netz_d0.py) ändert sich nur, was die Zusatzfelder brauchen:
  - opp und own sind breiter (30 Felder je Gegnerplatz, 27 globale; zusatz_felder.py).
  - Einheiten-Kodierer: die eigenen Einheiten (bis 128) und Angriffe (bis 16) als Menge.
    Ihre Zusammenfassung (Mittel ⊕ Maximum) geht in den Kern.
  - own_ref ist ein Zeiger auf diese Liste statt eines blinden 128-Klassen-Kopfs. Erst damit
    sind Angriff abbrechen, Boot abbrechen, Kriegsschiff verschieben, Einheit löschen und
    Aufwerten überhaupt entscheidbar: der alte Kopf sah nie, welche Einheit auf Platz i steht.
Kodierer, Gegner-Transformer, Köpfe, Zellzeiger und Verlust bleiben wie in D0, damit der
Vergleich mit bc2 aussagekräftig ist.

Warum ein Set-Kodierer (DeepSets) und kein Transformer über die Einträge:
  1. Die Liste hat keine bedeutsame Reihenfolge (sie ist nur der Index für own_ref);
     ein gemeinsames MLP je Eintrag ist permutationsäquivariant.
  2. Die Kosten wachsen linear. Eine Self-Attention über 144 Einträge liefe für JEDES
     Sample, nicht nur für die 40 % Zeigerzeilen; Durchsatz ist ein Designziel.
  3. Was zwischen eigenen Einheiten zählt, ist fast nur räumlich. Das bringt das
     Kartenmerkmal am Ort der Einheit (x2, 23×45) direkt mit.
  4. Worauf gezeigt wird, entscheidet die Anfrage aus dem Kontext h, der schon Aktionstyp,
     unit_type und Zielspieler kennt. Ein Angriff bekommt die Einbettung seines Zielgegners
     aus dem Gegner-Transformer.

Zeiger: Logits ⟨q(h), e_i⟩ + b(e_i) wie beim Zellzeiger. Bei cancel_attack auf die
Angriffsliste (ownAttackIds), sonst auf die Einheiten (ownUnitIds) — so definiert
actions.encode den Index. Leere Plätze bekommen −1e4 (nicht −∞: eine Zeile ohne Einheiten
darf keine NaN liefern; ihr Label ist ohnehin IGNORE).
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

import actions as AC
import featurize as FZ
import net as ALT
import netz_d0 as D0
import zusatz_felder as ZF

N_ART = 17                 # 0 = leerer Platz, 1…16 = TYP_LISTE in zusatz/src/felder.ts
LEER = -1e4
CANCEL_ATTACK = int(AC.A.CANCEL_ATTACK)


def _pool(e: torch.Tensor, m: torch.Tensor) -> torch.Tensor:
    """(B,N,d), Maske (B,N) → (B,2d): Mittel ⊕ Maximum über besetzte Plätze, 0 ohne Einträge."""
    mf = m[..., None].to(e.dtype)
    mittel = (e * mf).sum(1) / mf.sum(1).clamp(min=1)
    mx = e.masked_fill(~m[..., None], LEER).amax(1)
    mx = torch.where(m.any(1, keepdim=True), mx, torch.zeros_like(mx))
    return torch.cat([mittel, mx], -1)


class Einheiten(nn.Module):
    """Set-Kodierer für eigene Einheiten und Angriffe (siehe Modulkopf)."""

    def __init__(self, c2: int, e_opp: int, de: int = 64):
        super().__init__()
        self.art = nn.Embedding(N_ART, 16)
        # Einheit: Art-Einbettung ⊕ (spalte, zeile, im_bau, nachladen, unterwegs, alter) ⊕ Karte am Ort
        self.u = nn.Sequential(nn.Linear(16 + 6 + c2, 128), nn.ReLU(inplace=True), nn.Linear(128, de))
        # Ziel eines Angriffs ausserhalb der Top 24 (24) bzw. kein Spieler (25)
        self.a_sonder = nn.Parameter(torch.randn(2, e_opp) * 0.02)
        # Angriff: (truppen, rueckzug, alter) ⊕ Einbettung des Zielgegners
        self.a = nn.Sequential(nn.Linear(3 + e_opp, 128), nn.ReLU(inplace=True), nn.Linear(128, de))

    def forward(self, einh, angr, x2, opp_emb):
        B = einh.shape[0]
        art = einh[..., 0].round().long().clamp(0, N_ART - 1)
        m_u = art > 0
        h2, w2 = x2.shape[-2:]
        sp = (einh[..., 1] * w2).long().clamp(0, w2 - 1)          # spalte/180 → Spalte in x2
        ze = (einh[..., 2] * h2).long().clamp(0, h2 - 1)          # zeile/90 → Zeile in x2
        f2 = x2.flatten(2)                                        # (B,C,h2·w2)
        ort = torch.gather(f2, 2, (ze * w2 + sp)[:, None, :].expand(-1, f2.shape[1], -1)).transpose(1, 2)
        eu = self.u(torch.cat([self.art(art).to(ort.dtype), einh[..., 1:7].to(ort.dtype), ort], -1))

        m_a = angr[..., 0] > 0                                    # truppen > 0 heisst: Platz besetzt
        zp = angr[..., 1].round().long().clamp(0, 25)
        tab = torch.cat([opp_emb, self.a_sonder[None].expand(B, -1, -1).to(opp_emb.dtype)], 1)
        ez = tab.gather(1, zp[..., None].expand(-1, -1, tab.shape[-1]))
        ea = self.a(torch.cat([angr[..., [0, 2, 3]].to(ez.dtype), ez], -1))
        return eu, m_u, ea, m_a


class D1Netz(D0.D0Netz):
    def __init__(self, de: int = 64, **kw):
        kw.setdefault("opp_dim", FZ.OPP_DIM + ZF.DIM_OPP)
        kw.setdefault("own_dim", FZ.OWN_DIM + ZF.DIM_GLOBAL)
        super().__init__(**kw)
        E = ALT.EMB
        c2, c3 = self.enc.d2[0].out_channels, self.enc.d3[0].out_channels
        core, dq = self.kern[0].out_features, self.ctx[0].out_features
        self.einh = Einheiten(c2, E, de)
        # Kern wie D0, nur die Eingangsbreite wächst um die Zusammenfassung der beiden Listen
        self.kern = nn.Sequential(nn.Linear(2 * c3 + E + 128 + 32 + 4 * de, core), nn.ReLU(inplace=True),
                                  nn.Linear(core, core), nn.ReLU(inplace=True))
        del self.koepfe["own_ref"]                                # ersetzt durch den Zeiger
        self.r_qu, self.r_bu = nn.Linear(dq, de), nn.Linear(de, 1)
        self.r_qa, self.r_ba = nn.Linear(dq, de), nn.Linear(de, 1)

    def kodiere(self, b, karte):
        if "einheiten" not in b or "angriffe" not in b:
            raise KeyError("D1 braucht 'einheiten' und 'angriffe' im Batch (Zusatzfelder, --zusatz)")
        x1, x2, x3 = self.enc(karte)
        gmap = torch.cat([x3.mean((2, 3)), x3.amax((2, 3))], 1)
        opp_emb, opp_sum = self.opp(b["opp"], b["opp_mask"])
        eu, mu, ea, ma = self.einh(b["einheiten"], b["angriffe"], x2, opp_emb)
        listen = torch.cat([_pool(eu, mu), _pool(ea, ma)], 1)
        kern = self.kern(torch.cat([gmap, opp_sum, self.own(b["own"]), self.cfg(b["config"]),
                                    listen.to(gmap.dtype)], 1))
        return {"x1": x1, "x2": x2, "x3": x3, "opp_emb": opp_emb, "kern": kern, "karte": karte,
                "eu": eu, "mu": mu, "ea": ea, "ma": ma}

    def _weitere_koepfe(self, out, h, b, z):
        eu, mu, ea, ma = z["eu"], z["mu"], z["ea"], z["ma"]
        lu = torch.einsum("bd,bnd->bn", self.r_qu(h), eu) + self.r_bu(eu).squeeze(-1)
        la = torch.einsum("bd,bnd->bn", self.r_qa(h), ea) + self.r_ba(ea).squeeze(-1)
        lu = lu.masked_fill(~mu, LEER)
        la = F.pad(la.masked_fill(~ma, LEER), (0, lu.shape[1] - la.shape[1]), value=LEER)
        abbruch = (b["labels"]["atype"] == CANCEL_ATTACK)[:, None]
        out["own_ref"] = torch.where(abbruch, la.to(lu.dtype), lu)


class D1Adapter(D0.D0Adapter):
    name = "d1"

    def __init__(self, **kw):
        self.modul = D1Netz(**kw)
        self.kopf_groessen = {h: k for h, k in AC.HEAD_SIZES.items() if h != "fine"}

    def board_schichten(self):
        return super().board_schichten() + [
            {"id": "einh", "name": "Einheiten-Kodierer", "n": self.modul.einh.u[-1].out_features}]


def baue(**kw):
    return D1Adapter(**kw)
