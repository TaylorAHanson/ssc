# Metadata Manager

**Metadata Manager**, in Watch Tower, keeps the tags and descriptions on
catalogs, schemas, tables, views and columns correct. Classification, ownership,
domain and data-product membership all ride on tags, and certification checks
them, so this is where they're maintained.

Everyone can open it by default, so data owners can edit their own objects
without an admin role. A Platform Admin can limit it to the **Browse roles**
setting.

## How it works

{{brand_name}} keeps its own list of the tags and descriptions each object
should have. A background reconciler compares that list with Unity Catalog and
makes Unity Catalog match. Changes you save reach Unity Catalog within a minute
or so, and a full check of every object runs on a schedule.

- **The list is the source of truth.** A tag added directly in Databricks
  shows up as drift and, once enforcement is on, is removed on the next pass.
  Make the change here instead.
- **Search is instant.** The page searches {{brand_name}}'s own list, never
  Unity Catalog, so filters don't wait on a warehouse.
- **Tables that don't exist yet.** Type a full name to declare tags for a table
  before it's created. They're applied as soon as it exists.

## Finding objects

The **Objects** tab lists every object {{brand_name}} knows about. Type part of
a name, or filter by:

- **Catalog** and **type**, from the side panel, which also shows how many
  objects match each value;
- a tag: `classification=restricted` (has that value), `data_owner` (has the
  tag) or `!data_owner` (lacks it, the quickest way to find certification gaps);
- **Editable by me**, to see only what you can change directly.

Open an object to see the tags it should have next to the ones Unity Catalog
holds, who set each tag, when and from where, its columns, and its history.

## Making a change

1. Edit tags and the description on one or more objects. Each edit goes into
   the tray at the bottom of the page.
2. Choose **Review staged edits**. It shows every change, which ones need
   approval and who approves them, any policy problems, and the risk score.
   It also warns when a changed tag is one a column mask or row filter keys
   on, because that changes who can see the data.
3. Add a reason and choose **Submit**. Changes you're allowed to make are saved
   straight away; the rest become one approval request.

A key with no value, such as `pii`, is a key-only tag. The reconciler applies
it as a tag with no value.

## Starting out: adopt, then plan, then enforce

The first time an object is edited here, the change becomes its complete
desired state. Under **Enforce**, any tag it carries in Unity Catalog that
isn't in that state is removed. So bring existing objects under management
before editing or enforcing them:

1. **Adopt current state** (Platform Admins, on the **Objects** tab) declares
   what Unity Catalog holds now for a catalog or schema, so those tags are
   kept. **Import CSV or JSON** loads desired state from a file instead.
2. Leave **Reconcile mode** on **Plan** and choose **Run a plan** on the
   **Reconciler runs** tab. It lists what enforcing would set and remove,
   without changing anything. Check the removals.
3. Switch the mode to **Enforce**. The next full check, or **Run a full check
   now**, applies everything saved in the meantime.

**Declare a new object** adds tags for a table that doesn't exist yet; they're
applied once it's created.

## Tagging many objects at once

Choose **Bulk edit** on the **Objects** tab. It has two ways to pick objects:

- **Match a pattern.** Type a name pattern such as `sales_dev.bronze.*` (`*`
  matches any characters within one part and `?` matches one character), and
  optionally a column name or pattern such as `*_email`. Then choose tags to add
  or remove, or set a description. Tags are typed as
  `data_owner=sales_eng; domain=sales; pii`.
- **Paste from a spreadsheet.** Copy the cells from Google Sheets or Excel,
  header row included, and paste them in. Name one column `name` (or `table`) for
  `catalog.schema.table`; every other column is a tag key, and each non-empty
  cell sets that tag. Optional `column` and `description` columns cover columns
  and descriptions. A row's name can be a pattern too.

**Preview** shows how many objects each pattern matched and exactly what
changes. The usual approval rules apply to every object. One change covers up
to 5,000 objects by default. If the preview warns that objects aren't managed
yet, adopt their catalog or schema first, or the tags they carry now are
removed under Enforce.

