/**
 * Hintergrunddienst (MV3 service worker). Er ist keine Seite, darum gilt für ihn die
 * Mischinhalt-Regel nicht — er darf den Inferenz-Server über http erreichen.
 *
 * Er tut genau eine Sache: die übergebene Anfrage stellen und die Antwort zurückgeben.
 * Erlaubt ist allein der eingestellte Inferenz-Server; alles andere wird abgelehnt.
 * Er fasst openfront.io nicht an.
 */
const c: any = (globalThis as any).chrome;

/** Nur hierhin darf die Brücke. Ports sind frei, Gegenstelle nicht. */
const ERLAUBTE_GEGENSTELLEN = [/^100\.120\.102\.64$/, /^127\.0\.0\.1$/, /^localhost$/];

function erlaubt(url: string): boolean {
  try {
    const u = new URL(url);
    if (u.protocol !== "http:" && u.protocol !== "https:") return false;
    return ERLAUBTE_GEGENSTELLEN.some((r) => r.test(u.hostname));
  } catch {
    return false;
  }
}

c?.runtime?.onMessage?.addListener((m: any, _absender: any, antworte: (a: any) => void) => {
  if (m?.art !== "hole") return false;
  if (!erlaubt(m.url)) {
    antworte({ fehler: "Gegenstelle nicht erlaubt: " + String(m.url).slice(0, 120) });
    return true;
  }
  fetch(m.url, {
    method: m.methode ?? "GET",
    headers: m.kopf ?? {},
    body: m.koerper,
  })
    .then(async (r) => antworte({ status: r.status, text: await r.text() }))
    .catch((e) => antworte({ fehler: String(e?.message ?? e).slice(0, 200) }));
  return true; // asynchron
});
