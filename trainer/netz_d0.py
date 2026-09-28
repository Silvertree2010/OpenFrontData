"""Netz D0 nach docs/ZIELWAHL_ENTWURF.md §4–§6.

Reihenfolge der Entscheidung: atype → unit_type → Zielspieler → Zelle. Im
Training mit Lehrer-Vorgabe (echte Labels), beim Spielen der Reihe nach.

Aufbau
  Kodierer  Karte 18×90×180, sofort mit Stride 2 auf 45×90, dann 23×45 und
            12×23 (Engpass, 276 Tokens). Für alle Samples läuft keine Faltung
            auf voller Auflösung; die war der teure Teil des alten Netzes.
  Kern      Engpass gemittelt ⊕ Maximum, Gegner-Set-Transformer (env/net.py),
            eigene Zahlen, Spielmodus → 768.
  Köpfe     atype aus dem Kern. unit_type bedingt auf atype. target als Zeiger
            bedingt auf atype und unit_type. Die übrigen Köpfe aus
            h = MLP(Kern ⊕ emb(atype) ⊕ emb(unit_type) ⊕ e_ziel).
  Zeiger    nur für räumliche Zeilen mit Kachel-Label: q = MLP(h ⊕ Zellbreite,
            Zellhöhe). Engpass ⊕ Zusatzebenen (gemittelt) → FiLM(q) → eine
            Self-Attention über 276 Tokens → Decoder mit Skips zurück auf
            90×180. Auf voller Auflösung kommen Karte und die drei
            Zusatzebenen dazu: Gebiet des Ziels, Legalität des Typs, own_frac.
            Logits ⟨P·q, f_c⟩ + b(f_c), maskiert wird im Verlust.

Zellfakten gibt es nur für räumliche Samples. Deshalb gehen sie nur in den
Zeiger, nie in Kern oder atype-Kopf: sonst verriete "Zellfakten vorhanden"
den Aktionstyp (tests/netztest.py prüft das).

Zielspieler je Typ (Entwurf §4.1): Bauwerke SELBST, Boot und Nukes das
target-Label (aus dst_owner, sonst UNAUFGELÖST), Kriegsschiff KEINS, Spawn
NEUTRAL. Die Gebietsebene nimmt beim Boot und bei Nukes immer die echte
smallID dst_owner, auch wenn der Gegner nicht in den Top 24 steht.
"""
from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F

import daten as D  # noqa: F401  (sys.path)
import actions as AC
import featurize as FZ
import net as ALT
import netze as N
import tabellen as T

GH, GW = 90, 180
NC = 18
N_ATYPE = len(AC.A)
N_UNIT = AC.NUM_UNIT_TYPES
UNIT_KEINE = N_UNIT
Z_NEUTRAL, Z_ALLE = AC.TGT_NEUTRAL, AC.TGT_ALL          # 24, 25 wie im target-Kopf
Z_SELBST, Z_KEINS, Z_UNAUF = 26, 27, 28
N_SONDER = 5                                            # Einbettungen für 24…28
_ROLLE = {"bau": Z_SELBST, "hafen": Z_SELBST, "kriegsschiff": Z_KEINS,
          "schiff_bewegen": Z_KEINS, "spawn": Z_NEUTRAL}
ROLLE_T = torch.tensor([_ROLLE.get(g, -1) for g in T.GRUPPEN])   # -1 = aus dem target-Label
UEBRIGE = ("own_ref", "magnitude", "emoji", "quickchat", "embargo_start", "rocket_up", "amount")


def _norm(c, art):
    """GroupNorm läuft unter bf16-Autocast in fp32 (doppelter Speicher je Aktivierung),
    BatchNorm in bf16. Bei "bn" rechnet bewertung.bewerte die BN-Schichten im eval-Modus."""
    if art == "bn":
        return nn.BatchNorm2d(c)
    return nn.GroupNorm(8 if c % 8 == 0 else 1, c)


def cbr(ci, co, s=1, norm="gn"):
    return nn.Sequential(nn.Conv2d(ci, co, 3, s, 1, bias=False), _norm(co, norm), nn.ReLU(inplace=True))


class Res(nn.Module):
    def __init__(self, c, norm="gn"):
        super().__init__()
        self.a = cbr(c, c, norm=norm)
        self.b = nn.Sequential(nn.Conv2d(c, c, 3, 1, 1, bias=False), _norm(c, norm))
        nn.init.zeros_(self.b[1].weight)        # Start als Identität

    def forward(self, x):
        return F.relu(x + self.b(self.a(x)))


