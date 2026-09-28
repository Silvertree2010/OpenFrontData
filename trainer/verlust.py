"""Verlust des BC-Trainers.

Gewichte (materializer/DESIGN.md §4):
  w       gewichtet "welche Aktion" (Nichtstun 200, zusammengefasster Angriff = Klickzahl)
  w_tick  gewichtet "ob gehandelt wird", in Spieler-Ticks (Nichtstun 200, Samples
          eines Spieler-Ticks teilen sich 1)
Dazu optional das alte Advantage-Gewicht aus dem Spielausgang (bc_train.ADV_BETA),
batch-zentriert, geklemmt, Mittel 1.

atype, Modus "faktor" (Standard): Nichtstun ist Klasse 0 des bestehenden
atype-Kopfs. log p(a) zerfällt exakt in
    log p(a) = log P(handeln) + log p(a | handeln)      für a ≠ Nichtstun
    log p(0) = log P(nicht handeln)
Der erste Teil ("ob") wird mit w_tick gewichtet, der zweite ("welche") mit w
über die Aktions-Samples. Mit gleichen Gewichten ist die Summe genau die
Kreuzentropie über alle 21 Klassen (tests/selbsttest.py prüft das). Modus
"flach": eine Kreuzentropie über 21 Klassen, gewichtet mit w. Dann trägt
Nichtstun ~98 % des Gewichts (gemessen: Σw noop 2,39 Mio gegen 55k act in 40 Partien).

Kachel-Kopf (ZIELWAHL_ENTWURF §6): Logits ausserhalb der legalen Zellen des
Typs werden −∞, log-softmax in fp32. Ziel 0,5·onehot(c*) + 0,5·q_σ mit
q_σ(c) ∝ exp(−d²/2σ²)·legal(c), d in echten Kacheln von der Zellmitte zu
res_tile, σ = R/2. Liegt die Label-Zelle ausserhalb der Maske, fällt das Sample
aus dem Kachel-Verlust und wird gezählt. Die Label-Zelle wird nie in die Maske
geodert.
"""
from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn.functional as F

import daten as D
import tabellen as T

GW, GH = 180, 90


@dataclass
class VerlustOpt:
    atype: str = "faktor"          # "faktor" | "flach"
    adv_beta: float = 1.5          # 0 = reines BC
    adv_min: float = 0.25
    adv_max: float = 4.0
    value_w: float = 1.0
    maske: bool = True             # Kachel-Logits mit legal-Bits maskieren
    sigma_faktor: float = 0.5      # σ = faktor · R
    weich_anteil: float = 0.5      # Anteil des weichen Ziels
    fein: bool = False             # Fein-Kopf lernen (Entwurf D0: nein)
    raum: str = "weich"            # "weich" (D0-Standard) | "lset" (Entwurf §6, E6)


_R = torch.tensor([float(T.R_KACHELN[g] or 0) for g in T.GRUPPEN])


def r_kacheln(g: torch.Tensor) -> torch.Tensor:
    """R je Zeile (0 für Zeilen ohne Gruppe oder MIRV)."""
    return torch.where(g >= 0, _R.to(g.device)[g.clamp(min=0)], torch.zeros((), device=g.device))


def adv_gewicht(win: torch.Tensor, o: VerlustOpt) -> torch.Tensor:
    if o.adv_beta <= 0:
        return torch.ones_like(win)
    w = torch.exp(o.adv_beta * (win - win.mean())).clamp(o.adv_min, o.adv_max)
    return w / w.mean().clamp(min=1e-6)


def zellmaske(b: dict) -> torch.Tensor:
    """(B,16200) bool: legal-Bit der Zeile gesetzt."""
    return ((b["legal"].to(torch.int32) >> b["bit"].to(torch.int32)[:, None]) & 1).bool()


def weiches_ziel(maske, xy, wh, sigma, cstar, anteil):
    """(B,16200) Zielverteilung, Summe 1 je Zeile, 0 ausserhalb der Maske
    (ausser Zeilen ohne legale Masse, die fallen auf onehot zurück)."""
    B, dev = maske.shape[0], maske.device
    onehot = torch.zeros(B, GH * GW, device=dev).scatter_(1, cstar[:, None], 1.0)
    if anteil <= 0:
        return onehot
    cx = (torch.arange(GW, device=dev) + 0.5)[None] * wh[:, 0:1] / GW     # (B,180) Kacheln
    cy = (torch.arange(GH, device=dev) + 0.5)[None] * wh[:, 1:2] / GH     # (B,90)
    s2 = 2.0 * sigma.clamp(min=1e-3)[:, None] ** 2
    gx = torch.exp(-(cx - xy[:, 0:1]) ** 2 / s2)
    gy = torch.exp(-(cy - xy[:, 1:2]) ** 2 / s2)
    q = (gy[:, :, None] * gx[:, None, :]).reshape(B, -1) * maske.float()
    s = q.sum(1, keepdim=True)
    q = q / s.clamp(min=1e-30)
    a = anteil * (s > 0).float()
    return (1.0 - a) * onehot + a * q


