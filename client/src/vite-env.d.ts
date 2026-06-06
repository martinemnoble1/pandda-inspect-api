/// <reference types="vite/client" />

interface ImportMetaEnv {
  // AAD config for the cloud build (both set ⇒ SPA acquires a bearer; unset ⇒
  // desktop/dev, no auth). Non-secret public identifiers; baked at build time.
  readonly VITE_AAD_CLIENT_ID?: string;
  readonly VITE_AAD_TENANT_ID?: string;
}
