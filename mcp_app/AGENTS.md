# mcp_app/AGENTS.md

A **minimal, standalone MCP server** deployed as its *own* Databricks App —
independent of the main backend. It exists to validate the Databricks
App-hosted-MCP discovery + hosting pattern (AI Gateway → MCPs).

- `server.py` — an `MCPServer` (mcp 2.x) exposing the MCP protocol over
  Streamable HTTP at `/mcp` (`stateless_http=True` so any replica can serve any
  request). Transport options go to `streamable_http_app()`, not the constructor.
- `requirements.txt` — only needs `mcp` + `uvicorn`; **do not** pull in the main
  backend's dependencies. Pin exact versions: the SDK has moved and renamed server
  APIs between releases, so a floor like `mcp>=1.6` breaks the next deploy. This
  app is on mcp 2.x while the backend is still on 1.x; that's fine, they deploy
  separately.

## Key constraints

- **Host check:** mcp 2.x accepts only a localhost `Host` header by default.
  `server.py` turns that check off when `DATABRICKS_APP_PORT` is set (the Apps
  proxy authenticates every request and forwards the app's public hostname), and
  binds to `127.0.0.1` with the check on otherwise. With the default left on, a
  request carrying the app's public hostname gets `421 Misdirected Request`.
- For Databricks AI Gateway to discover it, the **app name** (set in
  `databricks.yml`) must start with `mcp-`.
- The MCP protocol must be served at `/mcp`; the app runs standalone under uvicorn
  (not mounted under a prefix), so avoid path-doubling.
- Keep this app self-contained. If you need real tools, wire them here rather than
  importing from `backend/` — the two are deployed and versioned separately.