def berechne(out: dict, b: dict, o: VerlustOpt):
    """Gesamtverlust, Teilverluste (Tensoren, ohne Sync) und Zusatzinfo für Kennzahlen."""
    lab = b["labels"]
    adv = adv_gewicht(b["win"], o)
    w = b["w"] * adv
    wt = b["wt"] * adv
    logs = {}

    # ---------------- atype mit Nichtstun als Klasse 0
    lg = out["atype"].float()
    a = lab["atype"]
    if o.atype == "faktor":
        lse = torch.logsumexp(lg, 1)
        logp0 = lg[:, 0] - lse
        log_act = torch.logsumexp(lg[:, 1:], 1) - lse
        noop = a == 0
        l_ob = -(torch.where(noop, logp0, log_act) * wt).sum() / wt.sum().clamp(min=1e-6)
        logq = F.log_softmax(lg[:, 1:], 1).gather(1, (a - 1).clamp(min=0)[:, None]).squeeze(1)
        wa = w * (~noop).float()
        l_welche = -(logq * wa).sum() / wa.sum().clamp(min=1e-6)
        la = l_ob + l_welche
        logs["ob"], logs["welche"] = l_ob, l_welche
    else:
        la = (F.cross_entropy(lg, a, reduction="none") * w).sum() / w.sum().clamp(min=1e-6)
    logs["atype"] = la
    total = la

    # ---------------- kategorische Köpfe (nur Zeilen mit Label)
    for h in D.HEADS:
        if h in ("atype", "coarse") or (h == "fine" and not o.fein) or h not in out:
            continue
        y = lab[h]
        m = (y != D.IGNORE).float()
        per = F.cross_entropy(out[h].float(), y.clamp(min=0), reduction="none")
        wm = w * m
        l = (per * wm).sum() / wm.sum().clamp(min=1e-6)
        logs[h] = l
        total = total + l

    # ---------------- Kachel-Kopf, maskiert
    y = lab["coarse"]
    g = b["g"]
    gueltig = (y != D.IGNORE) & (g >= 0)
    if o.maske:
        maske = zellmaske(b)
    else:
        maske = torch.ones(y.shape[0], GH * GW, dtype=torch.bool, device=y.device)
    yc = y.clamp(min=0)
    in_maske = maske.gather(1, yc[:, None]).squeeze(1)
    ok = gueltig & in_maske
    da = out.get("coarse_da")                # Netz mit fester Zeiger-Zeilenzahl: wer bekam Logits?
    ueberlauf = gueltig & ~da if da is not None else torch.zeros_like(gueltig)
    if da is not None:
        ok = ok & da
    m2 = maske | ~ok[:, None]              # Zeilen ohne Kachel-Verlust: unmaskiert, Gewicht 0
    logits = out["coarse"].float().masked_fill(~m2, float("-inf"))
    logp = F.log_softmax(logits, 1)
    sigma = o.sigma_faktor * r_kacheln(g)
    sigma = torch.where(ok, sigma, torch.ones_like(sigma))
    if o.raum == "lset":
        # −log Σ_{c∈S} p_c, S = {c*} ∪ weitere Label-Zellen, die zum Zeitpunkt t legal sind
        ls = b["lset"]
        sm = torch.zeros_like(m2).scatter_(1, yc[:, None], True)
        sm.scatter_(1, torch.where(ls >= 0, ls, yc[:, None].expand_as(ls)), True)
        sm &= m2
        ce = -torch.logsumexp(logp.masked_fill(~sm, float("-inf")), 1)
    else:
        ziel = weiches_ziel(maske & ok[:, None], b["xy"], b["wh"], sigma, yc, o.weich_anteil)
        ce = -(ziel * logp.masked_fill(~m2, 0.0)).sum(1)
    wc = w * ok.float()
    l_c = (ce * wc).sum() / wc.sum().clamp(min=1e-6)
    logs["coarse"] = l_c
    nll = -logp.gather(1, yc[:, None]).squeeze(1)
    logs["coarse_nll"] = ((nll * wc).sum() / wc.sum().clamp(min=1e-6)).detach()
    total = total + l_c

    # ---------------- Wert-Kopf (ungewichtet wie im alten Trainer)
    lv = F.mse_loss(out["value"].float(), b["win"])
    logs["value"] = lv
    total = total + o.value_w * lv

    info = {"maske": maske, "ok": ok, "gueltig": gueltig, "in_maske": in_maske, "ueberlauf": ueberlauf,
            "logits_maskiert": logits, "w": w, "wt": wt, "adv": adv}
    return total, logs, info
