# Metadata Manager

**Metadata Manager**, in Watch Tower, keeps the tags and descriptions on
catalogs, schemas, tables, views and columns correct. Classification, ownership,
domain and data-product membership all ride on tags, and certification checks
them, so this is where they're maintained.

Governance sees every object: Platform Admins, the editor roles and the
**Browse roles** (Governance Admin by default). Data owners can open it too,
without an admin role, but see only the objects they own: search, counts,
datasets and history leave everything else out, and the page says *Showing only
the objects you own*. Anyone who owns nothing can't open it, and the sidebar
link is hidden for them.

## How it works

{{brand_name}} keeps its own list of the tags and descriptions each object
should have. A background sync compares that list with Unity Catalog and
makes Unity Catalog match. Changes you save reach Unity Catalog within a minute
or so, and a full check of every object runs on a schedule.

- **The list is the source of truth.** A tag added directly in Databricks
  shows up as *Out of sync* and, once syncing is on, is removed on the next
  pass. Make the change here instead.
- **Search is instant.** The page searches {{brand_name}}'s own copy of Unity
  Catalog, so it never waits on a warehouse.
- **Tables that don't exist yet.** You can prepare tags for a table before it's
  created. They're applied as soon as it exists.

## Finding a table

The page opens on a list of every catalog, with how many tables and views each
holds; the **Catalogs | Datasets** switch above it lists datasets instead (see
[Tagging many objects at once](#tagging-many-objects-at-once)). Click a catalog
to see its schemas, and a schema to see its tables. The
path at the top (*All catalogs › sales_dev › bronze*) takes you back up. Every
level has its own address, so you can share a link to a catalog, a schema or a
table.

If the last full check couldn't read a catalog, its page says so and gives a
short reason (for example the warehouse ran out of time on a large catalog).
What's already listed is the last successful read, not a wipe; new and changed
tables still appear. Admins can open the failed check under **Sync & admin →
Sync history**. Don't turn syncing on until a check of that catalog completes.

The search box searches wherever you are: everything from the catalog list, or
just one catalog or schema once you've clicked into it. **Filters** narrows the
results:

- **When you search, look for** tables and views (the default), columns, or
  everything. This only affects searches; browsing always lists every catalog,
  schema and table;
- **Out of sync with Unity Catalog**, for objects whose tags there differ from
  the ones saved here;
- **Things I can change without approval**;
- **Has this tag**, and the environment when more than one is set up.

You can also type a tag into the search box: `classification=restricted` (has
that value), `pii=*` (has the tag at all) or `!data_owner` (lacks it, the
quickest way to find certification gaps).

A list row shows a status only when something needs your attention, for
example *Unsaved changes*, *Waiting to sync* (or *Saved here only* while
syncing is set to report only), *Out of sync* or *Couldn't update Unity
Catalog*.

## Making a change

1. Open a table. Click the pencil next to the description to edit it, the
   cross on a tag to remove it, or **Add tag** to add one. Each column has the
   same controls in the **Columns** list. Catalogs and schemas have them at the
   top of their page. Nothing is saved yet: changed tags turn green or amber,
   and a bar at the bottom counts your unsaved changes.
2. Keep going on other tables if you like; the bar collects everything. Click
   the count to see the list or discard one object's changes.
3. Choose **Review and save**. It shows every change, which ones need
   approval and who approves them, any policy problems, and the risk score.
   It also warns when a changed tag is one a column mask or row filter keys
   on, because that changes who can see the data. It also warns about suspected
   typos, such as a tag key one letter away from one already in use
   (`clasification` for `classification`), a value that only differs in
   capitalisation, or stray spaces; these warnings never block a save, and
   admins can turn them off under **Admin → Settings → Metadata Manager**
   (*Warn about suspected typos in previews*). The first time you change a
   table here it's marked *Starts being managed*, and the tags and description
   it already has are listed as *Stays in Unity Catalog*: they're kept, not
   counted as edits, and need no approval. If one breaks a tag rule, the
   review shows a warning instead of stopping the save. Only what you change
   changes: removing one of those tags is listed as a removal and follows the
   usual approval rules. So nothing added in Unity Catalog since the last check
   is missed, a first edit of up to 200 tables reads them from Unity Catalog
   again before saving, and isn't saved if that read fails. When more than one
   environment is set up, **Also make these changes in** lets you tick other
   environments to make the same changes there (see [Environments](#environments)).
4. Add a reason (if your admin requires one) and save. Changes you're allowed to make are saved straight
   away; the rest become one approval request, which you can follow under
   **Requests** → **My requests**.

**Discard** on the bar drops every unsaved change. A key with no value, such as
`pii`, is a key-only tag and is applied as a tag with no value.

Each table page also has two sections that start folded away: **Details**
(owners, the same table in other environments, sync status, **Check Unity
Catalog now** and **Copy to another environment**) and **Activity** (every
change made to it here).

## Tagging many objects at once

- **A whole dataset.** Switch **Browse** from **Catalogs** to **Datasets**. A
  dataset is every table and view tagged with the same `dataset` (or
  `data_set`) value, such as `dataset=crm`, whatever catalog it's in. Open one
  to see each of its tags and how many of its tables carry it. **Add to the
  other N** puts a tag on the tables missing it, **Remove from all** takes it
  off every table, and **Add or change a tag on every table** sets one value
  everywhere. You can also tick some of its tables and choose **Edit these**.
  The changes join your unsaved changes like any other edit.
- **Pick them from a list.** Tick the tables in a schema, or in search results,
  and choose **Edit all**. Whatever you add, remove or describe applies
  to every ticked object and joins your unsaved changes.
- **By name.** Choose **More → Edit many tables by name**. Type a name pattern
  such as `sales_dev.bronze.*` (`*` matches any characters within one part and
  `?` matches one character), and optionally a column name or pattern such as
  `*_email`. Then choose tags to add or remove, or set a description. Tags are
  typed as `data_owner=sales_eng; domain=sales; pii`. To cover every
  environment, put the catalog in braces: `{sales}.bronze.*` matches
  `sales_dev.bronze.*`, `sales_prd.bronze.*` and the rest.
- **Paste from a spreadsheet**, in the same dialog. Copy the cells from Google
  Sheets or Excel, header row included, and paste them in. Name one column
  `name` (or `table`) for `catalog.schema.table`; every other column is a tag
  key, and each non-empty cell sets that tag. Optional `column` and
  `description` columns cover columns and descriptions. A row's name can be a
  pattern too.

**Preview** shows how many objects each pattern matched and exactly what
changes. The usual approval rules apply to every object. One change covers up
to 5,000 objects by default. Objects that aren't managed yet keep the tags and
description they have in Unity Catalog, as with a single edit; a change that
replaces an object's tags lists each tag it removes.

**More → Prepare tags for a table that doesn't exist yet** sets tags and a
description for a name before the table is created.

## History

**History** lists the changes you've saved, newest first; switch to
**Everyone's** to see all changes and who made them, from this page, the
assistant, a pipeline or the sync. **Filters** narrows by table and by where
the change came from. Open a change to see each tag it touched; **Undo this
change** saves the opposite change. Undoing the change that started managing an
object stops managing it instead: Unity Catalog keeps the tags and description
it has, and the preview marks the object *Stops being managed* and warns about
anything changed on it since. Changes still waiting for approval are under
**Requests** → **My requests**.

## Sync & admin

Platform Admins get a **Sync & admin** link in the page header (not to be
confused with **Admin** in the sidebar, which opens the platform dashboard) with:

- a summary of whether syncing is on, which catalogs it covers and when Unity
  Catalog was last checked;
- **Out of sync with Unity Catalog**: objects whose tags or description there
  differ from the ones saved here, key by key. **Keep Unity Catalog's values**
  adds what Unity Catalog has to your unsaved changes, so syncing stops undoing
  it;
- **Sync history**: what each check did or, when syncing is set to report
  only, would do, and any failures. **Check now** starts a full check that runs
  in the background and does what the mode says: while syncing is report only
  it lists what would be written, and once it's on it writes it. On a large
  catalog it takes a few minutes and the list updates when it finishes. You
  can leave the page.
  Each row counts only what that check looked at: a *full check* reads whole
  catalogs, while *only these objects* re-checks what was edited here or changed
  in Unity Catalog, so its 0 out of sync says nothing about anything else. What's
  out of sync right now is under **Out of sync with Unity Catalog**.
  A row marked *Couldn't read* didn't check that catalog at all (its details
  say why) and changed nothing in it. That includes a read that would have
  marked a large share of the catalog as dropped, because losing access to part
  of a catalog looks exactly like a mass drop (see **Max share missing per
  check** below), and a catalog too large to hold in memory. To see whether a
  long *Running* check is still working, search the app's logs for the id shown
  at the top of its details: a slow read logs a line every minute. A check cut
  off because the app restarted or was redeployed shows as *Failed* the next
  time Sync history loads; it wrote nothing more after it stopped, and the next
  check carries on as normal. A check that's writing also stops before its next
  statement if the sync settings change or the app starts shutting down; its
  row says why, and the next check writes the rest.
  A sync *Paused* by the removal limit writes nothing until a Platform Admin
  chooses **Review and resume**. **Apply these removals** re-checks only that
  sync's objects and writes their changes, as long as that removes no more than
  it planned (and only while syncing is on). **Clear without applying** lifts
  the pause and writes nothing. Fix what caused the removals first, or the next
  check pauses again.
- **Automation access**: the pipelines and systems that may change tags through
  the API or MCP, and their scope. See [Metadata API and MCP](metadata-api.md);
- **Bring in many tables at once**: **Start managing catalogs** and **Import
  from a file**.

### Starting out: start managing, report, then sync

Syncing looks after managed objects only. Once it's on, any tag a managed
object carries in Unity Catalog that isn't saved here is removed. An object's
first edit here keeps what it already has, but objects nobody has edited aren't
managed, so drift on them isn't noticed. Bring existing catalogs under
management before turning syncing on:

1. **Start managing catalogs** saves what Unity Catalog holds now for a catalog
   or schema, so those tags are kept. **Import from a file** loads tags from a
   CSV or JSON file instead. Both copy the last full check, so they're refused
   unless that check read the catalog in the last 30 minutes (**Freshest copy
   to start managing from**): an out-of-date copy would let syncing remove tags
   added since. If it's refused, choose **Check now** under **Sync history**,
   wait for it to finish, and try again.
2. Leave **Reconcile mode** on **Plan** (shown on the page as *Report only*)
   and choose **Check now** under **Sync history**. A row appears
   immediately as *Running*; when it finishes, it lists what syncing would set
   and remove, without changing anything. Check the removals. Hourly scheduled
   checks report the same way — you don't have to start one by hand.
3. Switch the mode to **Enforce** (shown as *Keeping Unity Catalog in sync*).
   From then on every check writes: the hourly one, the one that follows a
   change made directly in Unity Catalog, and **Check now**. The first of them
   applies everything saved in the meantime. Report only is the preview; there
   isn't a separate one once syncing is on.

To start with part of a catalog that people are already using, list one schema
under **Enforce only in these schemas** before step 2. The check then marks
each statement for the rest of the catalog *Report only*, and once you switch
to Enforce only that schema is written; everything else in the catalog stays
as it is in Unity Catalog, though edits to it are still saved here. The removal
limit counts only removals in the listed schemas. It doesn't count a changed
value, so read every statement for the schema in that check, not just the
removals.

## Who can edit

- **Owners.** An object's owner tag (`data_owner` by default) names the groups
  that own it. Members of those groups can edit it. An object without the tag
  inherits the owner of its table, schema or catalog.
- **Admins.** Platform Admins, and any roles an admin adds as editors, can edit
  everything.
- **Everyone else** can propose a change. It goes to the object's owners, or to
  the governance approver group when nobody owns it. The preview they get back
  doesn't show the current values of objects they can't browse.

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
first, then open it and choose **Details → Copy to another environment**:
{{brand_name}} copies the dev values to the matching production object as a
new change, with the same approval rules.

To make a change in several environments at once, tick them under **Also make
these changes in** on the **Review and save** step, or in **Edit many tables
by name**. The preview updates to show the copies, and each environment's own
approval rules apply. An object whose catalog isn't named by an environment
pattern (a catalog listed by name, say) has no counterpart: it's changed where
it is, and the preview warns that the other environments were skipped for it.
A name pattern with the catalog in braces, such as `{sales}.bronze.*`, already
covers every environment, so the choice isn't offered with one.

## Asking the assistant

You can also ask the assistant, for example "mark the email column in
sales_prd.crm.customers as PII". It looks the object up, shows what will change
and who has to approve, and saves the change or files the approval once you
confirm.

## Settings

Platform Admins configure the Metadata Manager under **Admin → Settings →
Metadata Manager**:

- **Reconcile mode**: **Plan** (the default, shown on the page as *Report
  only*) reports what would change without changing anything; **Enforce**
  (*Keeping Unity Catalog in sync*) makes Unity Catalog match, including
  removing tags not saved here; **Off** stops syncing. Enforce only runs once
  **Catalogs to reconcile** and **Enforce only in these schemas** are filled in.
- **Catalogs to reconcile**: the catalogs this deployment manages, as names or
  patterns such as `*_dev` (`*` is every catalog). It only changes, adopts new
  tables in and accepts edits for these; an edit to any other catalog is
  refused at preview, and a bulk edit leaves those matches out. Blank means it
  reads and plans every catalog but changes none. On a dev or stage deployment,
  list only that environment's catalogs. The governance service principal needs
  BROWSE on each catalog to see all of it; Unity Catalog hides the tables it
  has no privilege on, so they're missing from the list.
- **Enforce only in these schemas**: limits what Enforce writes to these
  schemas, as `catalog.schema` names or patterns such as `sales_dev.bronze` or
  `sales_dev.*_raw`. The rest of the catalogs above is still checked and can
  still be edited, but is only reported, and its statements show as *Report
  only* in **Sync history**. A catalog's own tags are outside every named
  schema. `*` means every schema and the catalogs' own tags. Blank means
  Enforce writes nothing, so clearing the list can never widen what's written.
  Each entry is checked when you save.
- **Browse roles**: who sees every object, besides Platform Admins and the
  editor roles. Everyone else sees only what they own. Blank = admins and
  editor roles only; clearing it never opens the page to everyone.
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
- **Max removals per hour**: how many tags or descriptions syncing may remove
  or blank out in an hour, counted across every run (50 by default). A run
  that would go over writes nothing, is paused under **Sync history** and
  alerts governance. Removals a Platform Admin applies from a paused sync don't
  count. Set it to 0 and every removal waits for review.
- **Statements in parallel**: how many tables the reconciler updates at once.
- **Max objects per change**: 5,000 by default.
- **Wait after a new table changes**: a new table, or one dropped and
  recreated, is left alone until it has gone this many minutes without
  changing (10 by default). Pipelines often create a table and tag it a moment
  later; taking it over in between would treat those tags as drift. When a
  table that already has tags saved here is created or recreated, tags it was
  created with that aren't saved here are kept.
- **Max share missing per check**: a full check that would mark more than this
  percent of a catalog's objects as dropped (and more than 500 of them) saves
  nothing and shows the catalog as *Couldn't read* (5% by default). If a large
  drop is real, raise it, choose **Check now**, then set it back.
- **Largest catalog read (rows)**: a catalog whose tables, columns or tags come
  to more rows than this in one read is skipped and shows as *Couldn't read*,
  so it can't run the app out of memory (2,000,000 by default).
- **Freshest copy to start managing from**: how recent, in minutes, the last
  full read of a catalog must be for **Start managing** and imports (30 by
  default; 0 turns the check off).
- **Keep sync history**: Sync history rows older than this many days are
  deleted (30 by default; 0 keeps everything). Paused syncs and the newest run
  of each kind are kept, and changes stay in **History** regardless.
