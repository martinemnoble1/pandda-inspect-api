# Cloud deployment opt-ins

This API's principal delivery mode is the **standalone Electron desktop app**
(loopback, no auth, local files). Everything here is **opt-in via environment
variables** and **off by default**: with none of these set, `INSTALLED_APPS`,
`MIDDLEWARE`, `REST_FRAMEWORK`, the migration set, and artifact resolution are
exactly the desktop app's. The cloud features sit entirely behind these flags so
nothing about them can change — or break — the desktop flow.

These light up the **R0 / R2 / R3 / R6** items in
[MATERIA_INTEGRATION.md](MATERIA_INTEGRATION.md) for hosting inside Materia /
CCP4i2 (which runs Django 4.2 — hence this API now pins to the 4.2 LTS line; see
[requirements.txt](../requirements.txt)).

## Install the cloud extras

The auth and object-storage features need extra packages, kept out of the base
install:

```bash
pip install -r requirements.txt -r requirements-cloud.txt
```

That adds [`ccp4i2-api`](https://pypi.org/project/ccp4i2-api/) (the shared
CCP4i2 auth contract), `azure-storage-blob`, and `packaging`.

## 1. Auth — `PANDDA_AUTH_BACKEND`

| value          | effect                                                            |
| -------------- | ----------------------------------------------------------------- |
| *(unset)* / other | **No auth.** Nothing wired. The desktop default.               |
| `ccp4i2`       | Wire the CCP4i2 auth contract (needs `ccp4i2-api`).               |

With `ccp4i2`, **exactly one** auth middleware is selected by deployment shape
(mirroring CCP4i2's own settings order) and `django.contrib.auth` is added to
`INSTALLED_APPS` (its tables need a `migrate`):

| env present                       | middleware                       | identity                     |
| --------------------------------- | -------------------------------- | ---------------------------- |
| `CCP4I2_REQUIRE_AUTH=true`        | `AzureADAuthMiddleware` (cloud)  | Azure AD JWT (`sub`/`oid`)   |
| `CCP4I2_LOCAL_SESSION_TOKEN=…`    | `LocalSessionAuthMiddleware`     | OS user (desktop loopback)   |
| neither, `DEBUG=True`             | `DevAdminMiddleware`             | a dev superuser              |
| neither, `DEBUG=False`            | *(none)* → `AnonymousUser`       | —                            |

**No global `IsAuthenticated` is ever set.** Enforcement, when wanted, comes
from the active middleware itself (each returns its own 401). This is deliberate:
a blanket DRF permission would 401 the existing tokenless desktop client, so we
never add one here.

`LocalSessionAuthMiddleware` is the desktop **loopback dial-in** to the CCP4i2
contract: Electron mints a per-launch token, injects `CCP4I2_LOCAL_SESSION_TOKEN`
(+ `CCP4I2_LOCAL_USER_EMAIL`) when it spawns the backend, and the renderer sends
`Authorization: Bearer …`. The backend is ready for it; wiring the Electron +
client side is a separate, opt-in change (the default desktop build sets none of
this and is unaffected).

### Verify the dependency fit

Before auth is load-bearing, confirm `ccp4i2-api` fits the running
Django / DRF / Python:

```bash
python manage.py check_ccp4i2
```

It reads the installed package's declared version ranges and PASS/FAILs them
against what's actually importable (non-zero exit on a real mismatch — CI can
gate on it). If `ccp4i2-api` isn't installed it says so and exits 0 (opt-in).

### Identity on decisions

When auth is on and a request is authenticated, recording a decision stamps the
curator from the token: `inspected_by` = email, `inspected_by_oid` = the AAD
`oid` claim (falling back to `sub`). With auth off, the client-supplied
`inspected_by` stands and `inspected_by_oid` stays null — so pre-auth and
desktop rows are untouched (matches §12 reconciliation). The `oid` column is
nullable; no data migration is needed for existing rows.

### SPA sign-in (client bearer)

