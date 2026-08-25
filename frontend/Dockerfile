# --- Build stage -------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# Vite inlines env vars at build time, so the API URL is a build argument.
# Only VITE_-prefixed variables reach the bundle, and this is the only one the
# frontend reads - `test_security_secrets` asserts that.
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# --- Serve stage -------------------------------------------------------------
# The unprivileged variant: nginx runs as uid 101 with no root master process,
# and listens on 8080 because an unprivileged process cannot bind below 1024.
# The stock `nginx` image starts its master as root, which is more privilege
# than a static file server has any use for.
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime

# Rendered by the image's own entrypoint at start-up, so the Content-Security
# -Policy can name the API origin without rebuilding.
COPY default.conf.template /etc/nginx/templates/default.conf.template
ENV API_ORIGIN=http://localhost:8000

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

# Static files only; nothing here writes outside nginx's own temp directories,
# which compose mounts as tmpfs so the filesystem can be read-only.
# `127.0.0.1`, not `localhost`: inside the container that name resolves to ::1
# first, and nginx here listens on IPv4 only. The image's own
# 10-listen-on-ipv6-by-default.sh would normally add a v6 listener, but it runs
# before 20-envsubst-on-templates.sh has rendered the config, so it finds
# nothing to edit. Naming the address avoids depending on either.
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1:8080/healthz || exit 1
