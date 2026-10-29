# Metadata API and MCP

Pipelines and other systems can read and change Unity Catalog tags and descriptions
through {{brand_name}} instead of running `ALTER` statements themselves. Changes go
through the same checks, owner permissions and approvals as the
[Metadata Manager](metadata-manager.md) page, they're recorded in its history, and the
reconciler applies them to Unity Catalog. Tags can be declared for a table before it
exists: they're applied as soon as it's created.

There are two ways in:

- **REST**, under `/api/v1/metadata` on the app's URL, for pipelines and scripts.
- **MCP**, at `/mcp` on the app's URL, for AI agents and other MCP clients.

## Authentication

{{brand_name}} doesn't issue API keys. A system calls it with a Databricks OAuth token:

- **A service principal** (most pipelines) uses its own machine-to-machine token. It
  needs **Can use** on the app, and a Platform Admin or Metadata Manager editor must
  register it under **Admin → Automation access** in the Metadata Manager.
- **A person** (scripts, notebooks) uses their own token and gets exactly their own
  rights, as on the page.

```python
from databricks.sdk import WorkspaceClient

w = WorkspaceClient(host=WORKSPACE_URL, client_id=CLIENT_ID, client_secret=CLIENT_SECRET)
headers = w.config.authenticate()  # {"Authorization": "Bearer ..."}
```

Then call the app with those headers. Check what the app sees first:

```http
GET {app_url}/api/v1/metadata/whoami
```

`recognized_as` is `service_principal` for a registered principal. If it says
`unregistered_service_principal`, `message` names the identifier to register.

## What a system principal may do

Each registered principal has a scope, set under **Admin → Automation access**:

- **Names** it may change, as globs: `sales_*` (whole catalogs), `sales_dev.bronze` (one
  schema) or `*.bronze.*` (every bronze table). Nothing outside them, whatever the owner
  tags say.
- **Keys** it may set (globs; blank = any key the key policies allow).
- **Operations** and **environments** it may use (blank = all).
- **Max objects per change**, which can be higher than the page's limit.
- **Can skip approval**: whether its changes inside its scope are written directly even
  when they'd otherwise need approval.

## Read

| Call | Returns |
| --- | --- |
| `GET /objects?q=orders&catalogs=sales_prd&tag=pii&tag=!data_owner` | Matching objects with desired tags, description, drift and facets. `tag` is repeatable: `key=value`, `key` (has the key) or `!key` (lacks it). |
| `GET /object?name=sales_prd.crm.customers&column=email` | One object: desired and actual tags with each tag's author and version, drift, columns, owner groups and history. |
| `GET /keys?q=class` | Tag keys in use, with their values and key policy. |
| `GET /drift?catalog=sales_prd` | Managed objects whose Unity Catalog state differs. |
| `GET /changes`, `GET /changes/{id}` | Change history, and one change with every tag it touched. |

Every read answers from the app's own tables, never from Unity Catalog, so it is fast
and doesn't use a warehouse.

## Change

Preview first, then submit the same body:

```http
POST /api/v1/metadata/changes/preview
POST /api/v1/metadata/changes
Idempotency-Key: nightly-load-2026-10-20

{
  "ops": [
    {"op": "set_tags", "name": "sales_dev.crm.customers", "column": "email",
     "tags": {"pii": null, "classification": "restricted"}},
    {"op": "set_comment", "name": "sales_dev.crm.customers", "comment": "One row per customer."}
  ],
  "reason": "Classify the customer table after the schema change",
  "environments": ["prd"],
  "on_approval_needed": "split"
}
```

- **Operations:** `set_tags` adds or updates tags, `unset_tags` removes `keys`,
  `replace_tags` makes `tags` the complete set, and `set_comment` / `clear_comment` set
  or clear the description. A `null` value is a key-only tag.
- **Objects not managed yet** start from the tags and description they have in Unity
  Catalog, so only what an operation names changes; `replace_tags` removes the rest, and
  the preview lists each removal. Send only your edits, not the full set. A value that
  Unity Catalog already has needs no approval, but the caller must still be allowed to
  change or request a change to that object and key.
