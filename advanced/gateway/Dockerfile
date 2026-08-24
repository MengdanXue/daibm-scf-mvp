FROM node:24-bookworm-slim

ENV NODE_ENV=production
WORKDIR /app

COPY --chown=node:node package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --chown=node:node src ./src
COPY docker-entrypoint.sh /usr/local/bin/gateway-entrypoint
RUN chmod 0755 /usr/local/bin/gateway-entrypoint

EXPOSE 8090
ENTRYPOINT ["/usr/local/bin/gateway-entrypoint"]
CMD ["node", "src/index.js"]
