/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL for the EnergIA API. Empty string routes requests through the Vite dev/preview
   *  proxy (see vite.config.ts) instead of hitting an absolute origin -- see
   *  frontend/README.md, "Probar contra el backend real en desarrollo". */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