class Kodierer(nn.Module):
    def __init__(self, c1, c2, c3, tiefe, norm="gn"):
        super().__init__()
        self.d1 = cbr(NC, c1, 2, norm)                             # 45×90
        self.s1 = nn.Sequential(*[Res(c1, norm) for _ in range(tiefe[0])])
        self.d2 = cbr(c1, c2, 2, norm)                             # 23×45
        self.s2 = nn.Sequential(*[Res(c2, norm) for _ in range(tiefe[1])])
        self.d3 = cbr(c2, c3, 2, norm)                             # 12×23
        self.s3 = nn.Sequential(*[Res(c3, norm) for _ in range(tiefe[2])])

    def forward(self, m):
        x1 = self.s1(self.d1(m))
        x2 = self.s2(self.d2(x1))
        x3 = self.s3(self.d3(x2))
        return x1, x2, x3


class Zeiger(nn.Module):
    """Bedingter Zell-Zeiger auf 90×180 (Entwurf §4, D0 Punkte 2–4)."""

    def __init__(self, c1, c2, c3, dq, c0=32, df=64, heads=4, norm="gn"):
        super().__init__()
        self.ein = nn.Conv2d(c3 + 3, c3, 1)
        self.film = nn.Linear(dq, 2 * c3)
        nn.init.zeros_(self.film.weight)
        nn.init.zeros_(self.film.bias)
        self.pos = nn.Parameter(torch.randn(1, 12 * 23, c3) * 0.02)
        self.attn = nn.TransformerEncoderLayer(c3, heads, 2 * c3, dropout=0.0,
                                               batch_first=True, norm_first=True)
        self.u2, self.m2 = nn.Conv2d(c3, c2, 1), cbr(c2, c2, norm=norm)
        self.u1, self.m1 = nn.Conv2d(c2, c1, 1), cbr(c1, c1, norm=norm)
        self.u0, self.m0 = nn.Conv2d(c1, c0, 1), cbr(c0 + NC + 3, c0, norm=norm)
        self.f = nn.Conv2d(c0, df, 1)
        self.p = nn.Linear(dq, df)
        self.bias = nn.Conv2d(df, 1, 1)

    def forward(self, q, x1, x2, x3, karte, ebenen):
        S, C, h3, w3 = x3.shape
        z = self.ein(torch.cat([x3, F.adaptive_avg_pool2d(ebenen, (h3, w3))], 1))
        ga, be = self.film(q).chunk(2, 1)
        z = z * (1 + ga[:, :, None, None]) + be[:, :, None, None]
        t = self.attn(z.flatten(2).transpose(1, 2) + self.pos)
        z = t.transpose(1, 2).reshape(S, C, h3, w3)
        y = self.m2(F.interpolate(self.u2(z), size=x2.shape[-2:]) + x2)
        y = self.m1(F.interpolate(self.u1(y), size=x1.shape[-2:]) + x1)
        y = F.interpolate(self.u0(y), size=karte.shape[-2:])
        f = self.f(self.m0(torch.cat([y, karte, ebenen], 1)))
        lg = torch.einsum("bchw,bc->bhw", f, self.p(q).to(f.dtype)) + self.bias(f).squeeze(1)
        return lg.flatten(1)


