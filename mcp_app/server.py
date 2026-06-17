"""Minimal standalone MCP server, hosted as its own Databricks App.

Why this exists
---------------
Databricks surfaces a custom, App-hosted MCP server under
**Workspace → AI Gateway → MCPs** when the **app name starts with ``mcp-``** and
the app serves the MCP protocol over **Streamable HTTP** at ``/mcp``. This file is
that server: a tiny, self-contained example you can deploy to validate the
discovery + hosting pattern end-to-end before pointing real tools at it.

It is intentionally independent of the main ATLAS backend — it only needs the
``mcp`` SDK and ``uvicorn`` (see requirements.txt).

Endpoint
--------
``streamable_http_path="/mcp"`` makes the protocol available at ``<app-url>/mcp``
directly (the app is run standalone by uvicorn, not mounted under a prefix, so
there's no path-doubling to worry about). ``stateless_http=True`` lets any app
replica serve any request, which is what Databricks Apps' load balancer needs.
"""
import os

from mcp.server.fastmcp import FastMCP

# The instance name is cosmetic (shown to clients); the *app* name (set in
# databricks.yml) is what must start with "mcp-" for AI Gateway discovery.
mcp = FastMCP("mcp-edh-ssc", stateless_http=True, streamable_http_path="/mcp")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the provided text straight back. Useful as a connectivity probe."""
    return text


@mcp.tool()
def add(a: float, b: float) -> float:
    """Return the sum of two numbers."""
    return a + b


# Starlette ASGI app. FastMCP wires the Streamable HTTP session-manager lifespan
# into this app, so running it directly under uvicorn is all that's required.
app = mcp.streamable_http_app()


if __name__ == "__main__":
    import uvicorn

    # Databricks Apps inject the port the platform expects the app to listen on.
    port = int(os.environ.get("DATABRICKS_APP_PORT") or os.environ.get("PORT") or "8000")
    uvicorn.run(app, host="0.0.0.0", port=port)