The backend middleware validates bearers; the **SPA acquires one** via MSAL and
attaches it to every call. This is **build-time opt-in**: the client build sets
`VITE_AAD_CLIENT_ID` + `VITE_AAD_TENANT_ID` (non-secret public IDs — both as
Docker `--build-arg`s). Both set ⇒ the SPA runs an AAD redirect login on load,
acquires the **GUID-form** scope `<client-id>/.default`, and sends
`Authorization: Bearer …` on API calls; **both unset ⇒ no MSAL, no header — the
desktop/dev flow is identical** (MSAL is a dynamic chunk that's never loaded).

> **Scope shape (AADSTS90009):** this deploy uses ONE AAD app for both the SPA
> client and the API audience, so the SPA requests a token for *itself*. AAD
> only allows that with the GUID-form scope (`<client-id>/.default`), not the
> URI form (`api://<client-id>/.default`). The token's `aud` is identical either
> way. Split into two app regs only if you later need delegated
> Graph/on-behalf-of flows — then switch to `api://<api-client-id>/…`.

- **Register the redirect URI** on the AAD app: `<origin><VITE_BASE>/`
  (e.g. `https://<materia-host>/reinspect/`).
- **Artifact bytes** (maps/coords) are fetched by Moorhen's own internal code,
  where no header can be set — so those URLs carry the token as
  `?access_token=<token>` (the middleware's `extract_token` accepts this for
  downloads). Don't strip that query param at the proxy.
- Config is build-time (not a runtime `/config` endpoint) because a runtime
  endpoint would itself sit behind the auth middleware the SPA needs config to
  satisfy — and the IDs are public and the cloud image is already host-specific
  via `VITE_BASE`.

## 2. Storage — `PANDDA_DATA_STORE`

| value          | store                                                       |
| -------------- | ----------------------------------------------------------- |
| *(unset)* / `local` | `LocalFileStore` — the ingested tree (the default).    |
| `azure`        | `AzureBlobStore` — one container, `<project>/<relpath>` keys |

`AzureBlobStore` (in [storage.py](../inspect_api/storage.py)) implements the same
`DataStore` seam, so the download view is unchanged. It needs:

- `AZURE_STORAGE_CONNECTION_STRING`
- `AZURE_STORAGE_CONTAINER`
- `PANDDA_BLOB_CACHE` (optional; where `local_path` materialises blobs for tools
  that need a real file — defaults to a temp dir)

This is the **READ path only**. The refinement RUN path
(`jobservice._resolve_path`) still resolves locally — staging blob inputs for
servalcat/refmac is deferred (gated by Q2 in MATERIA_INTEGRATION.md).

### Test it against Azurite (no real Azure)

[Azurite](https://github.com/Azure/Azurite) is the local Blob emulator:

```bash
docker run -d -p 10000:10000 mcr.microsoft.com/azure-storage/azurite \
    azurite-blob --blobHost 0.0.0.0 --skipApiVersionCheck

export AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
export AZURE_STORAGE_CONTAINER=reinspect-test

python manage.py test inspect_api.tests.test_storage_azure
```

`--skipApiVersionCheck` lets Azurite accept the (newer) API version recent
`azure-storage-blob` SDKs negotiate. The test skips itself cleanly when the env
vars / SDK aren't present, so the default `manage.py test` run is unaffected.

## 3. Container deployment (Azure Container Apps / Materia host)

The repo ships a [Dockerfile](../Dockerfile): stage 1 builds the React/Moorhen
client; stage 2 installs `requirements.txt` + `requirements-cloud.txt` and runs
the **same** waitress server the desktop app uses
([packaging/server_main.py](../packaging/server_main.py)) — bound to
`0.0.0.0` and pointed at a cloud DB. Build/push to ACR, point the Container App
at the image.

### Health probe

`GET /healthz` → `200 {"status":"ok"}` (503 if the DB is unreachable). Plain,
auth-exempt (one of the paths the ccp4i2 auth middleware bypasses), no `/api`
prefix — wire it as both the **liveness and readiness** probe. The same view is
also at `/api/v1/health/` for in-app clients.

### Migration strategy

`server_main.py` runs `migrate` on start (idempotent), so a **single-replica**
Container App self-migrates on boot — simplest for the demo. For **multiple
replicas**, run migrations as a one-shot job/init-container *before* scaling the
app (concurrent `migrate` from N replicas is unsafe); the app containers then
boot against an already-migrated DB (the idempotent re-run is a no-op).

### Environment variables (definitive manifest)

**Required for any cloud deploy**
| var | purpose |
|-----|---------|
| `PANDDA_HOST` | bind address — set `0.0.0.0` in a container (default `127.0.0.1`) |
| `PANDDA_PORT` | listen port (default `8000`) |
| `DATABASE_URL` | `postgres://user:pass@host:5432/db` — the multi-tenant DB. **Unset ⇒ SQLite** at `PANDDA_DB_PATH` (fine for single-replica/demo; mount a volume) |
| `PANDDA_DATA_ROOT` / `PANDDA_JOBS_ROOT` | the mounted projects share (e.g. `/mnt/projects`) for artifacts + job/run workdirs |

**Auth (opt-in; see §1)** — runtime (backend): `PANDDA_AUTH_BACKEND=ccp4i2`,
`CCP4I2_REQUIRE_AUTH=true`, `AZURE_AD_TENANT_ID`, `AZURE_AD_CLIENT_ID`
(+ optional `ALLOWED_AZURE_AD_GROUPS`). Build-time (SPA bearer): the
`--build-arg VITE_AAD_CLIENT_ID` / `VITE_AAD_TENANT_ID` pair (same IDs).

**Storage (opt-in; see §2)** — `PANDDA_DATA_STORE=azure`, then
`AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_CONTAINER`
(+ optional `PANDDA_BLOB_CACHE`).

**Run lifecycle**
| var | purpose |
|-----|---------|
| `REINSPECT_UI_BASE_URL` | base for the `ui_url` returned by `POST /runs/` (`{base}/runs/<id>`). **Decision: path-on-Materia's-domain** — set to `https://<materia-host>/reinspect`, giving `ui_url = https://<materia-host>/reinspect/runs/<id>`. Unset ⇒ derived from request origin (fine only when Reinspect is reached directly). |
| `PANDDA_JOB_RUNNER` | `local` (default; subprocess) or `azure_batch` (the Batch runner) |

**Azure Batch** — read by `AzureBatchRunner` (`PANDDA_JOB_RUNNER=azure_batch`;
needs `azure-batch` + `azure-identity` from requirements-cloud). Names adopted
from Materia's convention: `AZURE_BATCH_ACCOUNT_ENDPOINT`,
`AZURE_BATCH_ACCOUNT_NAME`, `AZURE_BATCH_POOL_ID`, plus optional
`AZURE_BATCH_JOB_ID` (default `pandda-runs`). Pool auth via
`DefaultAzureCredential` → the Container App's managed identity (Batch
contributor). **Caveat:** the runner's lifecycle logic is unit-tested against a
mocked SDK; the SDK wire calls still need one validation run against a live
Batch account before production (Materia owns that integration test).

### Ingress — path-routing (decided)

Reinspect is served **under a path on Materia's domain** (e.g.
`/reinspect/...`), not a subdomain. Three things the proxy must honour (this is
a SPA + WASM + API, fussier than a plain API):

