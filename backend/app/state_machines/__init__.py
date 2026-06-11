"""
Legacy package retained only for two engine-agnostic utilities still used by the
V2 engine and API:

* ``facts`` — the immutable fact/event store (audit + idempotency + UI timeline)
* ``lock``  — request-level advisory locking for the poller

The V1 state-machine engine (base/factory/persistence/decorators + per-workflow
state machines) was removed in the V2 LangGraph cutover. Workflows now live as
durable graphs under ``app.v2``.
"""
