#!/usr/bin/env node
/**
 * Liefert Arena-Aufnahmen (GameRecords aus `arena.ts --aufnahme`) so aus, wie der
 * Client im Dev-Build ein archiviertes Spiel holt:
 *
 *   JoinLobbyModal.checkArchivedGame → GET ${getApiBase()}/game/<id>
 *   getApiBase() auf localhost       → localStorage.apiHost ?? http://localhost:8787
 *
 * Der Dev-Build hat gitCommit "DEV" und prüft die Version darum nicht. Am Client ändert
 * sich nichts; es braucht nur vite (9000) und den Spielserver (3000/3001, fragt vorher
 * `/w0/api/game/<id>/exists`), also `npm run dev` im Client.
 *
 *   node viewer/arena/aufnahme_server.mjs data/aufnahmen      # Port 8787, nur lokal
 *   open http://localhost:9000/game/<id>
 *
 * Unter http://localhost:8787/ steht die Liste der Aufnahmen mit Links.
 */
import fs from "node:fs";
import http from "node:http";
import path from "node:path";

const ordner = path.resolve(process.argv[2] ?? "data/aufnahmen");
const port = Number(process.env.PORT ?? 8787);
const viewer = process.env.VIEWER ?? "http://localhost:9000";

function antwort(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }
  const url = new URL(req.url ?? "/", "http://lokal");
  const m = url.pathname.match(/^\/game\/([A-Za-z0-9]{8})$/);
  if (m) {
    const datei = path.join(ordner, `${m[1]}.json`);
    if (fs.existsSync(datei)) {
      res.writeHead(200, { "Content-Type": "application/json" });
      fs.createReadStream(datei).pipe(res);
      console.log(`[aufnahme] ${m[1]} ausgeliefert`);
      return;
    }
  }
  if (url.pathname === "/") {
    const ids = fs.existsSync(ordner)
      ? fs.readdirSync(ordner).filter((f) => /^[A-Za-z0-9]{8}\.json$/.test(f)).map((f) => f.slice(0, 8))
      : [];
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(`<h1>Arena-Aufnahmen</h1><ul>${ids.map((id) =>
      `<li><a href="${viewer}/game/${id}">${id}</a></li>`).join("")}</ul>`);
    return;
  }
  // Alles andere, was der Client auf localhost:8787 sucht (Konto, Kosmetik …), gibt es hier nicht.
  res.writeHead(404, { "Content-Type": "application/json" });
  res.end('{"error":"not found"}');
}

// localhost kann im Browser ::1 oder 127.0.0.1 sein; beide, aber nie nach aussen.
for (const host of ["127.0.0.1", "::1"]) {
  http.createServer(antwort).listen(port, host)
    .on("error", (e) => console.error(`[aufnahme] ${host}:${port}: ${e.message}`));
}
console.log(`[aufnahme] ${ordner} auf http://localhost:${port}/ — Replay: ${viewer}/game/<id>`);
