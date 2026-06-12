"""External tool integration: Databricks MCP discovery + remote invocation.

This package is intentionally importable even when the optional ``databricks_mcp``
dependency is absent — every use of it is lazily imported inside functions so the
app (and ``load_tools`` auto-discovery) never fails just because MCP isn't wired
up in a given environment.
"""
