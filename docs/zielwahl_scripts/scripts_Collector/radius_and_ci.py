"""Collector: effective radius R = displacement at which the engine outcome overlaps >= 50 %.
Nukes: overlap of two inner discs (radius a). Warship: overlap of two patrol boxes (side 100, +-50).
Plus re-check of C's CI arithmetic."""
import math
def disc_overlap(d,a):
    if d>=2*a: return 0.0
    return (2*a*a*math.acos(d/(2*a)) - (d/2)*math.sqrt(4*a*a-d*d))/(math.pi*a*a)
def solve(f,lo,hi,t=0.5):
    for _ in range(80):
        m=(lo+hi)/2
        (lo,hi)=(m,hi) if f(m)>t else (lo,m)
    return (lo+hi)/2
for name,a in (("Atom inner",12),("Atom outer",30),("H inner",80),("MIRV warhead inner",12)):
    print(f"{name:20s} a={a:3d}: 50%-overlap at d={solve(lambda d:disc_overlap(d,a),0,2*a):5.1f}  (d/a={solve(lambda d:disc_overlap(d,a),0,2*a)/a:.3f})")
side=100
ax=solve(lambda d:(side-d)/side,0,side); dg=solve(lambda d:((side-d/math.sqrt(2))/side)**2,0,side*1.4)
print(f"Warship box +-50: 50%-overlap at d={ax:.1f} (axis) / {dg:.1f} (diagonal)")
# structures: uniform point in square cell of side c -> mean distance to centre
print(f"mean dist uniform point to centre of square side 15: {0.3826*15:.2f} (0.3826*c)")
# 2D Gauss: mass within r for sigma
for s_over_r in (1.0,0.5):
    print(f"2D Gauss sigma=R*{s_over_r}: mass within R = {1-math.exp(-0.5/(s_over_r**2)):.1%}")
# C's CI checks
z=1.96; zb=0.8416
for n,p,deff in ((2000,0.2,2),(500,0.1,2)):
    print(f"n={n} p={p} deff={deff}: +-{100*z*math.sqrt(p*(1-p)*deff/n):.2f} pp")
print(f"n for +-1pp p=0.2 deff2: {math.ceil(z*z*0.16/1e-4*2)}")
for D in (0.02,0.05):
    pd=0.10
    n=((z*math.sqrt(pd)+zb*math.sqrt(pd-D*D))**2)/(D*D)
    print(f"paired McNemar, discordant 10%, Delta={D}: n={n:.0f}")
