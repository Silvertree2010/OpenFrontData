"""Mikrotest für train.Schritt ohne Sync: fused AdamW mit found_inf überspringt das
Update, wenn Verlust oder Gradient nicht endlich ist, und aktualisiert sonst.
Läuft auf CUDA, sonst auf der CPU mit erzwungenem sync-freiem Pfad.

    python trainer/tests/schritttest.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch  # noqa: E402

import train as TR  # noqa: E402

dev = "cuda" if torch.cuda.is_available() else "cpu"
fehler = []


def pruefe(name, bed, info=""):
    print(f"{'ok  ' if bed else 'FEHL'} {name} {info}")
    if not bed:
        fehler.append(name)


class Mini:
    name = "mini"

    def __init__(self):
        self.modul = torch.nn.Linear(4, 1).to(dev)

    def vorwaerts(self, g):
        return self.modul(g["x"])


class VL:
    """Ersetzt verlust.berechne: der Test gilt dem Optimierer-Pfad."""
    @staticmethod
    def berechne(out, g, vopt):
        return (out.float() * g["s"]).mean(), {}, {}


TR.V = VL
ad = Mini()
opt = torch.optim.AdamW(ad.modul.parameters(), lr=1e-2, fused=True)
st = TR.Schritt(ad, opt, None, 0.0, dev, ohne_sync=True)
x = torch.randn(8, 4, device=dev)
w0 = ad.modul.weight.detach().clone()
st({"x": x, "s": torch.tensor(float("nan"), device=dev)}, 1e-2)
pruefe(f"NaN-Verlust: Gewichte unverändert ({dev})", torch.equal(w0, ad.modul.weight.detach()))
stp = float(opt.state[ad.modul.weight]["step"]) if ad.modul.weight in opt.state else 0.0
pruefe("NaN-Verlust: Schrittzähler nicht erhöht", stp == 0.0, f"step={stp}")
st({"x": x, "s": torch.tensor(1.0, device=dev)}, 1e-2)
pruefe("endlicher Verlust: Gewichte geändert", not torch.equal(w0, ad.modul.weight.detach()))
stp = float(opt.state[ad.modul.weight]["step"])
pruefe("endlicher Verlust: Schrittzähler 1", stp == 1.0, f"step={stp}")
s, n = st.lies()
pruefe("lies(): ein übersprungener Schritt, Summe endlich", n == 1.0 and s == s, f"summe={s:.4f}, n={n}")
print("\n" + ("ALLES OK" if not fehler else f"{len(fehler)} FEHLER: {fehler}"))
sys.exit(1 if fehler else 0)
