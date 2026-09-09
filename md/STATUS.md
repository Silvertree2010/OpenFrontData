# OpenFront-KI — Status (2026-09-09)

Ziel: RL-Agent, der Menschen in allen Modi schlägt. **Fokus FFA > 1v1 > 2v2.**
Detaillierter In-Flight-Übergabestand: siehe **`md/SESSION_HANDOFF.md`**.

## ⛔ Aktuell wichtigste Regel
**BC-Training ist gestaged, aber ANGEHALTEN — auf explizites „go" des Users warten.**
Nichts auto-starten. Start-Kommando + Precheck stehen im Handoff.

## Arbeitsteilung / Infrastruktur
- **arch** (`netter@100.120.102.64`, RTX 5080 + 7800X3D): Trainingsmaschine + künftiges Self-play. Projekt liegt hier unter `~/projects/openfront-ai`. Frei werktags 07-17. **WoL kaputt → manuell einschalten.**
- **apollo** (`ssh apollo` = `100.81.201.63`): Pool-Storage `/mnt/hdd/of-pool` (+`-records`), HA/WoL, wichtige Dienste — darf nicht laggen, max ~halbe Kerne.
- **node-1/2** (`100.81.143.39` / `100.81.22.70`): wichtig, nicht dauerlasten.
- **node-3l** (`lucas@100.108.191.107`): **Lucas' geliehene Box — NIE Dauerlast.**
- **Mac** (dieser Rechner): nur Steuer-/Gesprächs-Host, kein Projektcode.
- Budget: **10 CHF + die 5080.**

## Stand des Projekts
- **Human-Pool:** 12967 gerankte Partien gescraped, materialisiert zu **12217 Shards** (apollo `/mnt/hdd/of-pool`, auf arch nach `data/pool_shards` gesynct). Records werden noch fertig gezogen (Ziel ~12967).
- **BC-Netz:** 18-Kanal-Map + own/opp/config-Vektoren → CNN + SetTransformer + MLPs → Core (2×768) → 13 Heads, ~3.9M Params. Value-Head auf win∈{0,1}.
- **Training-Methode:** Advantage-Weighted BC (AWR) — Gewinner-Partien hochgewichtet (`ADV_BETA=1.5`), Outcome-Weighting statt reiner Imitation. Versionierte Snapshots (`_e<epoch>`, `_s<step>`) — nicht mehr überschreiben.
- **Checkpoints (arch):** `bc_real.pt` (altes Final, mode-collapse-anfällig), `bc_awr.pt` (stale). Neues Training schreibt `bc_big.pt`.
- **Val-Kern** plateauet ~42% (Generalisierung ≠ in-game-Skill). Letztes BC war in-game schwächer → Mode-Collapse; Worker-Sampling-Temp auf 1.3 als Mitigation.

## Nächste Schritte
1. **Auf „go" warten**, dann AWR-Full-Training auf 12217 Partien starten (`--fresh`, Snapshots) — Kommando im Handoff.
2. In-game-Skill + Val beobachten; Mode-Collapse gegenprüfen.
3. **Self-play-Phase:** 5080-zentriert (arch allein: 7800X3D CPU-Rollouts + 5080 GPU), FFA-Fokus, KEINE Fleet-Rollout-Farm. Später resumables 07-17-Windowed-Training.
4. arch-WoL (BIOS) fixen für echte Overnight-Automation.