class D0Netz(nn.Module):
    def __init__(self, c1=64, c2=128, c3=256, tiefe=(1, 1, 1), core=768, dq=256, norm="gn",
                 zeiger_anteil=0.4, opp_dim=FZ.OPP_DIM, own_dim=FZ.OWN_DIM):
        super().__init__()
        E = ALT.EMB
        # Zusatzfelder (trainer/zusatz_felder.py) hängen hinten an opp bzw. own
        self.opp_dim, self.own_dim = opp_dim, own_dim
        # Anteil räumlicher Zeilen im Batch ist ~30 %; 40 % lässt > 4 Standardabweichungen Luft
        self.zeiger_anteil = zeiger_anteil
        self.enc = Kodierer(c1, c2, c3, tiefe, norm)
        self.opp = ALT.OppEncoder(d_in=opp_dim)
        self.own = nn.Sequential(nn.Linear(own_dim, 128), nn.ReLU(inplace=True), nn.Linear(128, 128))
        self.cfg = nn.Sequential(nn.Linear(FZ.CONFIG_DIM, 32), nn.ReLU(inplace=True))
        self.kern = nn.Sequential(nn.Linear(2 * c3 + E + 128 + 32, core), nn.ReLU(inplace=True),
                                  nn.Linear(core, core), nn.ReLU(inplace=True))
        H = AC.HEAD_SIZES
        self.e_a = nn.Embedding(N_ATYPE, 32)
        self.e_u = nn.Embedding(N_UNIT + 1, 32)
        self.e_sonder = nn.Parameter(torch.randn(N_SONDER, E) * 0.02)
        self.h_atype = nn.Linear(core, H["atype"])
        self.h_unit = nn.Sequential(nn.Linear(core + 32, 256), nn.ReLU(inplace=True),
                                    nn.Linear(256, H["unit_type"]))
        self.t_q = nn.Linear(core + 64, E)
        self.t_sonder = nn.Linear(core + 64, 2)
        self.ctx = nn.Sequential(nn.Linear(core + 64 + E, dq), nn.ReLU(inplace=True),
                                 nn.Linear(dq, dq), nn.ReLU(inplace=True))
        self.koepfe = nn.ModuleDict({h: nn.Linear(dq, H[h]) for h in UEBRIGE})
        self.h_value = nn.Linear(core, 1)
        self.q = nn.Sequential(nn.Linear(dq + 2, dq), nn.ReLU(inplace=True), nn.Linear(dq, dq))
        self.zeiger = Zeiger(c1, c2, c3, dq, norm=norm)

    @staticmethod
    def ziel_index(b):
        """Index in [Gegner 0…23, NEUTRAL, ALLE, SELBST, KEINS, UNAUFGELÖST]."""
        t, g = b["labels"]["target"], b["g"]
        zi = torch.where(t >= 0, t, torch.full_like(t, Z_KEINS))
        rolle = ROLLE_T.to(g.device)[g.clamp(min=0)]
        raum = g >= 0
        zi = torch.where(raum & (rolle >= 0), rolle, zi)
        return torch.where(raum & (rolle < 0) & (t < 0), torch.full_like(t, Z_UNAUF), zi)

    @staticmethod
    def ebenen(b, idx):
        """(S,3,90,180): Gebiet des Ziels, Legalität des Typs, own_frac."""
        g = b["g"][idx]
        owner = b["owner"][idx].long()
        of = b["own_frac"][idx].float() / 255.0
        legal = ((b["legal"][idx].to(torch.int32) >> b["bit"][idx].to(torch.int32)[:, None]) & 1).float()
        rolle = ROLLE_T.to(g.device)[g][:, None]
        gebiet = torch.where(rolle == Z_SELBST, (of > 0).float(),
                             torch.where(rolle == Z_NEUTRAL, (owner == 0).float(),
                                         torch.where(rolle == Z_KEINS, torch.zeros_like(of),
                                                     (owner == b["dst"][idx][:, None]).float())))
        return torch.stack([gebiet, legal, of], 1).view(-1, 3, GH, GW)

    def _weitere_koepfe(self, out, h, b, z):
        """Einhängepunkt für abgeleitete Netze (netz_d1.py: own_ref-Zeiger). D0: nichts."""

    def forward(self, b, karte):
        return self.koepfe_aus(b, self.kodiere(b, karte))

    def kodiere(self, b, karte):
        """Alles, was nicht von Labels abhängt. Die Inferenz rechnet das einmal und
        fragt danach die Köpfe Schritt für Schritt bedingt ab (spielen.py)."""
        x1, x2, x3 = self.enc(karte)
        gmap = torch.cat([x3.mean((2, 3)), x3.amax((2, 3))], 1)
        opp_emb, opp_sum = self.opp(b["opp"], b["opp_mask"])
        kern = self.kern(torch.cat([gmap, opp_sum, self.own(b["own"]), self.cfg(b["config"])], 1))
        return {"x1": x1, "x2": x2, "x3": x3, "opp_emb": opp_emb, "kern": kern, "karte": karte}

    def koepfe_aus(self, b, z, zeiger=True):
        """Köpfe, bedingt auf die Labels in b (Lehrer-Vorgabe bzw. bereits gewählte Schritte)."""
        lab = b["labels"]
        x1, x2, x3, opp_emb, kern, karte = z["x1"], z["x2"], z["x3"], z["opp_emb"], z["kern"], z["karte"]
        B = karte.shape[0]
        out = {"atype": self.h_atype(kern), "value": self.h_value(kern).squeeze(-1)}
        za = self.e_a(lab["atype"].clamp(0, N_ATYPE - 1))
        u = lab["unit_type"]
        zu = self.e_u(torch.where(u >= 0, u, torch.full_like(u, UNIT_KEINE)))
        out["unit_type"] = self.h_unit(torch.cat([kern, za], 1))
        kau = torch.cat([kern, za, zu], 1)
        opp_lg = torch.einsum("be,bne->bn", self.t_q(kau), opp_emb)
        out["target"] = torch.cat([opp_lg.masked_fill(~b["opp_mask"], -1e9), self.t_sonder(kau).to(opp_lg.dtype)], 1)
        tab = torch.cat([opp_emb, self.e_sonder[None].expand(B, -1, -1).to(opp_emb.dtype)], 1)
        ez = tab[torch.arange(B, device=karte.device), self.ziel_index(b)]
        h = self.ctx(torch.cat([kau, ez.to(kau.dtype)], 1))
        for k, l in self.koepfe.items():
            out[k] = l(h)
        self._weitere_koepfe(out, h, b, z)          # D0: nichts; D1 (netz_d1.py): own_ref-Zeiger
        if not zeiger:
            return out
        # Zeiger auf einer festen Zahl S von Zeilen, ohne Sync: die GPU sortiert räumliche
        # Zeilen mit Kachel-Label nach vorne, der Rest der S Plätze sind Füllzeilen, deren
        # Ausgabe der Verlust ignoriert. Feste Form heisst: kein Neukompilieren und kein
        # cudnn-Autotuning je Batch. Räumliche Zeilen jenseits von S (Überlauf) bekommen
        # coarse_da = False und fallen aus dem Kachel-Verlust. Im Eval (zeiger_voll) alle Zeilen.
        ar = torch.arange(B, device=karte.device)
        if b.get("zeiger_voll"):
            idx = ar
        else:
            raum = (b["g"] >= 0) & (lab["coarse"] >= 0)
            S = min(B, math.ceil(self.zeiger_anteil * B))
            idx = torch.argsort(torch.where(raum, ar, ar + B))[:S]
        wh = b["wh"][idx]
        geo = torch.stack([wh[:, 0] / GW, wh[:, 1] / GH], 1) / 20.0   # Zellgrösse in Kacheln, skaliert
        q = self.q(torch.cat([h[idx], geo.to(h.dtype)], 1))
        lg = self.zeiger(q, x1[idx], x2[idx], x3[idx], karte[idx], self.ebenen(b, idx).to(karte.dtype))
        out["coarse"] = lg.new_zeros(B, GH * GW).index_copy(0, idx, lg)
        out["coarse_da"] = torch.zeros(B, dtype=torch.bool, device=karte.device).index_fill_(0, idx, True)
        return out


