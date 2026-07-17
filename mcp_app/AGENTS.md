# mcp_app/AGENTS.md

A **minimal, standalone MCP server** deployed as its *own* Databricks App —
independent of the main backend. It exists to validate the Databricks
App-hosted-MCP discovery + hosting pattern (AI Gateway → MCPs).

- `server.py` — a `FastMCP` server exposing the MCP protocol over Streamable HTTP
  at `/mcp` (`stateless_http=True` so any replica can serve any request).
- `requirements.txt` — only needs `mcp` + `uvicorn`; **do not** pull in the main
  backend's dependencies.

## Key constraints

- For Databricks AI Gateway to discover it, the **app name** (set in
  `databricks.yml`) must start with `mcp-`.
- The MCP protocol must be served at `/mcp`; the app runs standalone under uvicorn
  (not mounted under a prefix), so avoid path-doubling.
- Keep this app self-contained. If you need real tools, wire them here rather than
  importing from `backend/` — the two are deployed and versioned separately.
