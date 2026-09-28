# syntax=docker/dockerfile:1.7
################################################################################
# Materialisierer v2 — Container-Rezept (DESIGN.md §7).
#
# Build-Kontext ist die Repo-Wurzel:
#
#   docker build -f materializer/docker/Dockerfile \
#     --build-arg MAT_VERSION=$(git rev-parse HEAD) \
#     -t of-mat2 .
#
# Drei Engine-Baeume (88cc95d8, 8b45be57, 115da032) aus EINEM Klon (Bare-Repo +
# Worktrees, DESIGN §1/§7). Je Baum wird materializer/src (neu) UND der alte
# Materialisierer (env/materialize.ts + env/obs.ts, Kanarien-Referenz) mit
# esbuild zu selbststaendigen .mjs-Buendeln gepackt. Die Compileroptionen
# spiegeln tsconfig.json im Repo-Wurzel (useDefineForClassFields=false ist
# Pflicht, siehe dortiger Kommentar).
#
# Laufzeit-Image enthaelt NUR die Buendel + Kartendaten — kein node_modules,
# keine Git-Historie, keine Engine-Quellen, keine Tests. esbuild bundelt alle
# reinen JS-Abhaengigkeiten (zod, nanoid, ...) direkt mit ein; die Engine hat
# keine nativen Laufzeit-Abhaengigkeiten im Simulationspfad (kein canvas-Import
# in materialize.ts, siehe ENGINE_FACTS.md).
################################################################################

ARG NODE_IMAGE=node:24-bookworm-slim
ARG MAT_VERSION=unknown

# Volle 40-Zeichen-Commits (Build-Kontext-Konstanten, DESIGN §7). Die 8-Zeichen-
# Praefixe sind die Verzeichnisnamen /app/w/<sha8>, dieselben, die dispatch.ts
# zur Laufzeit aus gitCommit ableitet.
ARG SHA_A=88cc95d8b6d74d951546da341be809bfb3cab960
ARG SHA_B=8b45be57542f5f8cce8380c4a75d816674a1dabe
ARG SHA_C=115da032763480c119f3b120c8ecd6b9a378c628
ARG SHA8_A=88cc95d8
ARG SHA8_B=8b45be57
ARG SHA8_C=115da032

########################## Stufe 1: Ein Klon, drei Baeume #####################
FROM ${NODE_IMAGE} AS repo
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ARG SHA_A
ARG SHA_B
ARG SHA_C
ARG SHA8_A
ARG SHA8_B
ARG SHA8_C

WORKDIR /src
# 115da032 ist kein Vorfahr von 88/8b (ENGINE_FACTS §0) — ein normaler Klon holt
# alle Branches und damit alle drei Commits. Danach ein Worktree je Commit statt
# drei vollen Klonen: sie teilen sich die Objektdatenbank in repo.git.
RUN git clone --quiet https://github.com/openfrontio/OpenFrontIO.git repo.git \
 && cd repo.git \
 && git worktree add --detach "/app/w/${SHA8_A}/vendor/openfront" "${SHA_A}" \
 && git worktree add --detach "/app/w/${SHA8_B}/vendor/openfront" "${SHA_B}" \
 && git worktree add --detach "/app/w/${SHA8_C}/vendor/openfront" "${SHA_C}"

