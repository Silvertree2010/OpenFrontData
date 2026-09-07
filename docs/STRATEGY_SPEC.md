# Strategie-Spec — aus Ultimus_Rex (Rang 3), 200 Videos destilliert

Nicht als Regeln fürs Netz (die liest es nicht), sondern als **Design-Vorgabe**:
was das Netz SEHEN muss, was zu BELOHNEN ist, worauf zu TESTEN. Abgeleitet aus
5.635 gerankten Ratschlägen (data/ultimus_rex/highsignal.txt).

## A. Beobachtungs-Lücken (das Wichtigste — sonst kann es das nie lernen)

Priorität nach Häufigkeit/Wucht der Aussagen:

1. **Allianz-Restlaufzeit je Verbündetem.** Das meistwiederholte Thema überhaupt:
   „renew before it expires", „SAM before the alliance expiration begins",
   „this is what happens when you don't renew alliances". Wir zeigen ally ja/nein,
   aber NICHT den Timer. → neues Gegner-Feld: Ticks bis Allianz-Ablauf.
2. **Struktur-LEVEL, nicht nur Vorhandensein.** „silo level = gleichzeitige Raketen",
   „upgrade your SAMs or you get targeted", Stadt-Level → Truppen-Cap. Unsere Kanäle
   markieren nur Präsenz. → Level in die Gebäude-Kanäle kodieren (Wert = Level).
3. **Eigene Truppen / Max-Cap (S-Kurve-Position).** „population regen is an S-curve",
   „never let troops get this low — takes an hour", Optimum ~42%. Wir haben rohe
   Truppen, nicht den Cap/Ratio. → own: troops/maxTroops + Regen-Position.
4. **Wer gewinnt (Kronen-/Board-Leader-Flag) je Gegner.** „always focus on the player
   winning the game", „ally the strongest or you die". → Gegner-Feld: Stärke-Rang /
   ist-Anführer.
5. **Gegner-Nuke-Fähigkeit.** „always check people's money before you attack — they
   might have a hydrogen bomb", „ally people with silos". → Gegner-Feld: hat Silo?,
   Gold-für-Bombe-Schwelle, Bomben-Reichweite auf mich.
6. **Boote in der Luft (0–3) + Verfügbarkeit.** „don't send all three at once", „keep
   a reserve", „send short- AND long-range". Cap 3 kennen wir (Maske). → own: Anzahl
   ausgesandter Transporter + Rückkehrzeiten.
7. **SAM-Abdeckung je Kachel** (das Schachbrett-Overlay). „silo within SAM range is
   worthless", „SAM must be right next to the silo to intercept". → neuer Karten-Kanal:
   unter-SAM-Abdeckung (eigene/feindliche).
8. **Anteil des Boards, das mir gehört.** „attack speed scales with % land owned, not
   just troops". → own: Board-Anteil.

## B. Aktionsmaske — bestätigt kritisch

- **wouldNukeBreakAlliance(ziel)** als harte Maske. Der Spieler sagt es wörtlich:
  „make sure the circle isn't red" — roter Kreis = Allianzbruch = NICHT werfen. Der
  Client zeigt es live; wir müssen es genauso maskieren. Höchste Priorität.
