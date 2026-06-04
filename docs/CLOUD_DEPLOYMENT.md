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
