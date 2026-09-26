# The dashboard: a static bundle behind Caddy.
#
# WHY CADDY AND NOT nginx. `check-config` refuses to call a setup production
# ready while `AI_NAPLES_COOKIE_SECURE=0`, and that flag cannot be turned on
# without real HTTPS — the browser would refuse to send the cookie back and
# every sign-in would look broken. Caddy obtains and renews a Let's Encrypt
# certificate on its own, which turns the hardest prerequisite into one line of
# configuration. nginx would need certbot, a renewal timer and a reload hook.

FROM node:22-slim AS builder

WORKDIR /build

# Lockfile first: `npm ci` installs exactly what is pinned and fails if
# package.json and the lockfile disagree, which `npm install` would quietly
# paper over.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------

FROM caddy:2-alpine AS runtime

COPY --from=builder /build/dist /srv
COPY docker/web/Caddyfile /etc/caddy/Caddyfile

EXPOSE 80 443