- **The object doesn't have to exist.** Its desired state is kept as *pending* and
  applied when the table is created.
- **Many objects at once:** give an operation `match` instead of `name`, a pattern
  with `*` (any characters) and `?` (one character) inside each part, for example
  `{"op": "set_tags", "match": "sales_dev.bronze.*", "tags": {"domain": "sales"}}`.
  One part matches catalogs, two schemas and three tables and views. Add `column`
  (a name or a pattern) to match columns, or `match_types` such as `["view"]` to
  match only some kinds of table. Put the catalog part in braces to mean that catalog
  in every environment: `{sales}.bronze.*` matches `sales_dev.bronze.*`,
  `sales_prd.bronze.*` and so on, using the catalog patterns under **Environments**
  (catalogs listed by name, rather than by pattern, aren't included). A pattern
  covers every object {{brand_name}} knows about: what Unity Catalog had at the last
  sweep plus objects declared ahead of time. The preview's findings say how many objects each pattern matched, and the
  change fails if one matches nothing. Patterns can't be combined with
  `expected_versions`. One change covers at most 5,000 objects by default (**Max
  objects per change** in Settings).
- **`environments`** applies each operation to the same object in those environments
  too, for example the `prd` counterpart of a `dev` table. An object whose catalog
  isn't named by an environment pattern has no counterpart: it's changed where it is,
  that environment is skipped for it, and the preview says so in a `no_counterpart`
  warning.
- **Concurrent edits:** to make sure nobody changed a tag since you read it, pass
  `expected_versions` (`{"classification": 3}`; `0` means the tag must not exist yet),
  or `expected_comment_version` for the description. A mismatch fails the whole change
  with 409 and lists the conflicts.
- **Retries:** send an `Idempotency-Key` header (or `idempotency_key` in the body).
  Repeating a change with the same key returns the first result without writing again.

### When a change needs approval

Some changes need approval: sensitive ones (such as the owner tag, access-control tags,
a classification downgrade or removing a PII flag), and anything on an object the caller
doesn't own. `on_approval_needed` decides what happens:

- `request` (default): the whole change is filed as one approval request.
- `split`: what the caller may change directly is written now; the rest is filed. An
  object that isn't managed yet is filed whole when any of its changes needs approval,
  so syncing can't remove a tag of it that's still waiting.
- `reject`: nothing is written, and the response explains why.

The response's `status` is `committed`, `pending_approval`, `partial`, `noop` or
`rejected`, with the change set and the request id.

### Errors

| Status | Meaning |
| --- | --- |
| 400 | The request is malformed, for example a bad name. |
| 403 | The caller may not do this; `detail` says why. |
| 404 | No such object or change. |
| 409 | Someone changed a tag or description since you read it; `detail.conflicts` lists each one. |
| 422 | A key policy blocks the change; `detail.preview` holds the findings. |

## Tell the reconciler about new tables

The reconciler finds new tables on its own within a minute or so. A pipeline that wants
its tags applied straight away can say so right after `CREATE`:

```http
POST /api/v1/metadata/reconcile
{"names": ["sales_dev.crm.customers", "sales_dev.crm.orders"]}
```

A service principal can only notify names inside its scope.

## MCP tools

Over MCP, {{brand_name}} publishes four tools, which a Platform Admin can switch off in
the Tool Registry:

| Tool | Does |
| --- | --- |
| `search_metadata` | Searches desired state (`query`, `catalog`, `tag`, `securable_type`, `editable_only`, `limit`). |
| `get_metadata` | Reads one object (`name`, `column`). |
| `preview_metadata_change` | Dry-runs `changes` (the same operations as `ops` above) with a `reason` and optional `environments`. |
| `submit_metadata_change` | Submits them, with `on_approval_needed` and `idempotency_key`. |

The MCP client authenticates the same way, and the tools act with the caller's own
rights: a person's groups, or a service principal's scope.
