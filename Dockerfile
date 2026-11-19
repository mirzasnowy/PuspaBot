# syntax=docker/dockerfile:1
# Puspa multi-tenant deploy image (G13).
#
# Pattern (hermes-agent Dockerfile, rewritten for Puspa): multi-stage
# build, s6-overlay as PID 1, dashboard bound to loopback by default,
# remote access only through an SSH tunnel or an authenticated reverse
# proxy, UID/GID remap so home files stay owned by the host user, API
# server off unless its key is uncommented.
#
# Honest limits (read before building):
# - Docker containers cannot run on this dev VM (the kernel rejects
#   setns), so this file has NOT been built or run here. It is checked
#   offline by puspa/teams/deploy.py (parse + rule validation) and
#   must be validated live on a VPS: `docker build` + `docker compose
#   up` + a real login. See docs/teams.md ("Deploy").
# - The image installs the full pinned requirements.txt, including
#   torch (~2.5 GB installed). The image is large. Splitting out a
#   slim server-only requirements file is a follow-up, not done here.
# - The PWA is built in the Node stage below and copied into the
#   runtime stage, so the deployed image serves the React SPA
#   (E-05). Before E-05 the image served the Jinja templates only,
#   because the built SPA (apps/pwa/dist) was never copied in.
#   The image smoke test (tests/test_image_smoke.py) runs the
#   image, requests /app, and checks the React bundle is served;
#   it runs in the CI sandbox lane (E-01), because Docker cannot
#   run on the dev VM (no daemon socket here).
# - BuildKit is required (heredoc RUN blocks below):
#   DOCKER_BUILDKIT=1 docker build .  (or: docker buildx build .)
#
# Exposure model — read this, it is the subtle part:
# - PUSPA_DASHBOARD_HOST defaults to 127.0.0.1: that is the bind
#   address when the app runs directly on a host (see
#   puspa/pwa/app.py run(): host="127.0.0.1").
# - Inside the container the service binds 0.0.0.0 instead. Docker's
#   port publishing DNATs to the container IP, so a process bound to
#   the container's loopback would never receive the published port.
#   The exposure boundary is therefore NOT the in-container bind
#   address: it is the compose file, which publishes each port on
#   the HOST's 127.0.0.1 only (127.0.0.1:8000:8000). Nothing is
#   reachable from the LAN or the internet; remote access is only
#   via an SSH tunnel to the host (ssh -L 8000:127.0.0.1:8000) or an
#   authenticated reverse proxy in front of the host loopback.
# - PUSPA_API_KEY is intentionally NOT set anywhere in this file:
#   the API server stays off unless the key is uncommented in
#   docker-compose.yml (hermes pattern: API server dead unless the
#   key is uncommented).

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=20
ARG S6_OVERLAY_VERSION=3.2.3.2
ARG PUSPA_UID=1000
ARG PUSPA_GID=1000

# --- pwa: build the React SPA ---------------------------------------------
# Node stage (E-05): installs the PWA dependencies with npm ci and
# builds the SPA (tsc + vite) into /pwa/dist. The runtime stage
# copies that dist in below, so the deployed image serves React,
# not the Jinja fallback. The build output is gitignored (dist/),
# so a checkout without this stage would serve Jinja only.
# apps/pwa/static is copied in too: Vite bundles the PWA icon from
# it, so the build output is identical to a local `npm run build`
# (verified: same dist/index.html bytes with and without it, the
# icon link differs without it).
FROM node:${NODE_VERSION}-slim AS pwa

WORKDIR /pwa
COPY apps/pwa/package.json apps/pwa/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/pwa/tsconfig.json apps/pwa/vite.config.ts apps/pwa/vitest.config.ts apps/pwa/index.html ./
COPY apps/pwa/src ./src
COPY apps/pwa/static ./static
RUN npm run build

