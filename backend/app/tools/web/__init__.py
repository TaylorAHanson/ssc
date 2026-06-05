"""Web lookup tools (Databricks docs search + approved-domain page fetch).

These tools let the agent answer product/how-to questions by consulting
official documentation. They are gated by the ``web_search`` feature flag
and constrained to an operator-controlled domain allowlist with SSRF
protections. See ``app.tools.web._common`` for the shared safety layer.
"""
