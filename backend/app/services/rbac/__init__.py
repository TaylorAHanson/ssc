"""Access Rules: the group-to-securable ledger (see docs/adr/0005-rbac-access-rules.md).

Modules:

- ``layout``: environments, managed sheets and header layouts, from settings.
- ``audit``: change sets, rule events, row hashes and the ledger watermark.
- ``inflight``: open rbac_access_change requests and the rows they touch.
- ``cells``: atom semantics of packed ``;`` cells, and the deterministic
  row-edit planner that turns grants and revokes into workbook-style edits.
- ``policy``: the single policy module every write path calls.
- ``ledger``: reads, direct edits (preview / commit / revert), preparing and
  recording access changes, history and find-access.
- ``approvers``: the approver group per type, view / edit rights, group lookup.
- ``workbook_import``: one-time workbook import with preview, and guarded reset.
- ``csv_export``: the scheduled full-snapshot CSV export, path test and compare.
- ``report``: the approver-facing report of a prepared change (report_markdown).
"""
