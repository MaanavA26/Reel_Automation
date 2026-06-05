# syntax=docker/dockerfile:1
#
# Reel Automation — frontend image (Vite/React static bundle on nginx).
#
# Multi-stage build:
#   1. builder  — node base, `npm ci` + `npm run build` (tsc -b && vite build)
#                 producing the static bundle in dist/.
#   2. runtime  — nginx serves dist/ with an SPA history fallback so deep
#                 links resolve to index.html.
#
# Build context is `frontend/`. node_modules / dist / tsbuildinfo are excluded
# via .dockerignore and rebuilt deterministically from the lockfile.

# ---------------------------------------------------------------------------
# Stage 1: builder
# ---------------------------------------------------------------------------
FROM node:20-slim AS builder

WORKDIR /app

# Install dependencies from the lockfile first for layer caching.
COPY package.json package-lock.json ./
RUN npm ci

# Copy the sources the build needs (tsc -b reads tsconfig*, vite reads
# index.html + vite.config.ts + src/).
COPY tsconfig.json tsconfig.node.json vite.config.ts index.html ./
COPY src ./src

# Optional API base override baked at build time (Vite inlines VITE_* vars).
# Defaults to the dev convention http://localhost:8000 when unset.
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# SPA-aware config: serve the built assets and fall back to index.html for
# client-side routes. Inlined to keep the change to the listed files only.
RUN printf '%s\n' \
    'server {' \
    '    listen 80;' \
    '    server_name _;' \
    '    root /usr/share/nginx/html;' \
    '    index index.html;' \
    '    location / {' \
    '        try_files $uri $uri/ /index.html;' \
    '    }' \
    '}' > /etc/nginx/conf.d/default.conf

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

# BusyBox wget (alpine) — short flags only; -O /dev/null discards the body and
# a connection/HTTP error exits non-zero -> "unhealthy".
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:80/"]

CMD ["nginx", "-g", "daemon off;"]
