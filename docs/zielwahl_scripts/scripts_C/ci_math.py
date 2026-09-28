"""Sample-size / CI math for the spatial metric (binomial, cluster design effect, paired)."""
import math
z = 1.96; zb = 0.8416
print("n for 95% half-width h (independent, DE=1 / DE=2 for ~12 samples/game, ICC~0.09):")
for p in (0.02, 0.05, 0.1, 0.2, 0.3, 0.5):
    row = []
    for h in (0.01, 0.02):
        n = z * z * p * (1 - p) / h / h
        row.append(f"h={h:.0%}: {math.ceil(n):>6} / {math.ceil(2*n):>6}")
    print(f"  p={p:.2f}  " + "   ".join(row))
print("half-width at fixed n (DE=2):")
for n in (500, 1000, 2000, 5000):
    print(f"  n={n:5d}: " + "  ".join(f"p={p:.2f}: +-{z*math.sqrt(2*p*(1-p)/n):.3f}" for p in (0.05, 0.1, 0.2, 0.4)))
print("paired comparison (same val set, two checkpoints), detect delta with 80% power, alpha=5% two-sided:")
for disc in (0.05, 0.1, 0.2):          # share of samples where the two checkpoints disagree (hit vs miss)
    for d in (0.01, 0.02, 0.05):
        n = (z + zb) ** 2 * disc / d / d
        print(f"  discordant {disc:.2f}, delta {d:.2f}: n = {math.ceil(n)}")
