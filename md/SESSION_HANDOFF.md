# Session-Handoff — OpenFront-AI (Stand 2026-09-09 ~08:45)

> Vorherige Session war lang/„verrottet". Das hier ist der saubere Übergabestand.
> Ergänzt die Auto-Memories (MEMORY.md) — dort stehen die dauerhaften Fakten
> ([[game-mode-priority]], [[homelab-fleet-constraints]], [[selfplay-rig-single-machine]],
> [[bigger-training-pool-deferred]], [[obs-gaps-and-lobby-finder]]).

## ⛔ SOFORT / WICHTIGSTE REGEL
**Der User will das BC-Training NICHT automatisch gestartet — WARTE AUF SEIN EXPLIZITES „GO".**
Alles ist gestaged, aber angehalten. Nichts starten bis er es sagt.

## Aktueller Zustand (arch = 5080-Box, `netter@100.120.102.64`)
- `bc_fit.py` läuft **nicht**. `morning-train.service` = failed (von mir gestoppt, kein echter Fehler).
- Stamp `~/projects/openfront-ai/.morning_train_done = 20260909` gesetzt → **kein Auto-Restart heute** (auch bei Reboot).
- Trainingsdaten auf arch: `data/pool_shards` = **12217/12217 Shards ✅**, `data/pool_records` ≈ 1839/12967 (**rsync lief noch** — vor Training auf Vollständigkeit prüfen bzw. neu ziehen).
- Checkpoints arch `~/projects/openfront-ai/checkpoints/`: `bc_real.pt` (einziges „echtes" BC-Final, überschrieben — kein Rollback möglich), `bc_awr.pt` (abgebrochener AWR-Versuch, stale). **`bc_big.pt` existiert noch nicht** (das wird das neue Training schreiben).

## Wenn der User „go" sagt — Training starten
Vorher sicherstellen, dass Records vollständig sind:
```bash
ssh netter@100.120.102.64
cd ~/projects/openfront-ai
# Records ggf. fertig ziehen:
rsync -a netter@100.81.201.63:/mnt/hdd/of-pool-records/ data/pool_records/
find data/pool_records -name '*.json' | wc -l   # Ziel ~12967
```
Dann AWR-Full-Training (identisch zu morning-train.sh, versionierte Snapshots):
```bash
setsid env ADV_BETA=1.5 VALUE_W=1.0 .venv/bin/python env/bc_fit.py \
  --shards data/pool_shards --device cuda --epochs 3 --batch 128 --shuffle-buf 8192 \
  --ckpt checkpoints/bc_big.pt --fresh --records-dir data/pool_records \
  --log-every 100 --ckpt-every 1000 --snapshot-every 20000 >> logs/bc_big.log 2>&1 < /dev/null &
# Fortschritt: tail -f logs/bc_big.log
```
ADV_BETA=1.5 = Outcome-Weighting (Gewinner hochgewichtet); VALUE_W=1.0 = Value-Head MSE auf win∈{0,1}.
Snapshots: `_e<epoch>.pt` je Epoche + `_s<gstep>.pt` alle 20000 Steps. **Diesmal NICHT überschreiben** (Lehre aus letztem Mal — Rollback war unmöglich, weil nur bc_real.pt final übrig blieb).

## Infrastruktur-Map
| Host | Adresse | Rolle | Regel |
|---|---|---|---|
| **arch** (`archlinux`) | `netter@100.120.102.64`, LAN 192.168.1.178 | 5080 + 7800X3D — **Training & künftiges Self-play** | frei werktags 07-17 |
| **apollo** | `ssh apollo` = `netter@100.81.201.63`, LAN 192.168.1.x | Pool-Storage `/mnt/hdd/of-pool` (+`-records`), HA/WoL, wichtige Dienste | darf nicht laggen; max ~halbe Kerne |
| **node-1** | `netter@100.81.143.39` | 4 Kerne, wichtig | nicht dauerlasten |
| **node-2** | `netter@100.81.22.70` | 4 Kerne, wichtig (AdGuard-DNS) | nicht dauerlasten |
| **node-3l** | `lucas@100.108.191.107` | 4 Kerne — **LUCAS' Box, nur geliehen** | **NIE Dauerlast**, nur endliche Bursts mit Auto-Stop |
| Mac | dieser Rechner, LAN 192.168.1.169 | Steuerung, vite-Client | — |

Nie killen: Jellyfin, qBittorrent (seeden AUS lassen!), Tailscale, Home Assistant, AdGuard-DNS, Minecraft, cloudflared.
Budget: **10 CHF max + die 5080.**

## Was diese Session geschah (kurz)
- Fleet-Materialisierung des Human-Pools: 12967 Records total, davon in dieser Nacht auf **12217 Shards** gebracht (Rest ~750 fehlt durch Überlast — egal, weit über dem 12000-Gate).
- Overnight-Automation (WoL 6 Uhr + morning-train.service Boot-Launcher + Daten-Gate) war gebaut, **schlug aber fehl**: **arch wachte per WoL NICHT auf** (BIOS-WoL vermutlich aus — offen!). Training lief nicht um 6 Uhr.
- Zusätzlich: **Mac-Tailscale-TCP brach** (nur UDP-Disco lebte) → Zugang zur Flotte weg. Fix: User hat Tailscale am Mac neugestartet + arch manuell eingeschaltet.
- Danach: Flotte fleetweit abgeräumt (Lucas' Box frei), morning-train sprang beim Boot an, zog Pool → ich habe es **vor dem bc_fit-Launch gestoppt** (User will manuelles Go).

## Operative Lehren (Fehler dieser Session — nicht wiederholen)
1. **apollo NICHT mit 11-12 Workern überlasten** → Swap-Thrashing, sshd verhungert. Max ~halbe Kerne.
2. **SSH nicht dauerfeuern** (dutzende Verbindungen/Poll-Loops) — belastet + kann Zugang mit-vergiften. Sparsam pollen, `nc -z` statt ssh für reine Reachability.
3. **zsh macht KEIN Wort-Splitting** von unquoted Vars (`for x in "a b"; set -- $x` → $1=alles). Explizit iterieren oder `${=x}`.
4. `timeout` gibt's auf dem **Mac nicht** (nur `gtimeout`). ssh `ConnectTimeout` nutzen.
5. Self-match-Falle bei pgrep/pkill über ssh: Bracket-Trick (`"bc_fi[t].py"`).
6. **arch WoL funktioniert nicht** — muss noch untersucht werden (BIOS Wake-on-LAN aktivieren?). Solange: manuelles Einschalten nötig.
7. arch bootet in **SDDM (Xorg vt2 + Xwayland :1)** — fb-Blank reicht nicht für Screen-off, bräuchte DPMS via richtige xauth. User: „egal, lassen".

## Offene Aufgaben (nach „go" + Training)
- BC-Training laufen lassen, Val-Kern + in-game-Skill beobachten (letztes BC war in-game schlechter trotz ok Val → Mode-Collapse; Worker temp steht auf 1.3 als Mitigation).
- **Self-play-Phase** planen: 5080-zentriert (arch allein, 7800X3D CPU-Rollouts + 5080), FFA-Fokus, KEINE Fleet-Rollout-Farm. Später resumables 07-17-Windowed-Training bauen.
- arch-WoL fixen (BIOS) für echte Overnight-Automation.
- Cloudflare-Token `cfut_...` (aus früherer Session) war noch gültig → User sollte ihn **revoken**.
