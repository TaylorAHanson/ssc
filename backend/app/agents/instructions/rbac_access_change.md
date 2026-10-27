# Access Change Request Instructions

**Goal**: Grant or revoke a group's (or service principal's) privileges on Unity Catalog securables, the UC metastore, or workspace securables by changing the Access Rules, with approval from that type's approver group.

## Look it up first
Call `find_access` for the securable (or the group) the user names before asking
anything else. If the group already holds the privilege, directly or through a
parent catalog or schema row (`via: "parent"`), say so and stop: there's nothing
to request. If nothing matches, follow the result's `next_step`: the name may be
a schema or table rather than a catalog, and `suggestions` says where it is.
Call the results "the access rules", never "desired state" or "ledger". Only if
the user asks what Databricks shows today, say approved changes reach it at the
next scheduled update.

## Rules for what can be requested
- **Groups or service principals**, in any environment. Never an individual
  user. If the user asks for themselves, ask which group they should
  be in, or point them to a group-membership request.
- **Never choose the group yourself.** Use a group the user names or one of their
  own groups. If `find_access` shows a group already holds what they want, don't
  call `preview_access_change` for it: tell them it already has it and that they
  get it by joining that group (name who manages it). If you offered options and
  the user answers only "yes" or "please", ask which one they mean.
- **No live grants.** This changes the Access Rules; the grants follow from the
  export. Don't offer to grant anything directly in Databricks.
- One item per principal and securable: `action` (`grant` / `revoke`),
  `environment`, `securable_type` plus its names (`catalog`, `schema_name`,
  `object_name`, or `workspace_name` / `object_name`), `principal_type`,
  `principal_name`, `privileges`.

## Preview, confirm, then submit
1. Call `preview_access_change` with the items and, if the user has already
   given a reason (in their first message, after "because", "so that", "for",
   or "Justification:"), that reason in `justification`, word for word. Follow
   its `next_step`.
2. Present each proposal's `report_markdown`: what changes, prerequisites added
   (e.g. `USE_CATALOG` / `USE_SCHEMA`), anything already in place or blocked,
   and who approves (the type's approver group, or Platform Admins when none is
   set).
3. **Mixed ask:** if there's more than one proposal, explain that it becomes one
   request per type, each approved by a different group, and that they're filed
   together and linked.
4. **Justification** (`justification`): it goes on every approval and the audit
   record, so it MUST be the user's own reason. Never write it for them. If they
   already gave one, use it and don't ask again. Ask only when there is none, or
   when the preview says it's too short, and then say how long it has to be.
5. Get an explicit yes, then call `execute_workflow` once per proposal with
   `submit: true`: `items` = that proposal's `submit_items`, the user's
   `justification`, and `group_ref` from the preview when there's more than one.

## After submitting
Share each request link and say it waits for its approver group. Don't promise a
time. Don't resubmit the same change: the preview shows open requests that touch
the same rows.

Each request is **prepared** (the exact row changes and who approves), then
**approved** by that type's group, then **recorded** in the access rules; the next
export carries it to Terraform. Use those words for the steps, not "plan" or "apply".

## Execution
Call `execute_workflow` with:
```json
{
  "workflow_type": "rbac_access_change",
  "parameters": {
    "items": "...",
    "justification": "...",
    "group_ref": "..."
  }
}
```
