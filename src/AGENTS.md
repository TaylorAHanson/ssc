# src/AGENTS.md

React 19 + TypeScript single-page app, built with Vite and styled with Tailwind.
This is the admin/self-service UI that talks to the backend at
`/api/v1`.

## Setup, run, build

- **Install:** `npm install` (from repo root). `./dev.sh` runs this automatically
  when `node_modules` is missing. Behind corporate TLS inspection (e.g. Netskope)
  `npm install` may fail cert validation — point npm at the corporate root CA
  (`npm config set cafile <ca.pem>` or `NODE_EXTRA_CA_CERTS`).
- **Dev server:** `npm run dev` (Vite on `:5173`), or `./dev.sh` to run frontend
  + backend together.
- **Typecheck + build:** `npm run build` (`tsc && vite build`) — this is the
  typecheck gate; there's no separate `typecheck` script. Output goes to
  `backend/static/` (the backend serves the built SPA in production).
- **Lint:** `npm run lint` (`eslint .`; config in `eslint.config.js`).

## How it talks to the backend

- The API base URL is injected at build time as `VITE_API_BASE_URL`
  (`vite.config.ts`): `http://localhost:8000/api/v1` in dev, `/api/v1` in prod.
  **There is no dev proxy** — the frontend calls the backend port directly, so the
  backend must be running for the UI to work.
- **All backend calls go through `src/services/api.ts`.** Add new endpoints there
  (with typed interfaces) rather than scattering `fetch` calls.

## Directory map (`src/`)

| Path | What |
|---|---|
| `pages/` | Route-level views, incl. `pages/admin/*` (Settings, EnforcementSentinel, Allowlist, DataCertification, ...). |
| `components/` | Reusable UI components. |
| `services/` | `api.ts` — the typed backend client. |
| `stores/` | Zustand state stores. |
| `hooks/`, `lib/` | Shared hooks and utilities. |
| `types/` | Shared TypeScript types. |
| `theme.ts`, `App.tsx`, `main.tsx` | Theme + app entry. |

## Conventions

- Keep new API types in sync with the backend Pydantic models; a mismatch usually
  surfaces as a `tsc` error during `npm run build`.
- Admin settings are data-driven from the backend `settings_store` schema — new
  runtime settings appear in the Settings page automatically once added there, so
  most "add a config knob" tasks need no frontend change.
- Don't hardcode the product/brand name; render it from the branding the backend
  serves.
