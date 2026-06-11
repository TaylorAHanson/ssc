"""Agent tools for authoring no-code workflows (Workflows).

These are gated to platform/governance admins (``required_role="Governance Admin"``
— Platform Admins pass every role check, Governance Admins match by name, and
everyone else is filtered out by the chat endpoint). They let the *same* agent
that runs workflows also help an admin design, validate, preview, and ship them,
wrapping the exact same ``WorkflowService`` / spec-loader / dry-run / publish gate
the visual editor uses so both surfaces stay consistent.
"""