# --- builder: install the pinned Python dependencies into a venv -------
FROM python:${PYTHON_VERSION}-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# --- runtime: s6-overlay + app, running as the puspa user --------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG S6_OVERLAY_VERSION
ARG PUSPA_UID
ARG PUSPA_GID

# Dashboard host default: loopback (see the exposure note above).
ENV PUSPA_DASHBOARD_HOST=127.0.0.1
ENV PUSPA_DASHBOARD_PORT=8000
ENV PUSPA_HOME=/home/puspa
ENV PUSPA_SERVICE_MODE=service
ENV PATH=/opt/venv/bin:$PATH
# PUSPA_API_KEY is intentionally absent: the API server stays off
# unless the key is uncommented in docker-compose.yml.

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl xz-utils ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g ${PUSPA_GID} puspa \
    && useradd -m -u ${PUSPA_UID} -g ${PUSPA_GID} -d /home/puspa puspa

# s6-overlay v3 (noarch + arch tarballs, same URLs as the upstream
# README quickstart). /init becomes PID 1 via ENTRYPOINT below.
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz /tmp
RUN tar -C / -Jxpf /tmp/s6-overlay-noarch.tar.xz
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-x86_64.tar.xz /tmp
RUN tar -C / -Jxpf /tmp/s6-overlay-x86_64.tar.xz

# App + PWA templates/static + built SPA. The venv comes from the
# builder stage; the SPA dist comes from the pwa stage (E-05).
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY puspa /app/puspa
COPY apps/pwa/templates /app/apps/pwa/templates
COPY apps/pwa/static /app/apps/pwa/static
COPY --from=pwa /pwa/dist /app/apps/pwa/dist

# cont-init: fix home ownership (a fresh named volume is root-owned
# on first mount) so the puspa user can write its home. Runs before
# any service starts; s6-overlay never bypasses the entrypoint.
RUN <<EOF
mkdir -p /etc/cont-init.d
cat > /etc/cont-init.d/10-fix-home <<'EOS'
#!/command/with-contenv sh
mkdir -p /home/puspa
chown -R puspa:puspa /home/puspa
EOS
chmod +x /etc/cont-init.d/10-fix-home
EOF

# The one service: the Puspa app (dashboard + API in one FastAPI app;
# docker-compose.yml runs two containers of this image with different
# PUSPA_ROLE / PUSPA_DASHBOARD_PORT values for the gateway + dashboard
# topology). Binds 0.0.0.0 inside the container — see the exposure
# note at the top: the loopback boundary lives in the compose
# publish binding, not here. Drops to the puspa user via s6-setuidgid.
RUN <<EOF
mkdir -p /etc/services.d/puspa
cat > /etc/services.d/puspa/run <<'EOS'
#!/command/with-contenv sh
exec s6-setuidgid puspa /opt/venv/bin/python -m uvicorn puspa.pwa.app:create_app --factory --host 0.0.0.0 --port "$PUSPA_DASHBOARD_PORT"
EOS
chmod +x /etc/services.d/puspa/run
EOF

VOLUME /home/puspa

# Metadata only: EXPOSE never publishes a port. The compose file
# publishes 127.0.0.1:8000 (dashboard) / 127.0.0.1:8001 (gateway).
EXPOSE 8000

# Liveness probe. /manifest.json is used (not /health) because the
# PWA app has no /health route yet — it is a stable, unauthenticated
# route that proves the app answers HTTP. Adding a real /health
# route is a follow-up for the PWA owner, not for G13.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f "http://127.0.0.1:${PUSPA_DASHBOARD_PORT}/manifest.json" || exit 1

# The container itself starts as root — s6-overlay's /init must run as
# root to supervise services. The APP never runs as root: the service
# script drops to the puspa user with s6-setuidgid (see above). A
# Dockerfile USER directive here would break /init, so there is
# deliberately none; puspa/teams/deploy.py checks for s6-setuidgid
# instead.
ENTRYPOINT ["/init"]
