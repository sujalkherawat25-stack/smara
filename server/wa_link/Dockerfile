# Smara WhatsApp link sidecar (Baileys). Pinned by package-lock.json; no state is written to disk.
FROM node:22-alpine AS deps
WORKDIR /app
# libsignal (a Baileys dependency) is fetched from git, so git is needed for this stage only.
RUN apk add --no-cache git
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=deps /app/node_modules ./node_modules
COPY package.json ./
COPY src ./src
USER node
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -qO- http://127.0.0.1:8090/healthz || exit 1
CMD ["node", "src/index.js"]
