# Access rules

**Access Rules**, in the Watch Tower section of the sidebar, is the record of
which groups hold which privileges on Unity Catalog securables, metastore
objects and workspace items. An external Terraform pipeline applies it from CSV
files that {{brand_name}} exports on a schedule. It replaces the RBAC workbook:
after the first import, people change access here or through the assistant,
never in the spreadsheet.

Access rules only cover groups (and, where an environment allows it, service
principals). Adding people to groups is a separate request.

## The three types of rule

Every rule belongs to one of three types. Each type has its own sheets and its
own approver group.

| Type | Sheets | Approver group setting |
| --- | --- | --- |
| UC securables | `UC_Securables_RBAC_<Env>`, one per environment | **UC securables approver group** |
| UC metastore | `UC_metastore_RBAC` | **Metastore approver group** |
| Workspace securables | `WS_Securables_RBAC_<Env>`, one per environment | **Workspace securables approver group** |

The approver group is a Databricks group, named exactly as it appears in
Databricks. Its members approve requests of that type and can edit that type's
rules directly. The **Approvers** line at the top of the page shows each type's
group, with a pencil where you can edit that type and an eye where you can only
view it. A type with no group shows **Not set** and falls back to Platform
Admins, and Platform Admins can approve every type.

The group can be a workspace group or an account group, including one that
isn't assigned to this workspace, as Unity Catalog grants often use. The dot
after each group shows what Databricks says about the name (green, amber or
red); hover over it for the detail:

- **Found in Databricks:** the group is in this workspace, or you're in it.
- **Found in the account:** an account group that isn't assigned to this
  workspace. Members are recognised from their account groups, so they still
  get the approvals.
- **Couldn't confirm:** the group isn't in this workspace, and the account's
  groups couldn't be checked. Check that the name matches the group exactly.
- **Not found in Databricks:** no group in the workspace or the account has this
  exact name, so only Platform Admins get these approvals until it's fixed. A
  red warning under the line says so.

If a member of an account group sees that type as **View only**, {{brand_name}}
couldn't read their account groups; the warning in the app's logs says why.
Assigning the group to the workspace also fixes it.

The access request workflow reads these settings when each request reaches its
approval step, so you change approvers here, not in the workflow. In the
workflow editor that step shows as **Access rule approvers** and lists the
three groups.

## Who can use the page

- **Platform admins** can do everything, including importing the workbook.
- **Members of an approver group** see every sheet and edit their own type's
  sheets.
- **Viewer roles** (Governance Admin by default) see everything but can't
  change it.

Nobody else sees the page in the sidebar.

## How a request becomes a rule

People ask the assistant for access, for example "give `grp_finance_analysts`
SELECT on the `sales` schema in prod". The assistant first checks the rules: if
the group already has that access, it says so instead of filing a request. A
bare name doesn't have to say what it is: if `sales` isn't a catalog, the
assistant finds the schema or table with that name, and says which one it used.
Otherwise it shows the proposed change, including any prerequisite it adds
(such as `USE_SCHEMA` alongside `SELECT`), and asks for confirmation and a
justification in the requester's own words.

A request covers one type. If an ask spans types, such as workspace access plus
a schema grant, the assistant files one request per type and links them, since
each type has different approvers. Each part is approved on its own.

Each request then goes through three steps:

1. **Prepare** works out the exact row changes and who approves.
2. **Approve** waits for the approver group of the request's type. The approval
   card shows the prepared change, any linked requests, and who approves, such
   as "UC securables approvers: `grp_uc_admins`", or "Platform Admins (no UC
   securables group set)" when that type has no group.
3. **Record** writes the approved change into the rules straight away. The
   request then completes and names the next scheduled export.

When that export runs, the request's timeline shows it. Terraform applies the
change on its next run.