- Atombombe nur auf Ziele mit ≥4 Städten (sonst „every atom bomb wastes money —
  cheaper for them to replace the city"). Als weiche Maske/Belohnung.

## C. Belohnungs-Teilziele (RL-Phase, früh, später weggeannealt)

- Städte FRÜH (Truppen-Cap + Regen) — „critical to get cities early".
- Bot-Kills IMMER zu Ende führen (Gold) — „finish off your bot kills or you can't buy
  structures".
- Allianz mit dem/den Stärkeren halten/erneuern (Überleben).
- Truppen über einer Deter-Schwelle halten — „keep troops high and no one attacks you".
- SAM-Abdeckung VOR Verwundbarkeitsfenstern (Allianz-Ablauf).
- Nach Bombardierung: Truppen in den Feind, nicht in neues Land — „if bombed, first
  thing throw troops at the enemy".

## D. Eval-Verhaltenstests (prüfen, was BC/RL gelernt hat)

- Erneuert es Allianzen VOR Ablauf, v.a. mit dem stärksten Nachbarn?
- Baut es Städte in den ersten Minuten?
- Vermeidet es Rot-Kreis-Nukes (kein versehentlicher Allianzbruch)?
- Macht es verlustreiche 1v1-Angriffe (Verteidiger gewinnt) — soll es NICHT?
- Snipet es Häfen? Bootet es früh (vor dem Kriegsschiff-Schwarm)?
- Fokussiert es im Endspiel den Anführer statt sinnloser Dauerkriege?
- Hält es einen Boots-Reserve? Baut es SAM bevor der Nachbar Silos hat?

## Meta-Muster (durchgängig, fürs Verständnis)
Diplomatie schlägt Kampf („learn to deescalate", „don't burn bridges"). Allianzen
sind Zeit-begrenzte Verträge, kein Dauerzustand — das Timing (Ablauf, Erneuerung,
Verrat direkt nach Ablauf = straffrei) ist der halbe Skill. Boote umgehen die
Verteidigungs-KI der Nationen (keine Defense-Posts als Antwort) — starke Farm-Taktik.
Nukes sind Positions- und Ökonomie-Werkzeuge, nicht nur Schaden.

## ⚠️ Versions-Vorbehalt (wichtig)
Die Videos stammen TEILS aus älteren Spielversionen (im Transkript: „V31", „V33",
„B32"). Konsequenz:
- **Muster/Prinzipien = robust** und version-übergreifend gültig (Allianz-Timing,
  Städte früh, Boot-Timing, Deeskalation, Anführer fokussieren, Nuke als Positions-
  werkzeug). Diese treiben die Observations-/Reward-/Eval-Punkte oben — die stehen.
- **Konkrete Zahlen = NICHT übernehmen.** Alles Zahlenhafte (z.B. „≥4 Städte für
  Atombombe", „Häfen 2 H-Bomben-Durchmesser Abstand", „50% mehr Casualties in V31")
  ist gegen UNSERE gepinnte Engine zu prüfen — Quelle der Wahrheit ist der Code bzw.
  docs/MECHANIK.md, nicht die Videos. Wo Spec und MECHANIK.md sich widersprechen,
  gilt MECHANIK.md.
- Die Observations-Features (Teil A) sind ohnehin version-neutral: sie machen ein
  Signal SICHTBAR, sie behaupten keinen Zahlenwert. Deshalb unbedenklich.

## E. Gegner lesen — Theory of Mind & menschliche Vielfalt (2026-09-07)

Ziel: die KI soll menschliche Gegner einschätzen — dreckig, ehrenhaft, dumm,
gut, fehlerhaft. Drei Ebenen:

**1. Implizit (schon vorhanden).** Rekurrenter Kern (GRU) = Gedächtnis über die
Zeit; pro Gegner sichtbar: traitor, betrayals, Allianz-Status + -Timer. Bricht
jemand ein Bündnis, reagiert die Policy. BC auf ECHTEN Menschen-Spielen erdet das
in der realen Verhaltensverteilung (Verrat/Dummheit/Fehler inklusive) — die KI
nimmt nicht an, alle spielen optimal.

**2. Gegner-Vorhersage-Kopf (Hilfsaufgabe, einzubauen).** Zusätzlicher Ausgang je
Gegner-Einbettung, der dessen NÄCHSTE Aktion vorhersagt (greift er mich an? bricht
er das Bündnis? nukt er?). Label gratis aus dem Replay (wir wissen, was der Gegner
wirklich tat). Trainiert per Hilfsverlust neben BC. Zwingt das Netz, ein echtes
Gegnermodell zu lernen → verbessert auch die Hauptpolicy. Sitzt am Gegner-
Transformer (der bettet Gegner ohnehin ein). Billig, hoher Wert.
- Konkret: opp_pred-Kopf über jede Gegner-Einbettung → {greift-mich-an,
  bricht-bündnis, wirft-nuke, expandiert-neutral} als Multi-Label übers nächste
  Fenster. Verlust nur auf beobachtbaren Gegnern.

**3. Liga-Vielfalt gegen die Self-Play-Falle (kritisch).** Reines Self-Play =
alle Gegner spielen gleich (konsistent, fast-optimal) → die KI VERLERNT den Umgang
mit chaotischen Menschen. Deshalb Pool dauerhaft durchmischen:
- BC-Klone (menschenähnlich) bleiben Dauergegner.
- Die 4 Skript-Bots (Easy…Impossible) bleiben drin — andere Spielweisen.
- Gezielte „Störer"-Agenten züchten, die dreckig spielen (Früh-Verrat, All-in-Rush,
  Bündnis-Farming), damit das Hauptnetz lernt sie zu kontern.
- Regelmäßige Eval gegen FRISCHE Menschen-Replays (Verteilung driftet mit dem Meta).

Zweite Begründung der Liga (neben Strategie-Kollaps): sie hält die menschliche
Lesbarkeit drin. NIE reines Self-Play.

## F. Identität als Signal — Clan & Reputation, nicht Text (2026-09-07)

Spielernamen tragen Signal, aber das Netz liest keinen Text. Wir operationalisieren
NUR das Verlässliche:

**Neue Gegner-Features:**
- `inClan` (0/1): hat einen Clan-Tag. Clan-Spieler sind organisiert/koordiniert und
  im Schnitt stärker (Top-Liste: LUX, UN, JR…).
- `reputation` (0..1): historischer Skill des Gegners, gekeyt auf (username, clan_tag).
  Quelle: trackerfront-Rang wenn bekannt, sonst unser selbst-Elo, sonst NEUTRAL 0.5.
  Das ist „der Name sagt was aus" — als Zahl. Reputationstabelle:
  data/trackerfront_ladder.json + data/ratings.sqlite.
- optional `sameClanAsAlly` / Koordinations-Signal.

**Bewusst NICHT gemacht:**
- Namens-Semantik per Text-Embedding ("Never Betray", "Trader"). Schwach + irreführend
  — Leute lügen im Namen. Beobachtetes Verhalten (traitor/betrayals/Angriffe) schlägt
  den deklarierten Namen IMMER.
- Roh-Identitäts-Embeddings je Spieler. Würden Individuen memorieren, generalisieren
  nicht auf Fremde. Nur aggregierte Signale (Clan, Rang-Bucket).

**Zwei harte Regeln:**
1. `reputation` ist ein PRIOR mit neutralem Fallback (0.5) für unbekannte Spieler;
   beobachtetes Verhalten aktualisiert die Einschätzung im Spiel. Das Netz darf NIE
   brechen, wenn ein Name unbekannt ist — es muss auf Verhalten zurückfallen. Weil die
   BC-Daten Bekannte UND Unbekannte mischen, lernt es genau diese Robustheit.
2. Verhalten > Reputation > Name-Text. In dieser Reihenfolge vertrauen.

**Anschluss:** reputation/inClan gehen in OpponentFeat (obs.encodeVec). Der Extractor
hat username/clan_tag pro Spieler ohnehin → Lookup in die Reputationstabelle beim
Featurizen. OPP_DIM wächst entsprechend (Trainer-Wiring).
