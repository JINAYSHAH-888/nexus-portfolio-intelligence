# ── deps ──────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ── build ─────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build && npm prune --omit=dev

# ── runtime ───────────────────────────────────────────────────────────────────
FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000
# Never run as root.
RUN addgroup -S nexus && adduser -S nexus -G nexus
COPY --from=build --chown=nexus:nexus /app/.next ./.next
COPY --from=build --chown=nexus:nexus /app/node_modules ./node_modules
COPY --from=build --chown=nexus:nexus /app/public ./public
COPY --from=build --chown=nexus:nexus /app/package.json ./package.json
USER nexus
EXPOSE 3000
CMD ["npm", "start"]
