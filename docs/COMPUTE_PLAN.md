# Rechenleistung mieten — Vorausplanung (2026-09-07)

## Der Engpass ist CPU, nicht GPU
Unsere Simulation läuft auf der CPU (Node/TS-Engine). Self-Play will viele
Partien parallel → viele Kerne. Die GPU macht nur Inferenz + Lernen und ist mit
unserem winzigen Netz (5–15 Mio Param.) unterfordert. **Also: günstige Kerne
mieten, keine teuren Grafikkarten.**

## Preise (echt, Sep 2026, Hetzner, netto)
| Instanz | vCPU | €/h | €/Monat | burst-tauglich? |
|---|---|---|---|---|
| CPX62 (shared EPYC) | 16 | 0.208 | 130 | ja, stündlich |
| CCX53 (dediziert)   | 32 | 0.855 | 533 | ja, stündlich |
| CCX63 (dediziert)   | 48 | 1.368 | 853 | ja, stündlich |
| **AX102 dediziert** | 16C/32T Ryzen 7950X3D | (0.163) | **119** | nur monatlich, jederzeit kündbar |

Andere: Vast.ai/RunPod sind GPU-fokussiert (4090 ~$0.34/h, A100 ~$0.55/h) — gut
falls wir mal eine Cloud-GPU als Lerner wollen, aber für CPU nicht ihre Stärke.
AWS/GCP-Spot ~$0.7–1.2/h für 64 vCPU, aber komplexer und unterbrechbar.
**Hetzner gewinnt klar** — und steht in Finnland, nah an uns (niedrige Latenz).

## Kostenmodell (an gemessenem Durchsatz verankert)
Messung: ~3000 Ticks/s/Kern reine Sim → mit Policy im Loop konservativ ~1000 →
~900 Partien/Kern-h lokal, ~450/vCPU-h in der Cloud (halbe Kernleistung).

| Instanz | Partien/h | € pro 1 Mio Partien |
|---|---|---|
| CPX62 | ~7.200 | €29 |
| CCX53 | ~14.400 | €59 |
| CCX63 | ~21.600 | €63 |
| **AX102 (Monat)** | ~17.600 | **€9** |

Bursts: CCX53 3 Tage → ~1,0 Mio Partien (~€62). AX102 1 Woche → ~3 Mio (~€27).
AX102 1 Monat → ~12,7 Mio (~€117).

## Die ehrliche Unbekannte
Wie viele Self-Play-Partien bis „stark"? Für dieses Spiel gibt es keine saubere
Referenz. Deshalb **kein Vorab-Zielwert**, sondern: als Regler behandeln. Wir
messen die Elo-über-Partien-Kurve und kaufen Durchsatz in Blöcken, bis die Kurve
abflacht oder das Budget die Grenze zieht. Erste sichtbare RL-Verbesserung
vermutlich ~0,5–2 Mio Partien (ein paar Tage, €20–120).

## Empfohlene Haltung
1. **Bis inkl. Behavior Cloning: nur lokal, 0 €.** 5080 + 7800X3D reichen.
2. **Erster Self-Play-Burst:** eine Hetzner-Box, Lerner bleibt lokal auf der 5080
   (Miet-Box erzeugt nur Erfahrung, schickt sie zum Lerner).
   - Zum Testen/Antasten: **CCX53 stündlich**, 2–3 Tage, ~€60–90, danach wieder aus.
   - Wenn wir wochenlang iterieren: **ein AX102 für einen Monat (€119)** — mit
     Abstand bestes Preis-Leistungs-Verhältnis, praktisch ein zweiter 7950X3D 24/7.
3. **Skalieren später:** mehr Boxen dazu, aber erst wenn die Elo-Kurve zeigt,
   dass mehr Partien noch etwas bringen.

## Fazit
Es ist **im Budget.** Ein sinnvoller erster Burst kostet €60–120. „Wie weit wir
pushen" wird ein Regler mit echten Daten, keine Wette jetzt. Vorbedingung ist
nicht Geld, sondern eine verifizierte Pipeline — die bauen wir lokal (gratis)
bis Behavior Cloning läuft, DANN mieten.
