# The dashboard in DEVELOPMENT: the Vite server, with hot reload.
#
# Production is `Dockerfile.prod`, which builds the static bundle and serves it
# behind Caddy. Two files and not one with targets, because they share nothing
# but the base image: this one keeps the sources mounted and a node process
# running, that one throws node away and ships only the built files.
#
# The build context is THIS folder (compose.yaml), so only the frontend is sent
# to the daemon; `.dockerignore` here keeps node_modules and dist out of it.

FROM node:22-slim

# The image's node user, not root, and BEFORE `npm ci`. Vite writes its cache
# into node_modules/.vite at startup; with node_modules installed as root the
# server dies on its first write with EACCES. compose mounts an anonymous
# volume on node_modules, and Docker seeds it with this ownership.
WORKDIR /app
RUN chown node:node /app
USER node

# Lockfile first: `npm ci` installs exactly what is pinned and fails if
# package.json and the lockfile disagree.
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci

# The sources are copied for a standalone `docker run`; under compose they are
# bind-mounted over this, so edits on the host reach the server at once.
COPY --chown=node:node . .

EXPOSE 5173

# `--host` is already in vite.config.ts (`host: true`); the port is pinned there
# too, with strictPort, because 5173 is among the API's default CORS origins.
CMD ["npm", "run", "dev"]