Requesters can't approve their own requests. See
[Approvals](settings-and-access.md#approvals) in Settings.

## Rules tab

Pick a sheet with the **Type** selector (UC securables, workspace securables or
the UC metastore), then the **Environment** selector, which shows each sheet's
row count; the metastore has one sheet for every environment. You see its rows
exactly as the workbook had them, including packed
cells such as `USE_SCHEMA;SELECT` or a list of groups. Search across every
column, or filter by column. A marker on a row means an open request will
change it.

To fit more on a small screen, drag the border between two column headings to
make a column narrower or wider. Text that no longer fits ends in "…"; hover
over it to read the whole value. {{brand_name}} remembers your widths in this
browser, for each type of sheet. Double-click a border to put that column back,
or select **Reset columns** above the grid to restore them all. The tables on
the History, Exports and Import tabs resize the same way.

If you can edit the sheet:

1. Edit cells inline, add rows, duplicate a row or delete one. Your edits stay
   staged in the tray at the bottom until you save or discard them; switching
   sheets or tabs asks before discarding them.
2. Select **Review** to check the changes against the same rules that requests
   follow. Blocking findings, such as a privilege that isn't on the allow list
   or an individual user, must be fixed first. Warnings don't block, including
   a workspace or catalog name that isn't in the environment's list.
3. Give a reason and commit. The whole set is saved together, or not at all.

Where a column has a set list of values, editing a cell offers that list
instead of free text:

- **Environment**: any environment on the metastore sheet; on an
  environment's own sheet, just that environment.
- **Securable type**: the types this sheet holds.
- **Principal type**: `group`, plus `service principal` where the row's
  environment allows service principals.
- **Privilege**: a checklist of what the allow list permits on the row's
  securable type; tick as many as you need. A privilege already on the row
  that the allow list doesn't include stays ticked and marked in red. You can
  remove it, but you can't add it back.
- **Workspace name**, and the securable name on a `workspace` or
  `workspace entitlement` row: the workspaces set up for the environment.
- **Catalog**, on UC securables sheets: the catalogs set up for the
  environment.

Workspace names and catalogs stay free text until an environment has a list.
A value already on the row that isn't in the list shows as the current choice,
and it stays as it is unless you pick another. The other columns, such as
schema, object, owner and principal names, are free text that suggests the
values already used on the sheet; you can still type a new one. In a list,
the arrow keys move and typing jumps to a value; Space ticks a privilege,
Enter saves and Esc cancels.

When your changes put a value outside its list, for example a privilege the
row's new securable type doesn't allow, the grid marks it in red so you can
fix it before you review. Values you haven't touched aren't marked.

The lists come from **Admin → Settings → Watch Tower → Access Rules**: the
**Allowed privileges (allow only)** setting, and the **Workspaces** and
**Catalogs** columns of **Environments**.

If someone else changed a row after you loaded it, the commit stops and shows
you their version. You can keep your edit on top of theirs or drop it.

An environment can require a second approver for direct edits. On those
sheets, committing files a request instead, and another member of the type's
approver group must approve it.

## History tab

Every change is listed here, whichever way it was made: a request, a direct
edit, an import or a revert. Open one to see who made it, when, why, and the
row-by-row difference. **Revert this change set** undoes it as a new change,
which is checked like any other and needs its own reason. Where an environment
requires a second approver, the revert files a request instead, just as a
direct edit does.

## Exports tab

Each export writes one CSV per sheet, named after the sheet, with the sheet's
own headers and every row in order. The files are meant to be identical to
what a person gets when they export each sheet from Excel, so the Terraform
pipeline reads them without change.

- Exports run on the schedule set in Settings, and can also run after every
  change. **Export now** runs one straight away. A scheduled run is skipped when
  nothing has changed since the last one.
- **Destinations** lists where each environment's sheets are written, plus the
  metastore sheet's own destination. **Test** writes and deletes a small file
  there, to prove {{brand_name}} can write to it. An environment with no path
  isn't exported, and the tab says so.
- **Runs** lists past exports. Open one for its files, row counts and
  checksums, and download any file.
- **Compare with a manual export** checks a CSV that a person exported from the
  workbook against the current export of the same sheet. It tells you whether
  they're byte-identical and, if not, why: byte-order mark, line endings,
  headers, quoting, or rows on only one side.

## Import tab

The workbook is imported once, to seed the rules. Only Platform Admins see this
tab.

1. Upload the workbook. The preview lists, per sheet, the rows found, the
   headers captured and any duplicate rows. Tabs that aren't managed sheets,
   such as a scratch tab, are listed as skipped.
2. Import. The workbook is the source of truth, so every row is copied exactly
   as it is. Rows aren't checked against the access rules: those rules apply to
   changes made after the import, so existing access is kept exactly.

**Reset and re-import** replaces every rule with a new workbook. It's off by
default (**Allow reset and re-import** in Settings), needs a typed confirmation, and is blocked while access requests are
open. A reset is recorded in History like any other change, so it can be
reverted. Leave it off in production: a re-import discards every change made
in {{brand_name}} since the seed.

## Settings

**Admin → Settings → Watch Tower → Access Rules** holds everything the page
uses:

- **Environments**: each environment's key, the names it goes by, its sheet
  suffix (`Dev`, `Prd`, …), its export path, and its policy: whether
  self-service requests are allowed, whether write privileges and service
  principals are, and whether direct edits need a second approver
  (**Two-person rule**). **Workspaces** and **Catalogs** (comma-separated)
  are the names the grid offers in its dropdowns on that environment's
  sheets. When a list is set, only those names can be added: a request using
  another name is blocked, and a direct edit gets a warning. Leave a list
  blank to let people type any name.
- **Metastore export path**, **Export schedule**, **Also export after every
  change**, **Write a byte-order mark**, **Line ending**, and how long exported
  files are kept.
- The three approver groups and **Viewer roles**.
- **Max row changes per save**, and the minimum reason and justification
  lengths.
- **Allowed privileges (allow only)**: one row per securable type, listing
  the only privileges a request or direct edit can add on it. The grid offers
  these as its choices. A type with no row can't be granted anything new, and
  a privilege Databricks adds later can't be granted until you add it here.
  Removing a privilege is always allowed, and existing rules are left as they
  are, even where they hold a privilege the list no longer includes. The
  default list covers every privilege {{brand_name}} knows except
  `ALL_PRIVILEGES`, `MANAGE`, `SET_SHARE_PERMISSION` and the metastore's
  `CREATE_CATALOG`, `CREATE_EXTERNAL_LOCATION`, `CREATE_STORAGE_CREDENTIAL`,
  `CREATE_CONNECTION`, `CREATE_SHARE`, `CREATE_RECIPIENT` and `CREATE_PROVIDER`.
- **System groups** nobody may change, the **Default owner** and **Naming
  rules**.
- **Header aliases**, for a renamed workbook column, and **Allow reset and
  re-import**.

Export paths can be a Unity Catalog volume (`/Volumes/...`) or a workspace
folder. {{brand_name}}'s service principal needs write access to each one.

Add approvers to an approver group directly. Membership through a nested group
may not be recognised. Use the group's display name from the workspace; a
group the workspace can't see shows as not found.

## Moving from the workbook

1. Set the export path for each environment and for the metastore sheet, and
   check each one with **Test**.
2. Freeze the workbook so nobody edits it, then import it.
3. Export, then use **Compare with a manual export** on every sheet against the
   last CSVs exported by hand. If a sheet differs only by byte-order mark or
   line endings, change that setting and export again, until every sheet is
   byte-identical.
4. Set the three approver groups. Check that a member sees and can approve a
   request of their type, that someone outside the group can't, and that a
   requester can't approve their own request.
5. Run the pipeline once against the exported files, then switch its input to
   the export paths.
