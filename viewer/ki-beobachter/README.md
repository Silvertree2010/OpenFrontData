# OpenFront-KI als Browser-Erweiterung — Kern

Die echte Seite liefert ihren Client, der Nutzer meldet sich an und löst das Captcha wie
immer selbst. Die Erweiterung hängt nur die KI an: sie liest den Zugstrom mit, führt die
Partie im Hintergrund mit derselben Engine mit und rechnet daraus die Beobachtung für das
Modell — genau die, auf der es trainiert wurde.

## Teile

| Datei | Was |
|---|---|
| `schnittstelle.ts` | Vertrag zwischen Kern und Oberfläche. **Unveränderlich.** |
| `kern.ts` | Läuft in der Seite: Socket übernehmen, Rahmen lesen, Spiegel füttern, `sendeAbsicht` |
| `engineSpiegel.ts` | Worker: führt die Partie aus dem Zugstrom mit |
| `beobachtung.ts` | Beobachtung aus dem gespiegelten Zustand (18 Kanäle, Zellfakten, Vektoren) |
| `politik.ts`, `einblendung.ts` | gehören dem anderen Agenten |
| `bauen.sh` | Bündelt alles, Ziel `~/ki-beobachter` |

Der Kern legt sich unter `globalThis.__KI_KERN__` ab und erfüllt `Kern` aus
`schnittstelle.ts`: `beobachtung()`, `sendeAbsicht()`, `aufTick()`, `zustand()`.

## Senden

**Standard aus.** `SENDEN_AN` ist `false` und bleibt es; scharf wird nur, wer im Fenster
ausdrücklich `__KI_SENDEN__ = true` setzt. Ohne das gibt `sendeAbsicht` `false` zurück
und rührt den Socket nicht an — gemessen: null Byte (`aitest/kernladeprobe.ts`).
An Turnstile, Cloudflare und der Anmeldung wird nichts verändert.

## Bauen

```bash
cd ~/openfront-client-v33/erweiterung && ./bauen.sh     # → ~/ki-beobachter
```

Gebaut wird **im** Client-Arbeitsbaum, weil Kern und Spiegel Engine und Wire-Format von
dort holen (`v0.33.14` — der Stand, der ausgeliefert wird). Der Worker landet als
Zeichenkette in `kern.js` und startet aus einem Blob: ein Skript in der Seite kommt an
die Dateien der Erweiterung nicht heran.

## Laden (Vivaldi/Chromium)

1. `vivaldi://extensions`, **Entwicklermodus** an.
2. **Entpackte Erweiterung laden** → Ordner `~/ki-beobachter`.
3. openfront.io neu laden, Konsole mit F12.

Erwartet: `[ki-kern] aktiv …`, beim Betreten einer Partie `[ki-kern] Partie <id>,
N Spieler, eigene Kennung <id>` und `Spiegel bereit, Karte World`. Danach
`__KI_KERN__.zustand()` und `__KI_KERN__.beobachtung()`.

## Was gemessen ist (ohne Browser)

- `aitest/kernprobe.ts`: 5351 Rahmen kodiert und zurückgelesen (0 Unterschiede),
  536 Zustands-Hashes korrekt, und an 12 Proben sind **Kartenblock (291'600 Byte),
  owner, frac und legal bitgleich** zu den Trainingsblöcken des Materialisierers.
  Eigene Kennung aus dem Startbrief erkannt, Startphase erkannt (Ende Tick 302).
- `aitest/kernladeprobe.ts`: das **gebaute** `kern.js` übernimmt den Socket, erkennt die
  Partie und die eigene Kennung, reicht alle Züge an den Spiegel — und sendet ohne
  Schalter nichts.

## Mischinhalt: warum der Hintergrunddienst

openfront.io ist https, der Inferenz-Server http — ein Skript **in** der Seite darf das
nicht holen. Gewählt ist der Weg über den **Hintergrunddienst** der Erweiterung
(`dienst.ts`): für ihn gilt die Mischinhalt-Regel nicht, er holt mit `host_permissions`
und reicht die Antwort durch (`bruecke.ts` → `brueckeInhalt.ts` → `dienst.ts`). Gründe:
die Erweiterung trägt alles selbst, auf arch muss nichts eingerichtet bleiben (an der
Tailscale-Einrichtung wird ungefragt nichts gedreht), und es hängt nicht an MagicDNS oder
einem Zertifikat. Der Weg über `tailscale serve` bleibt offen: dann zeigt `inf` einfach
auf die https-Adresse, und die Brücke fällt von selbst weg (`bruecke.ts` reicht https
direkt durch). Der Dienst darf **nur** zum Inferenz-Server (Liste in `dienst.ts`),
openfront.io fasst er nicht an.

## Keine Inline-Skripte — die CSP von openfront.io

Die Seite verbietet per Content-Security-Policy eingehängte `<script>`-Blöcke. Die Brücke
hat sich früher so bei der Seite gemeldet; das lief dort nie (gesehen am 12.09.2026).
Jetzt meldet sie sich per `window.postMessage` („da"), wiederholt das kurz nach dem Start
und antwortet ausserdem auf Klopfen der Seite — beide Reihenfolgen sind damit abgedeckt.
Meldet sich nichts, kommt nach 5 s ein klarer Fehler statt stiller Stille.
Sonst nutzt nichts in der Erweiterung Inline-Skripte, `eval` oder `innerHTML`; der Worker
aus einem Blob läuft (im Browser bestätigt: „Spiegel bereit").

## Laden, drei Zeilen

1. `cd ~/openfront-client-v33/erweiterung && ./bauen.sh` (baut nach `~/ki-beobachter`).
2. `vivaldi://extensions` → **Entwicklermodus** an → **Entpackte Erweiterung laden** →
   Ordner `~/ki-beobachter`.
3. openfront.io neu laden, F12, eine öffentliche Partie betreten und den Startpunkt
   selbst setzen.

### Die zwei Befehle in der Konsole

```js
__KI_KERN__.zustand()        // { partie, tick, spieler, fehler }
__KI_POLITIK__.protokoll()   // oder kiProtokoll() — Partien, Handlungen, letzte Aktion
```

Erwartet: `partie` ist die Spiel-ID, `tick` läuft mit ~10/s hoch, `fehler` bleibt `null`.
In der Einblendung unten links müssen Tick, Gebiet und „letzte Aktion" mitlaufen;
letztere endet ohne Schalter immer auf **(nur zuschauen)**.
