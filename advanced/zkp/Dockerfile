FROM node:24-bookworm-slim

ENV NODE_ENV=production \
    PROVER_HOST=0.0.0.0 \
    PROVER_PORT=8091

WORKDIR /app

COPY --chown=node:node package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
# The published circuit artifacts are the trust anchor: index.mjs verifies
# their manifest hashes before the first proof is produced, so they are
# copied verbatim rather than rebuilt inside this image.
COPY --chown=node:node artifacts ./artifacts
COPY --chown=node:node circuits ./circuits
COPY --chown=node:node scripts ./scripts
COPY --chown=node:node index.mjs ./index.mjs
COPY --chown=node:node src ./src

USER node
EXPOSE 8091
CMD ["node", "src/index.mjs"]