class D0Adapter(N.NetzAdapter):
    name = "d0"

    def __init__(self, **kw):
        self.modul = D0Netz(**kw)
        self.kopf_groessen = {h: k for h, k in AC.HEAD_SIZES.items() if h != "fine"}

    def vorwaerts(self, b):
        return self.modul(b, self._karte(b))

    def kompiliere(self, umfang: str = "alles"):
        """"alles": Kodierer und Zeiger. "kodierer": nur der Kodierer (statische Formen).
        Rückfall, weil der Zeiger mit dynamischer Zeilenzahl bei Batch 512 in einem
        Inductor-Kernel abstürzte (CUDA illegal address, 11.09.)."""
        self.modul.enc.compile()
        if umfang == "alles":
            self.modul.zeiger.compile()               # feste Zeilenzahl im Training

    def board_schichten(self):
        m = self.modul
        return [{"id": "map_in", "name": "Karten-Eingang", "n": NC},
                {"id": "enc", "name": "Kodierer 12×23", "n": m.enc.d3[0].out_channels},
                {"id": "opp", "name": "Gegner-Transformer", "n": ALT.EMB},
                {"id": "core", "name": "Kern", "n": m.kern[0].out_features},
                {"id": "ctx", "name": "Kontext h", "n": m.ctx[0].out_features}]


def baue(**kw):
    return D0Adapter(**kw)
