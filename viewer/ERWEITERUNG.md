# Machbarkeitstest: die KI als Browser-Erweiterung auf der echten Seite

Der eigene Client ist an Turnstile gescheitert (Fehler 110200: die Domain gehört nicht
zum Schlüssel — localhost ist beim ausgelieferten Schlüssel nicht freigegeben). Daran
wird nichts gedreht. Der andere Weg: die echte Seite liefert ihren eigenen Client, der
Nutzer löst das Captcha wie immer selbst, und eine Erweiterung hängt nur die KI an.

## Die Frage

Kann eine Erweiterung auf openfront.io (1) den Zugstrom mitlesen und (2) eigene
Absichten über dieselbe Verbindung senden, ohne an Schutzmechanismen zu rühren?

## Was gemessen ist (12.09.2026, alles ohne Browser)

**Mitlesen: ja.** Eine Erweiterung kann sich im `MAIN`-Kontext bei `document_start` vor
`window.WebSocket` legen — das läuft, bevor das Bundle der Seite seinen Socket aufmacht.
Der Rahmeninhalt ist kein Rätsel: `src/core/ZbinWire.ts` liegt offen, und mit der
Spielerliste aus dem Startbrief lässt sich alles Weitere entschlüsseln.

- `aitest/wireprobe.ts`: 600 echte Züge kodiert und wieder gelesen, **0 Unterschiede**.
- `aitest/extprobe.ts`: das **gebündelte Skript der Erweiterung** in einem nachgebauten
  Fenster — es übernimmt den Socket, erkennt die Partie (`gameID`, Karte, Modus,
  Spielerzahl), liest 200 Züge, **kein Rahmen unverstanden**, zählt die Absichten nach Art.

**Nachrichtenfluss.** Ein Socket je Partie (`wss://openfront.io/w<N>`), dazu einer für die
Lobby-Liste. Ein Startbrief (144 Byte in der Probe: Spielerliste, Karte, Konfiguration),
danach **10 Zug-Nachrichten je Sekunde**, im Median **5 Byte**, Spitze 47 Byte. Für zehn
Minuten Partie sind das unter 100 KB. Der Strom ist winzig, weil er nur die Absichten der
Spieler trägt — der Weltzustand entsteht auf jedem Client neu.

**Eigene Engine mitlaufen lassen: passt.** Genau dafür ist der Strom gemacht.
- Nachspielen: **2,4 ms je Tick** (600 Ticks in 1,4 s) gegen ein Budget von 100 ms.
- Beobachtung rechnen (18 Kartenkanäle + Zellfakten + Vektor): **89 ms**, alle 32 Ticks
  einmal. Gehört in einen Worker, sonst ruckelt das Bild.
- Speicher: 84 MB Heap, 377 MB RSS inklusive Karte.
- Bundle: Engine + Wire + Kodierung + Zielwahl = **782 KB, 197 KB gepackt**. Für eine
  Erweiterung nichts. Die Kartendatei (~4 MB) holt sie vom selben CDN wie die Seite.

**Senden: technisch derselbe Handgriff, aber in diesem Test nicht gemacht.** Die Absicht
ginge über denselben Socket (`encodeClientMessage` mit der Tabelle aus dem Startbrief) —
dieselbe Funktion und dieselbe Verbindung, die der Client für jeden Klick des Nutzers
benutzt. Bewiesen ist das hier **nicht**: die Erweiterung überschreibt `send` nicht, sie
zählt nur. Die Probe hat keine einzige Absicht erzeugt.

## Was noch offen ist

- Der Hook im echten Browser ist ungeprüft. Das geht nur mit geladener Erweiterung.
- Die eigene Engine braucht die **eigene clientID** (steht als `myClientID` im Startbrief)
  und muss die Startphase mitbekommen — beides liegt im Strom, ist aber nicht gemessen.
- Aufwand für den vollen Bau, geschätzt: Worker mit Engine und Beobachtung, Anbindung an
  den Inferenz-Server, Zielwahl gegen den eigenen Zustand, Einblendung — das meiste ist
  schon geschrieben (`ai-live-v33.patch`) und muss umgehängt werden. Ein halber bis ein
  ganzer Tag, plus Prüfen.

## Laden

Ordner `ki-beobachter/` (auf arch: `~/ki-beobachter`). Drei Schritte stehen in dessen
README. Die Erweiterung verlangt **keine Berechtigungen**: kein `host_permissions`, kein
`webRequest`, kein Hintergrunddienst, nur ein Skript auf openfront.io, das mitliest.
