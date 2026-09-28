# syntax=docker/dockerfile:1.7
################################################################################
# Zusatzlauf — Container-Rezept, gebaut nach demselben Muster wie
# materializer/docker/Dockerfile: ein Klon, drei Worktrees, esbuild-Buendel.
#
#   docker build -f zusatz/docker/Dockerfile -t of-zusatz:2 .
#   (Kontext ist die Repo-Wurzel; gebraucht werden nur zusatz/ und tsconfig.json)
#
# Warum Buendel statt `npx tsx`: tsx loest Symlinks auf und wendet dann
# useDefineForClassFields=false aus der tsconfig nicht mehr an — die Engine
# stirbt im ersten Tick nach der Spawnphase. esbuild bekommt die tsconfig
# ausdruecklich mit (--tsconfig) und erzeugt eine einzelne .mjs, die ohne
# node_modules laeuft. Das Laufzeitbild braucht deshalb weder Engine-Quellen
# noch node_modules, nur die Kartendaten.
#
# Einhaengen:
#   /in     Records (nur lesen), Pfade wie in der Auftragsdatei
#   /pool   Metadateien, <shard>/<gid>.meta.zst (nur lesen)
#   /out    Ausgabe: <gid>.zusatz.zst
#   /auftrag.tsv  Auftragsdatei dieses Hosts (zusatz/verteilen.py)
# Umgebung: JOBS (parallele Partien), EXTRA (weitere Argumente an den Treiber)
################################################################################

ARG NODE_IMAGE=node:24-bookworm-slim
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
COPY --from=repo /app/w /app/w

# 88 und 8b teilen den package-lock (ENGINE_FACTS §9) — ein npm ci, dann Symlink.
# Entschieden wird nach sha256, nicht nach Dateinamen.
RUN cd "/app/w/${SHA8_A}/vendor/openfront" && npm ci --no-audit --no-fund \
 && HASH_A=$(sha256sum package-lock.json | cut -d' ' -f1) \
 && HASH_B=$(sha256sum "/app/w/${SHA8_B}/vendor/openfront/package-lock.json" | cut -d' ' -f1) \
 && if [ "$HASH_A" = "$HASH_B" ]; then \
      ln -s "../../../${SHA8_A}/vendor/openfront/node_modules" "/app/w/${SHA8_B}/vendor/openfront/node_modules"; \
    else \
      cd "/app/w/${SHA8_B}/vendor/openfront" && npm ci --no-audit --no-fund; \
    fi
RUN cd "/app/w/${SHA8_C}/vendor/openfront" && npm ci --no-audit --no-fund

# zusatz/src muss IM Baum liegen: die Importe zeigen relativ auf ../../vendor/openfront.
COPY zusatz/src/ /tmp/zusatz-src/
COPY tsconfig.json /tmp/tsconfig.json
RUN for sha8 in "${SHA8_A}" "${SHA8_B}" "${SHA8_C}"; do \
      mkdir -p "/app/w/${sha8}/zusatz/src" \
      && cp -r /tmp/zusatz-src/. "/app/w/${sha8}/zusatz/src/" \
      && cp /tmp/tsconfig.json "/app/w/${sha8}/tsconfig.json"; \
    done

# useDefineForClassFields=false ist Pflicht und kommt aus der tsconfig.
RUN for sha8 in "${SHA8_A}" "${SHA8_B}" "${SHA8_C}"; do \
      tree="/app/w/${sha8}"; \
      mkdir -p "${tree}/dist"; \
      "${tree}/vendor/openfront/node_modules/.bin/esbuild" "${tree}/zusatz/src/lauf.ts" \
        --bundle --platform=node --format=esm --target=node24 \
        --tsconfig="${tree}/tsconfig.json" \
        --outfile="${tree}/dist/zusatz.mjs" || exit 1; \
    done

########################## Stufe 3: Laufzeit ###################################
FROM ${NODE_IMAGE} AS runtime
ARG SHA8_A
ARG SHA8_B
ARG SHA8_C
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /app/w/${SHA8_A}/dist /app/w/${SHA8_A}/dist
COPY --from=build /app/w/${SHA8_A}/vendor/openfront/resources/maps /app/w/${SHA8_A}/vendor/openfront/resources/maps
COPY --from=build /app/w/${SHA8_B}/dist /app/w/${SHA8_B}/dist
COPY --from=build /app/w/${SHA8_B}/vendor/openfront/resources/maps /app/w/${SHA8_B}/vendor/openfront/resources/maps
COPY --from=build /app/w/${SHA8_C}/dist /app/w/${SHA8_C}/dist
COPY --from=build /app/w/${SHA8_C}/vendor/openfront/resources/maps /app/w/${SHA8_C}/vendor/openfront/resources/maps
COPY zusatz/treiber.py /app/treiber.py
RUN mkdir -p /in /pool /out
ENV JOBS=4
ENTRYPOINT ["/bin/sh", "-c", "exec python3 /app/treiber.py --auftrag /auftrag.tsv --in /in \
--pool /pool --baeume /app/w --aus /out --jobs ${JOBS} --nice 10 ${EXTRA}"]
