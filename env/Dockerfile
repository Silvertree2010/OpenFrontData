# Materializer-Image: Node + OpenFront-Engine + unsere env-Skripte.
# Ein Image für beide Commits — Deps sind identisch, der Commit wird zur Laufzeit
# per `git checkout` gewählt (ENTRYPOINT-Wrapper). Läuft CPU-limitiert je Knoten,
# damit Dienste (z.B. Jellyfin auf apollo) nicht verhungern.
#
# Build (Kontext = Projektwurzel):  docker build -f env/Dockerfile -t of-mat .
FROM node:24-bookworm-slim

# git für den Engine-Checkout; cairo/pango-Libs falls die Engine canvas lädt.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates \
      libcairo2 libpango-1.0-0 libjpeg62-turbo libgif7 librsvg2-2 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Engine klonen + Standard-Commit + Deps (klein halten: kein COPY der 1.7 GB vendor)
RUN git clone --quiet https://github.com/openfrontio/OpenFrontIO.git vendor/openfront \
 && cd vendor/openfront \
 && git checkout -q 8b45be57542f5f8cce8380c4a75d816674a1dabe \
 && npm ci --no-audit --no-fund \
 && (npm install-scripts approve esbuild 2>/dev/null || true) \
 && (npm install-scripts approve canvas 2>/dev/null || true)

COPY env/ /app/env/
COPY tsconfig.json /app/tsconfig.json
RUN ln -s vendor/openfront/node_modules /app/node_modules

# Wrapper: waehlt Commit (COMMIT env) und materialisiert eine Liste.
#   docker run -e COMMIT=<sha> -v <records>:/in:ro -v <shards>:/out \
#     of-mat --list /in/list.txt /out
RUN printf '%s\n' '#!/bin/sh' \
  'set -e' \
  'cd /app/vendor/openfront' \
  '[ -n "$COMMIT" ] && git checkout -q "$COMMIT"' \
  'exec npx tsx /app/env/materialize.ts "$@"' > /app/run.sh && chmod +x /app/run.sh

WORKDIR /app/vendor/openfront
ENTRYPOINT ["/app/run.sh"]
