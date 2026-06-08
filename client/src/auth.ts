/**
 * AAD sign-in for the cloud deployment. Entirely OPT-IN: enabled only when the
 * build carries VITE_AAD_CLIENT_ID + VITE_AAD_TENANT_ID (the cloud image). With
 * them absent — the desktop build, and every dev build — `authEnabled` is false,
 * MSAL is never imported, and no token machinery runs, so the loopback no-auth
 * flow is byte-identical.
 *
 * Token consumers:
 *  - api.ts attaches `Authorization: Bearer <token>` to every API call (fresh
 *    token per request via acquireToken()).
 *  - Artifact-byte loads use `Authorization` headers too: Moorhen's
 *    loadToCootFrom*URL accept a trailing RequestInit, so api.authHeaders()
 *    rides a header (via cachedAccessToken()). Header — not token-in-URL —
 *    because an enterprise JWT with many group claims runs ~5 KB and a
 *    token-in-URL inflates the request line past the ingress cap → HTTP 431.
 *  - The ONE exception is the report <iframe>: a browsing context sends no
 *    headers, so api.artifactUrlWithToken() appends `?access_token=`, which the
 *    ccp4i2 middleware's extract_token() accepts for downloads.
 */
import type { IPublicClientApplication } from "@azure/msal-browser";

const clientId = import.meta.env.VITE_AAD_CLIENT_ID as string | undefined;
const tenantId = import.meta.env.VITE_AAD_TENANT_ID as string | undefined;

export const authEnabled: boolean = Boolean(clientId && tenantId);

// Resource scope for Reinspect's API. GUID form (`<client-id>/.default`), NOT
// the URI form (`api://<client-id>/.default`): this deploy uses ONE AAD app for
// both the SPA client and the API audience, and AAD rejects a client requesting
// a token for itself via the URI form (AADSTS90009) — only the GUID-based app
// identifier is accepted for that self-resource case. The resulting token's
// `aud` is the same client-id, so the backend validates it identically. (If SPA
// and API are ever split into two app regs, switch to `api://<api-id>/...`.)
const SCOPES = clientId ? [`${clientId}/.default`] : [];

let msalPromise: Promise<IPublicClientApplication> | null = null;

async function getMsal(): Promise<IPublicClientApplication> {
  if (!msalPromise) {
    // Dynamic import keeps @azure/msal-browser out of the desktop bundle's
    // critical path — the chunk is only fetched when auth is enabled.
    msalPromise = import("@azure/msal-browser").then(async (msal) => {
      const instance = new msal.PublicClientApplication({
        auth: {
          clientId: clientId as string,
          authority: `https://login.microsoftonline.com/${tenantId}`,
          // Must match a redirect URI registered on the AAD app. Under a path
          // mount this is e.g. https://<host>/reinspect/.
          redirectUri: window.location.origin + import.meta.env.BASE_URL,
        },
        cache: { cacheLocation: "sessionStorage" },
      });
      await instance.initialize();
      await instance.handleRedirectPromise();
      return instance;
    });
  }
  return msalPromise;
}

// Synchronously-readable token for URL-embedded auth (Moorhen artifact loads).
// Primed at startup and refreshed on a timer; empty when auth is off.
let cachedToken = "";
export const cachedAccessToken = (): string => cachedToken;

/**
 * Ensure a signed-in account exists, triggering an interactive redirect login
 * if not. Resolves once an account is present (or returns immediately when auth
 * is disabled). Call before rendering the app.
 */
export async function ensureSignedIn(): Promise<void> {
  if (!authEnabled) return;
  const msal = await getMsal();
  if (msal.getAllAccounts().length === 0) {
    // Navigates away to the AAD login; the returned promise never resolves in
    // this page load (we come back to a fresh load with an account).
    await msal.loginRedirect({ scopes: SCOPES });
    return;
  }
  await refreshToken();
}

/** Acquire a fresh access token (silent; falls back to interactive). */
export async function acquireToken(): Promise<string | null> {
  if (!authEnabled) return null;
  const msal = await getMsal();
  const account = msal.getAllAccounts()[0];
  if (!account) {
    await msal.loginRedirect({ scopes: SCOPES });
    return null;
  }
  try {
    const res = await msal.acquireTokenSilent({ scopes: SCOPES, account });
    cachedToken = res.accessToken;
    return res.accessToken;
  } catch {
    // Silent acquisition failed (consent/expiry) — go interactive.
    await msal.acquireTokenRedirect({ scopes: SCOPES, account });
    return null;
  }
}

async function refreshToken(): Promise<void> {
  await acquireToken();
}

/** Keep the cached token fresh for URL-embedded use (tokens last ~1h). */
export function startTokenRefresh(): void {
  if (!authEnabled) return;
  setInterval(() => {
    void refreshToken();
  }, 20 * 60 * 1000);
}