## Who can edit

- **Owners.** An object's owner tag (`data_owner` by default) names the groups
  that own it. Members of those groups can edit it. An object without the tag
  inherits the owner of its table, schema or catalog.
- **Admins.** Platform Admins, and any roles an admin adds as editors, can edit
  everything.
- **Everyone else** can propose a change. It goes to the object's owners, or to
  the governance approver group when nobody owns it.

## What needs approval

Most changes by an owner are saved straight away. These are *sensitive* and
always need approval:

- changing the owner tag, or an approver or access tag;
- lowering or removing a classification;
- removing a PII flag (adding one doesn't need approval).

An environment can also require approval for every change. Approved changes are
applied by the reconciler like any other. Approvers see exactly what will
change on the approval card.

## Environments

One production deployment manages every environment's catalogs, for example
`sales_dev` and `sales_prd`. To try a change safely, make it on the dev object
first, then open the object and choose **Copy to another environment**:
{{brand_name}} copies the dev values to the matching production object as a
new change, with the same approval rules. A change can also name extra
environments to apply to at once.

## Other tabs

- **Changes**: every change, who made it and from where (this page, the
  assistant, a pipeline or the reconciler), with its reason and each tag it
  touched. Filter by object to see a catalog's or schema's changes, including
  the objects under it. **Undo this change** saves the opposite change.
- **Drift**: objects whose Unity Catalog tags or description differ from the
  list. **Check Unity Catalog now**, on an object, asks the reconciler to look
  at it straight away.
- **Reconciler runs**: what each run did or, in plan mode, would do, and any
  failures.
- **System principals** (Platform Admins): the pipelines and systems that may
  change tags through the API or MCP, and their scope. See
  [Metadata API and MCP](metadata-api.md).

## Asking the assistant

You can also ask the assistant, for example "mark the email column in
sales_prd.crm.customers as PII". It looks the object up, shows what will change
and who has to approve, and saves the change or files the approval once you
confirm.

## Settings

Platform Admins configure the Metadata Manager under **Admin → Settings →
Metadata Manager**:

- **Reconcile mode**: **Plan** (the default) reports what would change without
  changing anything; **Enforce** makes Unity Catalog match, including removing
  undeclared tags; **Off** stops the reconciler. Enforce only runs once
  **Catalogs to reconcile** is filled in.
- **Catalogs to reconcile**: the catalogs this deployment manages, as names or
  patterns such as `*_dev` (`*` is every catalog). It only changes, adopts new
  tables in and accepts edits for these; an edit to any other catalog is
  refused at preview, and a bulk edit leaves those matches out. Blank means it
  reads and plans every catalog but changes none. On a dev or stage deployment,
  list only that environment's catalogs.
- **Browse roles**: who can open the page. Blank = everyone.
- **Owner tag key**, **Editor roles** and **Governance approver group**: who
  can edit and who approves. With no governance approver group, only Platform
  Admins approve sensitive changes and changes to objects nobody owns.
- **Key policies**: per key, where it may be used, which values it allows, which
  groups may change it and whether it's sensitive.
- **Environments**: which catalogs belong to which environment.
- **Exempt tag keys**: keys the reconciler never touches.
  `system.certification_status` is on it, because OmniGuard owns it.
- **Check access policies in previews**: whether a preview reads the column
  masks and row filters on the changed objects. It needs READ METADATA for the
  governance service principal; for changes to more than 50 tables only the
  catalog and schema policies are read.
- **Max removals per run**: a run that would remove more tags, or clear more
  descriptions, stops and alerts governance instead. Saved changes are applied
  in batches of up to 200 objects, and the limit counts per batch. Set it to 0
  and every removal waits for you to review and resume the run.
- **Statements in parallel**: how many tables the reconciler updates at once.
- **Max objects per change**: 5,000 by default.
