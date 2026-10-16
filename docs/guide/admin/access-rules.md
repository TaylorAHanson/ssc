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
rules directly. The strip at the top of the page shows each type's group and
warns when one isn't set or can't be found in Databricks. A type with no group
falls back to Platform Admins.

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
the group already has that access, it says so instead of filing a request.
Otherwise it shows the planned change, including any prerequisite it adds
(such as `USE_SCHEMA` alongside `SELECT`), and asks for confirmation and a
justification in the requester's own words.

A request covers one type. If an ask spans types, such as workspace access plus
a schema grant, the assistant files one request per type and links them, since
each type has different approvers. Each part is approved on its own.

The approval card shows the planned change, the approver group and any linked
requests. Approving it records the change in the rules straight away; the
request then completes and names the next scheduled export. When that export
runs, the request's timeline shows it. Terraform applies the change on its next
run.

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

If you can edit the sheet:

1. Edit cells inline, add rows, duplicate a row or delete one. Your edits stay
   staged in the tray at the bottom, even if you switch sheets.
2. Select **Review** to check the changes against the same rules that requests
   follow. Blocking findings, such as a hard-denied privilege or an individual
   user, must be fixed first. Warnings don't block.
3. Give a reason and commit. The whole set is saved together, or not at all.

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
   headers captured, duplicates, and rows that break current rules. Tabs that
   aren't managed sheets, such as a scratch tab, are listed as skipped.
2. Import. Rows that break current rules are imported with a finding, never
   dropped, so existing access is kept exactly.

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
  (**Two-person rule**).
- **Metastore export path**, **Export schedule**, **Also export after every
  change**, **Write a byte-order mark**, **Line ending**, and how long exported
  files are kept.
- The three approver groups and **Viewer roles**.
- **Max row changes per save**, and the minimum reason and justification
  lengths.
- **Never-granted privileges**, **System groups** nobody may change, the
  **Default owner** and **Naming rules**.
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
