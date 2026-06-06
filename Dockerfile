# Container image for the cloud (Materia / Azure Container Apps) binding.
#
# This is the SAME app the desktop ships, served by the SAME waitress server
# (packaging/server_main.py) — just bound to 0.0.0.0 and pointed at a cloud DB
# instead of loopback + SQLite. The desktop/Electron path is untouched: it
# never builds this image.
#
# Build:  docker build -t pandda-inspect-api .
# Run:    docker run -p 8000:8000 -e PANDDA_HOST=0.0.0.0 pandda-inspect-api

# --- stage 1: build the React/Moorhen client into client/dist --------------
FROM node:20-slim AS client
WORKDIR /client
# Public path the app is mounted under. Default "/" (origin root). A
# path-mounted deploy (Reinspect behind a proxy at /reinspect on Materia's
# domain) passes --build-arg VITE_BASE=/reinspect so every emitted URL is
# prefixed. See docs/CLOUD_DEPLOYMENT.md (Ingress).
ARG VITE_BASE=/
ENV VITE_BASE=${VITE_BASE}
# AAD config for the cloud SPA's bearer-token acquisition (non-secret public
# IDs). Set BOTH on the cloud build (--build-arg VITE_AAD_CLIENT_ID=…
# VITE_AAD_TENANT_ID=…); unset ⇒ the SPA runs with no auth (desktop/dev). The
# AAD app must register the redirect URI <origin><VITE_BASE>/. See
# docs/CLOUD_DEPLOYMENT.md (Auth).
ARG VITE_AAD_CLIENT_ID=
ARG VITE_AAD_TENANT_ID=
ENV VITE_AAD_CLIENT_ID=${VITE_AAD_CLIENT_ID}
ENV VITE_AAD_TENANT_ID=${VITE_AAD_TENANT_ID}
# Manifests + the vendored Moorhen tarball (a local file: dependency in
# package.json) must all be present before `npm ci` resolves them.
COPY client/package.json client/package-lock.json ./
COPY client/*.tgz ./
RUN npm ci
COPY client/ ./
RUN npm run build

# --- stage 2: the Python backend, serving /api + the built client ----------
FROM python:3.12-slim AS app

# Django 4.2 + Python 3.12 (matches the Materia host; see requirements.txt).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PANDDA_HOST=0.0.0.0 \
    PANDDA_PORT=8000

WORKDIR /app

# Install runtime deps first (layer-cached). Base + the cloud extras (auth,
# blob store, container server, Postgres driver). psycopg[binary] bundles libpq
# so no apt build chain is needed.
COPY requirements.txt requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-cloud.txt

# App source + the built client from stage 1.
COPY . .
COPY --from=client /client/dist ./client/dist

EXPOSE 8000

# server_main applies migrations on start (idempotent) then serves with
# waitress. For a cloud DB, set DATABASE_URL; migrations run against it. See
# docs/CLOUD_DEPLOYMENT.md for the migration-strategy note (entrypoint vs job).
# Run by PATH, not `-m packaging.server_main`: the local packaging/ dir is not
# a package and the pip `packaging` library would shadow it.
CMD ["python", "packaging/server_main.py"]