########################## Stufe 2: Deps + Buendeln ############################
FROM ${NODE_IMAGE} AS build
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends python3 make g++ \
 && rm -rf /var/lib/apt/lists/*

ARG SHA8_A
ARG SHA8_B
ARG SHA8_C
ARG MAT_VERSION

COPY --from=repo /app/w /app/w

# ── Abhaengigkeiten ───────────────────────────────────────────────────────────
# 88 und 8b haben denselben package-lock.json (ENGINE_FACTS §9) -> ein `npm ci`,
# der zweite Baum bekommt einen Symlink. 115 hat einen eigenen Lock und damit
# einen eigenen `npm ci`. Die sha256-Pruefung entscheidet, nicht der Dateiname,
# damit ein kuenftiger Commit-Wechsel das nicht still falsch macht.
RUN cd "/app/w/${SHA8_A}/vendor/openfront" && npm ci --no-audit --no-fund \
 && (npm install-scripts approve esbuild 2>/dev/null || true) \
 && HASH_A=$(sha256sum package-lock.json | cut -d' ' -f1) \
 && HASH_B=$(sha256sum "/app/w/${SHA8_B}/vendor/openfront/package-lock.json" | cut -d' ' -f1) \
 && echo "lock ${SHA8_A}=${HASH_A} ${SHA8_B}=${HASH_B}" \
 && if [ "$HASH_A" = "$HASH_B" ]; then \
      ln -s "../../../${SHA8_A}/vendor/openfront/node_modules" "/app/w/${SHA8_B}/vendor/openfront/node_modules"; \
    else \
      echo "package-lock.json von ${SHA8_A} und ${SHA8_B} weichen ab — getrennter npm ci" \
      && cd "/app/w/${SHA8_B}/vendor/openfront" && npm ci --no-audit --no-fund \
      && (npm install-scripts approve esbuild 2>/dev/null || true); \
    fi

RUN cd "/app/w/${SHA8_C}/vendor/openfront" && npm ci --no-audit --no-fund \
 && (npm install-scripts approve esbuild 2>/dev/null || true)

# ── Quellen pro Baum zusammenstellen ──────────────────────────────────────────
# materializer/src/ (neu) und env/materialize.ts + env/obs.ts (alt, Kanarien-
# Referenz, siehe DESIGN.md Kopf: "NICHT anfassen") in jeden Baum kopieren.
# tsconfig.json muss relativ zum jeweiligen Baum liegen (Pfade zeigen auf
# ./vendor/openfront/*).
COPY materializer/src/ /tmp/src-neu/
COPY env/materialize.ts env/obs.ts /tmp/src-alt/
COPY tsconfig.json /tmp/tsconfig.json

RUN for sha8 in "${SHA8_A}" "${SHA8_B}" "${SHA8_C}"; do \
      mkdir -p "/app/w/${sha8}/materializer/src" "/app/w/${sha8}/env" \
      && cp -r /tmp/src-neu/. "/app/w/${sha8}/materializer/src/" \
      && cp /tmp/src-alt/materialize.ts /tmp/src-alt/obs.ts "/app/w/${sha8}/env/" \
      && cp /tmp/tsconfig.json "/app/w/${sha8}/tsconfig.json"; \
    done

# ── Buendeln mit esbuild ──────────────────────────────────────────────────────
# useDefineForClassFields=false ist PFLICHT (tsconfig.json-Kommentar, ENGINE_FACTS
# schweigt dazu, aber NationNukeBehavior stirbt sonst im ersten Tick). esbuild
# liest tsconfig ueber --tsconfig, `paths` bleibt fuer den Fall stehen, dass eine
# Quelle sie nutzt; die Importe in src/ und env/ sind ohnehin relativ.
RUN for sha8 in "${SHA8_A}" "${SHA8_B}" "${SHA8_C}"; do \
      tree="/app/w/${sha8}"; \
      esbuild="${tree}/vendor/openfront/node_modules/.bin/esbuild"; \
      mkdir -p "${tree}/dist"; \
      echo "=== esbuild ${sha8}: alter Materialisierer (Kanarien-Referenz) ==="; \
      "${esbuild}" "${tree}/env/materialize.ts" \
        --bundle --platform=node --format=esm --target=node24 \
        --tsconfig="${tree}/tsconfig.json" \
        --outfile="${tree}/dist/materialize_old.mjs"; \
      echo "=== esbuild ${sha8}: neuer Materialisierer ==="; \
      "${esbuild}" "${tree}/materializer/src/materialize.ts" \
        --bundle --platform=node --format=esm --target=node24 \
        --tsconfig="${tree}/tsconfig.json" \
        --outfile="${tree}/dist/materialize.mjs" || exit 1; \
    done

# dispatch.ts haengt an keinem Engine-Baum, nur an Node-Bordmitteln und an
# ./io (Fertig-Pruefung, eine Quelle mit dem Kern) — ein Buendel unter /app/dist.
RUN mkdir -p /app/dist \
 && "/app/w/${SHA8_A}/vendor/openfront/node_modules/.bin/esbuild" /tmp/src-neu/dispatch.ts \
      --bundle --platform=node --format=esm --target=node24 \
      --outfile=/app/dist/dispatch.mjs

# ── Kartendaten ───────────────────────────────────────────────────────────────
# 88/8b teilen resources/maps unveraendert (ENGINE_FACTS §0); 115 hat eigene
# Karten (u.a. sol/, yangtzeriver/). Fuer die Laufzeit-Stufe je Baum einmal
# ablegen (kein Symlink ueber Stage-Grenzen hinweg moeglich) — die Duplizierung
# zwischen 88 und 8b ist der Preis fuer unabhaengige Baeume; sie faellt gegen
# node_modules kaum ins Gewicht, da node_modules im Laufzeit-Image komplett
# entfaellt (s.u.).

########################## Stufe 3: Laufzeit ###################################
FROM ${NODE_IMAGE} AS runtime
ARG SHA8_A
ARG SHA8_B
ARG SHA8_C
ARG MAT_VERSION
ENV MAT_VERSION=${MAT_VERSION}

WORKDIR /app

# Nur die Buendel und die Karten — kein node_modules, keine .git, keine Quellen.
COPY --from=build /app/dist /app/dist
RUN mkdir -p /app/w
COPY --from=build /app/w/${SHA8_A}/dist /app/w/${SHA8_A}/dist
COPY --from=build /app/w/${SHA8_A}/vendor/openfront/resources/maps /app/w/${SHA8_A}/vendor/openfront/resources/maps
COPY --from=build /app/w/${SHA8_B}/dist /app/w/${SHA8_B}/dist
COPY --from=build /app/w/${SHA8_B}/vendor/openfront/resources/maps /app/w/${SHA8_B}/vendor/openfront/resources/maps
COPY --from=build /app/w/${SHA8_C}/dist /app/w/${SHA8_C}/dist
COPY --from=build /app/w/${SHA8_C}/vendor/openfront/resources/maps /app/w/${SHA8_C}/vendor/openfront/resources/maps

# /in und /out sind Einhaengepunkte (DESIGN §7); Verzeichnisse hier nur, damit
# ein Lauf ohne Mounts (z.B. Bau-Rauchtest) nicht sofort an mkdir scheitert.
RUN mkdir -p /in /out

ENTRYPOINT ["node", "/app/dist/dispatch.mjs"]