1. **`ui_url` has a `/runs/` segment** — set `REINSPECT_UI_BASE_URL` to the
   prefix *without* `/runs` (`…/reinspect`); the route is `…/reinspect/runs/<id>`.
2. **Asset base path (implemented).** The client is built with **`VITE_BASE`**
   so every emitted URL — assets, `index.html` refs, the inline Moorhen WASM
   loader, our API calls, and the server-emitted `download_url` — is prefixed.
   Materia's image build passes `--build-arg VITE_BASE=/reinspect`; the proxy
   then needs a **single rule**: `/reinspect/* → Reinspect, strip /reinspect`
   (matching the existing `api/proxy/ccp4i2/*` pattern) — no origin-root
   namespace claims, no `/favicon.ico` collision. The desktop build passes no
   `VITE_BASE` (base `/`), so it is byte-identical. NB pass `VITE_BASE` WITHOUT
   a trailing slash (`/reinspect`).
3. **COOP/COEP must pass through** — Moorhen's WASM needs
   `Cross-Origin-Opener-Policy: same-origin` + `Cross-Origin-Embedder-Policy:
   require-corp` + `Cross-Origin-Resource-Policy` (the server sets all three).
   If the proxy strips them the viewer breaks. Preserve them unmodified.
4. **Moorhen assets — origin-rooted under a mount.** Moorhen builds its worker
   (`${urlPrefix}/wasm/CootWorker.js`) from `urlPrefix`, whose default
   mis-resolves under `/reinspect`. So **only when path-mounted**, `InspectPage`
   pins `urlPrefix` to the ORIGIN-ROOTED `/MoorhenAssets` — which **bypasses
   `/reinspect` and hits the host's `/MoorhenAssets` handler** (served with
   cross-origin CORP). The host must serve `/MoorhenAssets/*` (Materia does).
   `monomerLibraryPath` is pinned to the canonical GitHub monomer library (the
   host serves the JS/wasm but not the monomer `.cif`s). Desktop (BASE_URL "/")
   keeps Moorhen's defaults untouched (no viewer change).
