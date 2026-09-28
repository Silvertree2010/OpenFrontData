// Liest BOOTSTRAP_CONFIG.gitCommit von openfront.io — ganz normal die Seite laden,
// nichts am Schutz drehen. Wenn Cloudflare eine Prüfung zeigt, warten wir sie ab; geht
// sie nicht von selbst durch, brechen wir ab und sagen das.
import { chromium } from "~/deckflow/node_modules/playwright-core/index.mjs";

const headless = process.argv.includes("--headless");
const browser = await chromium.launch({
  executablePath: "~/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
  headless,
});
const seite = await browser.newPage();
let ergebnis = null;
try {
  await seite.goto("https://openfront.io/", { waitUntil: "domcontentloaded", timeout: 60000 });
  for (let i = 0; i < 40; i++) {
    ergebnis = await seite.evaluate(() => {
      const b = globalThis.BOOTSTRAP_CONFIG ?? globalThis.__BOOTSTRAP_CONFIG__;
      return b ? { gitCommit: b.gitCommit, turnstileSiteKey: b.turnstileSiteKey,
                   jwtAudience: b.jwtAudience, numWorkers: b.numWorkers,
                   instanceLetter: b.instanceLetter, siteHost: b.siteHost,
                   cluster: b.cluster, titel: document.title } : null;
    }).catch(() => null);
    if (ergebnis?.gitCommit) break;
    await seite.waitForTimeout(1500);
  }
  if (!ergebnis?.gitCommit) {
    console.log(JSON.stringify({ ok: false, titel: await seite.title(),
      text: (await seite.evaluate(() => document.body?.innerText ?? "")).slice(0, 300) }));
  } else {
    console.log(JSON.stringify({ ok: true, ...ergebnis }));
  }
} finally {
  await browser.close();
}
