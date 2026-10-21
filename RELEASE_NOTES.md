# Release notes

User-facing release notes, rendered in the app at **Account menu → Release notes**
(`/release-notes`). See "Release notes" in `AGENTS.md` for how to add an entry.

Format rules (the app parses this file, so keep to them):

- One `## <major.minor.patch> — <YYYY-MM-DD>` heading per release, newest first.
  The top entry is shown as the current version and must match `package.json`.
- Group bullets under `### Added`, `### Changed`, `### Fixed`, or a feature-area
  subheading. Plain markdown only.
- Every bullet gives the rationale: what changed, why (what was wrong or missing
  before), and what the reader will notice or should do now.
- Anything above the first `##` heading (this preamble) is not shown in the app.

## 2.4.0 — 2026-10-21

### Added

- **You can now preview a table's rows in Discover.** Before, the details panel only listed a table's columns, so checking what the data actually looked like meant opening it somewhere else. Tables and views now have a **Data preview** tab with a sample of rows, read with your own permissions so you only see data you're allowed to query. Search across every column, click a header to sort, or filter a column by text, `null` or a comparison like `>20`. The preview loads only when you open the tab. Admins set the row limit under **Data Catalog → Data preview rows** (500 by default).
- **You can pin the tables, views, metric views and dashboards you use most.** Finding the same few assets meant browsing back through domains each time. Select the pin icon on an asset card, or **Pin** in its details panel, and it appears in a **Pinned** row at the top of Discover; click it to open it, or its × to unpin. The row stays on one line and puts any extra pins under **+N**, so it doesn't push the domains down. Pins are saved to your account, so they follow you between browsers and devices. Admins can change the limit (50 by default) under **Data Catalog → Pinned assets per user**.
- **The Discover details panel can be resized.** Wide tables and long descriptions were cramped in the fixed-width panel. Drag its left edge to make it wider or narrower, or double-click the edge to reset it; the width is remembered for next time.

### Changed

- **Enterprise Data is now the first domain in Discover, and it's highlighted.** It holds the shared data every business area builds on, but it was listed sixth and looked like any other domain. Its card now comes first, with its own icon and colour and a **Shared foundation** label.
- **Step 2 in Discover is laid out in two columns.** The subdomain list used to share space with a single "view all" link. Subdomains are now on the left, and on the right one shortcut per type jumps straight to that type across the whole domain. **Legacy Dashboards** is first, since finding the replacement for an old dashboard is the most common reason to look. **Explore all** still opens everything.
- **Domains without subdomains now open directly.** A domain whose assets weren't split into subdomains used to ask you to pick from a single "General Analytics" entry. Clicking its card now goes straight to its assets, and the subdomain picker is hidden there.


- **The Lakebase change history export is now set up by the deployment instead of by hand.** Copying the app's requests, approvals and access-rule history into Unity Catalog needed someone to open the Lakebase UI and click Start in every environment, and the app's tables weren't prepared for it unless an operator had also changed a setting, so in practice nothing was being exported. The `dev`, `stage` and `prod` deployment settings now prepare the app tables, and each has a destination catalog and schema to fill in (`cdf_catalog` and `cdf_schema`). After a deploy, `scripts/ensure_cdf.sh <target>` creates the feed if there isn't one and does nothing if there is; the develop pipeline runs it for you. Until you replace the placeholder destination, nothing is created. Once it is on, the history tables are written to the destination you chose, and the platform setup guide explains the permissions it needs. The develop pipeline also installs a newer Databricks CLI, because the older one can't create the feed.

## 2.3.0 — 2026-10-21

### Added

- **Training courses can now be linked to the Databricks Academy catalog, which keeps them current.** Course codes, links and durations used to be typed in by hand and went stale whenever Academy released a new version of a course, which happens often. The old "Sync from Catalog" button scraped a web page that no longer lists courses, so it found nothing. The Academy course catalog is now copied in every night. In Training Studio, **Pick from Academy** fills in a course from a search, and a linked course's code, link, type and duration update on every sync. When Academy replaces a course with a new version, linked courses move to it automatically. Courses that aren't linked yet are listed under the sync bar with suggested matches to accept, and nothing is linked without your say-so except courses whose code already matches exactly. The schedule and course languages are under Admin → Settings → Training.
- **Workflow steps that require a course now accept any version of it.** A step written against one course code used to pass only for that exact code, so people who took the newer version of the same course, or the instructor-led instead of the self-paced one, stayed blocked until an admin stepped in. The step, and the progress shown on the Training page, now accept a completion of any version or delivery of the required course. Codes that aren't Academy codes still have to match exactly.

### Changed

- **Your requests and your approvals are now one compact, searchable page.** My Requests and Pending Approvals were separate pages of large cards, which were hard to scan once a list grew, and Pending Approvals opened with a large box explaining your approval permissions. Both are now tabs of a single table, opened from **Requests** in the top section of the sidebar, under Ask Anything, instead of a section of its own: **Needs my approval** (only if something can be assigned to you), **My requests**, and **All requests** for Platform Admins. A search box filters by request ID, title, workflow, requester, status or the names in a request, and status buttons show how many requests match each. Approvers get Approve and Reject on each row, and reports from earlier steps are still shown before you confirm. The permissions box is gone, because if you can approve something its Approve button is already there.
- **Notifications moved into the account menu.** The separate bell at the bottom of the sidebar took a full row for one link. When approvals are waiting, a red count now shows on your initials and on **Requests** in the sidebar, and **Notifications** at the top of the account menu takes you to them. **Requests** opens straight on **Needs my approval** while anything is waiting for you.
- **Access Rules columns can be resized, and the page is a little more compact.** The rules grid shows a lot of information and was hard to work with on smaller screens. Drag the border between two column headings to resize. Long values now stop at two lines, with groups and privileges side by side instead of one per line, so rows stay short; hover over a cell to see its full value. Your widths are remembered in your browser for each type of sheet. Double-click a border to reset that column, or use **Reset columns** to restore them all. Text and rows are also slightly smaller, so more rules fit on screen. The History, Exports and Import tables resize the same way.
- **The Admin page now has just three tabs: Dashboard, Settings and Feedback.** Legacy dashboard mappings and training-completion uploads are occasional setup tasks, so they now sit in Admin → Settings next to the settings they relate to: **Legacy Dashboards** under Discover & Analyze, and **Training** under Learn & Share, which also holds the new Academy catalog settings. The Test Runner tab, which was only useful to developers, is gone. Old links and bookmarks to these tabs take you to their new pages.

## 2.2.0 — 2026-10-21

### Added

- **Copy changes to other environments from Review and save, and from Edit many tables by name.** The **Also make these changes in** checkboxes used to be hidden inside the collapsed list of unsaved changes, where most people never saw them. Edit many tables by name had no way to pick environments at all, so a bulk change only touched the environment its pattern named. Both now show the choice next to the preview. Tick prd while saving dev changes and the preview updates to list the production copies, each under that environment's approval rules. The choice is cleared after each save, so a later change isn't copied by surprise.
- **Name a catalog in every environment with braces.** In Edit many tables by name, in a pasted sheet, through the API or when asking the assistant, `{sales}.bronze.*` matches `sales_dev.bronze.*`, `sales_prd.bronze.*` and every other environment's copy at once. Before, you needed one pattern per environment, or a wildcard such as `sales_*` that could also catch unrelated catalogs. It follows the catalog patterns under **Environments** in Admin → Settings, so catalogs listed there by name aren't included.

### Changed

- **An object with no counterpart in another environment no longer blocks the whole change.** When you asked for changes to be copied to prd and one object's catalog wasn't named by an environment pattern, the entire change was refused, even if every other object had a match. Now that object is changed only where it is, and the preview warns that prd was skipped for it, naming the object, or giving a count per catalog when there are several.
- **The approver groups on Access Rules now take one line instead of three cards.** The three cards at the top of the page showed each type's approver group, but they pushed the rules themselves far down the screen. They're now a single **Approvers** line: each type's group, a pencil where you can edit that type or an eye where you can only view it, and a coloured dot for whether Databricks knows the group. Hover over the dot for the detail. A group that isn't in Databricks still gets a red warning under the line, because its approvals only reach Platform Admins until it's fixed.

### Fixed

- **The assistant no longer gives up when a name isn't the kind of securable it guessed.** Asking who can reach `platinum_insights` led the assistant to look for a catalog of that name, find none, and report that nobody had access, when it was a schema in `enterprise_prd`. When a lookup finds nothing, the assistant now searches for that name as a catalog, schema, table or workspace, in every environment, and looks up the match itself, telling you which one it used. If several fit, it asks which you meant, and if the name only exists in another environment it says so.
- **The assistant talks about access in plainer words.** Answers about who has access ended with a note that they were the "desired-state ledger, not a live check of current grants", which meant little to most people. The assistant now calls them the access rules and leaves out the caveat. If you ask whether access is live right now, it explains that approved changes reach Databricks at the next scheduled update.

## 2.1.0 — 2026-10-20

### Added

- **Browse and bulk-edit tags by dataset again.** The old Tag Manager let you pick a dataset and change the tags on all of its tables at once, and that went away when the Metadata Manager was rebuilt. A **Catalogs | Datasets** switch now sits at the top of **Browse**. **Datasets** lists every dataset, meaning the tables and views tagged with the same `dataset` (or `data_set`) value, along with its table count, its catalogs and whether any of its tables are out of sync. Open a dataset to see each of its tags with how many tables carry it ("On all 12" or "On 7 of 12"). From there, **Add to the other 5** fills the gaps, **Remove from all** takes a tag off, and **Add or change a tag on every table** sets one value everywhere. The changes join your unsaved changes and go through **Review and save** like any other edit, approvals included. Tables named only in a data contract, and not tagged, aren't listed.

### Changed

- **The Metadata Manager has been redesigned around finding a table and changing it.** The old page put a side panel of filter switches, five technical tabs and a tray of "staged edits" in front of you, so a first-time visitor had to learn words like drift, declare and reconciler before changing a single tag. Now it opens on a plain list of catalogs with how many tables each holds; click into a catalog, then a schema, then a table, or search from wherever you are. Each table has its own page, with an address you can share, where you edit the description and tags right where they're shown, column by column too. A bar at the bottom counts your unsaved changes until you choose **Review and save**, which works exactly as before, approvals included. A short "How this works" card explains the steps the first time and can be dismissed.
- **Fewer controls on screen, with the rest folded away.** Filters now sit behind one **Filters** button, rarer actions such as editing many tables by name or preparing tags for a table that doesn't exist yet are under **More**, and each table's owners, other environments, sync status and change activity are in sections that start collapsed. Lists show a status only when something needs attention, such as *Unsaved changes*, *Waiting to sync*, *Out of sync* or *Couldn't update Unity Catalog*. Lists now include every object Unity Catalog reports by default, not only the ones already edited here.
- **History shows your own changes first, and admin tools have their own page.** **History** opens on the changes you saved, with a switch to see everyone's. Platform Admins get an **Admin** link that gathers sync status, objects out of sync with Unity Catalog, sync history, automation access and the bulk onboarding tools, which used to be spread across tabs everyone saw. Old links to the Changes, Drift, Reconciler runs and System principals tabs open the matching new page.
- **Plainer words throughout the Metadata Manager.** "Drift" is now *out of sync with Unity Catalog*, reconciler runs are *sync history*, plan and enforce mode read as *Report only* and *Keeping Unity Catalog in sync*, adopting is *start managing*, and system principals are *automation access*. The settings themselves keep their names, so nothing needs reconfiguring.
- **The Metadata Manager no longer tags catalogs with the deployment that manages them.** Under Enforce, each deployment used to write a marker tag on every catalog it managed and skip catalogs another deployment had marked. Only one production deployment manages the governed catalogs, so the marker added a write to every catalog without protecting anything. **Catalogs to reconcile** is now the only boundary: keep each deployment's list separate. The **Claim catalogs**, **Claim tag key** and **Instance id** settings are gone from **Admin → Settings**, and values saved earlier are ignored. A marker tag already on a catalog is left in place unless that catalog is managed here, in which case Enforce removes it like any other undeclared tag.

### Fixed

- **The Metadata Manager now sees every table in a catalog its service principal can browse.** The full check read each catalog in a way that needs the USE CATALOG permission, so with only BROWSE (all that data certification needs) the check failed for that catalog every time. The list then showed just the few tables created or changed since the app started. The full check now reads the same metadata views as data certification, so BROWSE is enough. After upgrading, choose **Check and sync now** under **Admin → Sync history** once (or wait for the next hourly check), and the whole catalog appears.
- **Very large catalogs no longer fail the Metadata Manager's full check.** The check asked for a whole catalog's tables, columns and tags in one answer, and a SQL warehouse caps an answer at 25 MB, so a catalog with tens of thousands of columns failed every time with "Inline byte limit exceeded", and its tables never showed up or stayed out of date. When an answer is too big, the check now asks again for half the schemas at a time, and then for half of a schema's tables at a time, until each part fits. Very long nested column types are also stored as their short type name, such as STRUCT, to keep each answer smaller. Large catalogs take a few more queries to check, and nothing needs to change in settings.
- **A catalog's page lists its schemas even when the full check couldn't read it.** When a catalog's full check failed, the Metadata Manager still picked up its new and changed tables, but not the schemas they sit in. The catalog showed up on the catalog list with a table count, yet clicking into it said "No schemas here", which looked like the *Tables and views* filter was hiding them. Schemas that hold known tables are now always listed, and the check for new tables brings in their schema and catalog too. A catalog the last full check couldn't read now carries a warning that its list may be incomplete. For admins, the warning links to **Sync history**, where the failure is shown. In **Filters**, the choice between tables, columns and everything is now labelled *When you search, look for*, because it only affects searches, never the list you browse.
- **The review step no longer treats a table's existing description and tags as your edits.** The first time you change a table in the Metadata Manager, it records everything the table already has, so that later syncs keep it. The review step listed those values as edits of their own, so adding one tag could read as "Save 3 edits", and every such table was wrongly badged "Not created yet". Now the table is marked *Starts being managed*, the values it keeps are listed as *Already in Unity Catalog*, and the save button counts only what you changed. Those kept values also stopped blocking saves. A kept sensitive tag, such as an approver group, used to need approval, and a kept value that broke a tag rule, such as `reliability_window=7`, made the whole change impossible to submit. Kept values now need no approval, and a rule they break shows as a warning, so you can still fix it later. Your own edits are checked and approved exactly as before, and a table's kept values wait with any of its edits that need approval.
- **Sync runs cut off by a restart no longer show as running forever.** If the app restarted while a check or sync was in progress, **Sync history** showed that run as *Running* indefinitely. The next run now closes it as failed, with a note that the app restarted. A run older than six hours that never finished is closed the same way. Later runs were never affected, so nothing needs to be redone.
- **The reason field says "optional" when no minimum is set.** It used to read "required, at least 0 characters".

## 2.0.0 — 2026-10-20

### Metadata Manager

- **The Metadata Manager now keeps the list of correct tags and descriptions itself.** Before, it read tags from Unity Catalog on every search and wrote changes either straight to Unity Catalog or as pull requests to a governance repository. Nothing kept a record of what each object *should* carry, so a tag removed by hand went unnoticed. Now the platform keeps one list of the tags and descriptions every catalog, schema, table, view and column should have, with who set each tag, when and from where. A background reconciler makes Unity Catalog match it. Admins choose under **Admin → Settings → Metadata Manager → Reconcile mode** whether it only reports differences (**Plan**, the default) or also corrects them (**Enforce**). Under Enforce, tags added directly in Databricks are removed unless they're added to the list or their key is exempt, so review the **Drift** tab before switching.
- **Adopt, then plan, then enforce.** The first edit to an object that isn't on the list yet becomes its whole desired state, so under Enforce any other tag it carries in Unity Catalog would be removed. Previews warn when that's about to happen. Start by choosing **Adopt current state** on the **Objects** tab for the catalogs or schemas you work with, which copies what Unity Catalog holds today onto the list. Then choose **Run a plan** on the **Reconciler runs** tab to see what enforcing would set and remove, before switching the mode. A run that would remove more than 200 tags (counted per batch of 200 saved objects, or per full check) applies nothing and alerts governance instead.
- **A faster, redesigned Metadata Manager page.** Search answered from Unity Catalog, so tag filters took seconds and the page couldn't show who had set a tag. Search now answers instantly from the platform's own list, with filters for catalog, tag, `!key` (tag missing) and **Editable by me**. Opening an object shows the tags it should have next to the ones Unity Catalog holds, with each tag's author and history. New tabs show every **Change** (which can be undone), **Drift** and what each **Reconciler run** did. Edits are collected in a tray and reviewed together before you submit them.
- **Tag hundreds or thousands of tables in one change.** Tagging a whole schema, or applying a spreadsheet of tags agreed in a meeting, used to mean editing each table on its own. **Bulk edit** on the **Objects** tab now takes a name pattern such as `sales_dev.bronze.*`, optionally with a column name such as `*_email`, and adds or removes tags or sets a description on every matching object. You can also paste rows straight from Google Sheets or Excel: one column for the table name and one column per tag key. Preview first shows how many objects each pattern matched and what will change, and the usual owner and approval rules apply to every object. One change can now cover up to 5,000 objects (up from 500), and admins can change the limit under **Admin → Settings → Metadata Manager → Limits**. The assistant, the API and the MCP tools accept the same patterns.
- **Warnings when a tag change affects who can see data.** Column masks and row filters choose their targets by tag, so changing such a tag quietly changes who sees what. The review step now reads the policies on the changed objects and warns when a tag they key on is set, changed or removed, and the risk score rises with it. Reading the policies needs the READ METADATA permission for the governance service principal; for a change to more than 50 tables only the catalog and schema policies are read, and the warning says so. Admins can turn the check off under **Admin → Settings → Metadata Manager → Tag policy**.
- **Tags for tables that don't exist yet.** A pipeline had to create a table before its tags could be set, so new tables went untagged until someone noticed. Tags and descriptions can now be declared for a table by name in advance. They're applied within a minute or so of the table being created.
- **Owners edit their own data.** Only admins could change tags before. Now members of the groups named in an object's owner tag (`data_owner` by default, inherited from the table, schema or catalog when the object has none) can edit it, and anyone else can propose a change that goes to the owners. Owners' changes are saved directly unless they're sensitive: changing the owner tag or an approver or access tag, lowering a classification or removing a PII flag needs approval. Which keys are sensitive, and which values each key allows, are set per key under **Admin → Settings → Metadata Manager → Key policies**. So that owners can find it, **Metadata Manager** now shows in the sidebar for everyone rather than only for admins; a Platform Admin can limit it with **Browse roles**.
- **Each deployment only changes the catalogs it's given.** Dev and stage deployments share the metastore with production, and nothing stopped one from writing to production's catalogs or taking over new tables there. **Catalogs to reconcile**, under **Admin → Settings → Metadata Manager**, now lists the only catalogs a deployment changes, adopts new tables in or accepts edits for, by name or pattern such as `*_dev` (`*` means every catalog). An edit to any other catalog is refused at preview, and a bulk edit leaves those matches out and says how many. With the list blank, a deployment reports differences but changes nothing, and **Enforce** waits until the list is filled in, so production deployments moving to enforce need to list their catalogs (or `*`).
- **The removal limit also covers descriptions, and can be zero.** The safety limit counted only removed tags, so a run could clear any number of descriptions without pausing. **Max removals per run** now counts cleared descriptions too. Setting it to 0 makes every removal wait for review on the **Reconciler runs** tab, which is a safe way to start enforcing in a lower environment: tags and descriptions are added, and nothing is taken away until you resume the run.
- **One deployment manages every environment.** Each deployment used to apply tags to its own environment through its own repository branch. Now the production deployment manages dev, stage and production catalogs, mapped under **Admin → Settings → Metadata Manager → Environments**. Test a change on the dev object, then use **Copy to another environment** to bring it to production, or apply one change to several environments at once. Dev and stage deployments only report differences, so they never undo production's work.
- **Change tags from the assistant, pipelines and other systems.** You can ask the assistant to tag or describe a table. It looks the object up, shows what will change and who has to approve, and saves the change or files the approval once you confirm. Pipelines and external systems can use the new Metadata API and MCP tools, signing in as a Databricks service principal that a Platform Admin registers on the **System principals** tab with the names and keys it may change. The admin Guide's new **Metadata API and MCP** page has the details.

### Removed

- **GitOps mode for tags, and the tag-change workflow.** The settings for the governance repository, branch, migrations path and apply ledger are gone, and the tag-change workflow that opened pull requests no longer appears in the assistant or the request catalog. Requests filed under it keep their history and still open from **My Requests**, but any that were waiting won't go further; re-submit them in the Metadata Manager. The previous Metadata Manager screen, which searched and edited Unity Catalog directly, is replaced by the new page at the same address. The GitHub token no longer needs write access to a governance repository, so admins can narrow it to what the guide on GitHub token permissions lists. Admins moving over should follow the cutover runbook, which adopts what Unity Catalog holds today as the starting list so nothing is removed by surprise.

## 1.11.0 — 2026-10-19

### Added

- **Filter Pending Approvals by workflow.** Approvers who handle several kinds of request had to scroll through one long mixed list to find the ones they wanted. A new **Workflow** list at the top of **Approvals** names each workflow with how many requests are waiting. Pick one and both the pending and completed lists show only that workflow, and **Select all** only picks the requests you can see. The choice is kept in the page's web address, so it survives a reload and you can share a link to the same view.
- **Dropdowns when editing access rules.** Every cell on the Access Rules page used to be free text, so a typo or a privilege that isn't allowed only showed up at review. Environment, securable type and principal type are now picked from a list, and privileges are a checklist of what the allow list permits for the row's securable type. Other columns suggest names already used on the sheet and still accept new ones. Values already on a row that aren't in a list stay as they are, and a change that puts a value outside its list is marked in red.
- **Lists of workspaces and catalogs for each environment.** Platform Admins can now fill in the **Workspaces** and **Catalogs** columns of **Environments** under **Admin → Settings → Access Rules**, so the Access Rules page offers those names as choices. Requests through the assistant can then only name a listed workspace or catalog, and a direct edit outside the list gets a warning at review. Leave a column empty to keep typing names freely, as before.
- **Send the app's logs to Unity Catalog.** For operators: the app's logs used to be visible only in its own log view. Each environment's deployment settings now include a log destination, and once its catalog and schema are filled in, the app's logs and metrics are written to tables there. A separate deployment setting prepares chosen app tables for Lakebase Change Data Feed, so requests, approvals, the enforcement audit and access-rule history can be copied into Unity Catalog. Both are off until an operator turns them on, and the platform setup guide walks through each one.

### Changed

- **Access rules use an allow list of privileges.** The never-granted list only blocked the privileges named on it, so anything else could be requested, including privileges Databricks adds later. Now a request or a direct edit can add only the privileges a Platform Admin lists for each securable type under **Admin → Settings → Access Rules → Allowed privileges**. The starting list is every privilege that was allowed before, so nothing changes until you edit it, and a new privilege can't be granted until someone adds it. Existing rules stay as they are and removing access is always allowed; if you had customised the never-granted list, check the new allow list, because that customisation isn't carried over.
- **The access-rule approval step names its approver group.** The approval card and the workflow editor used to say only "Platform Admin", which suggested no group was involved even when one was set. The card now says which group approves, for example "UC securables approvers: grp_data_stewards", or "Platform Admins (no UC securables group set)" when that type has no group, and the requester's own request page says the same. The step is labelled **Access rule approvers** in the request's timeline and in the workflow editor, which also lists the three groups with a link to their settings, and the card no longer repeats the prepared change as raw data, since its report already shows it. Who can approve hasn't changed.
- **Access-rule requests now go through Prepare, Approve and Record.** The steps used to be called Plan and Apply, which was easy to confuse with Terraform, although nothing reaches Terraform at these steps. Prepare works out the exact rule changes and who approves them; Record writes the approved change into the access rules, and the next export carries it to Terraform. Requests already in progress finish normally.

### Fixed

- **Import findings give the row number you see in Excel.** Findings counted from the first data row and skipped blank rows, so every number was at least one short of the row in the workbook. They now count the header as row 1 and include blank rows, so you can go straight to the row they name.
- **Account groups work as approver groups.** An approver group that exists in your Databricks account but isn't assigned to this workspace, as Unity Catalog groups often aren't, showed as "Not found in Databricks". Its members also weren't recognised, so they couldn't approve or edit that type, and the warning that approvals only reached Platform Admins was right. Now such a group shows **Found in the account**, and the app reads each person's account groups when they sign in, so members get the approvals and edit rights. A group mapped to an app role under role mappings now applies to its members in the same way, even when it isn't assigned to the workspace. "Not found" now means no group in the workspace or the account has that name.

## 1.10.0 — 2026-10-16

### Added

- **Access Rules replaces the RBAC workbook.** A new **Access Rules** page in Watch Tower holds which groups have which privileges on Unity Catalog securables, the metastore and workspace items, sheet by sheet, exactly as the workbook laid them out. Until now, access changes meant editing a shared spreadsheet by hand, marking additions in red, and exporting each sheet to CSV for the Terraform pipeline. Now every change is checked against the same rules, recorded with who made it and why, and can be reverted from the **History** tab. A Platform Admin imports the workbook once to seed the page. The import copies it exactly as it stands, without checking existing access against the rules, because the workbook is the source of truth; the rules apply to changes made afterwards. After that the workbook is retired and nobody needs to edit it.
- **Access rule CSVs export on a schedule.** The app writes one CSV per sheet, named after the sheet and laid out like a manual export from Excel, to a folder you set for each environment. The Terraform pipeline reads them without change. Exports run on the schedule set under **Admin → Settings → Access Rules**, optionally after every change, or on demand with **Export now**. To prove the files match before you switch the pipeline over, **Compare with a manual export** on the **Exports** tab checks a hand-exported CSV against the app's and explains any difference.
- **Ask the assistant to give a group access.** Name the group or service principal, what it needs and why, in any environment, and the assistant checks whether it already has that access, shows the planned change (adding prerequisites such as `USE_SCHEMA` for you), and files the request once you confirm. Approval records the change straight away, and the next export carries it to Terraform. An ask that spans workspace and data access becomes linked requests, one per type, because each type has its own approvers.
- **A Databricks group approves each type of access rule.** UC securables, the metastore and workspace securables each have an approver group setting under **Admin → Settings → Access Rules**. Members of that group see and approve its requests in **Approvals**, and can edit its rules directly on the Access Rules page, with no role mapping to add. A type with no group set falls back to Platform Admins, and the page warns when a group is unset or can't be found. **Edit & Restart** isn't offered on these requests, because what gets approved must be exactly what was asked for; to change one, reject it and file a new one.
- **Approve several requests at once.** In **Approvals**, tick the requests you want and select **Approve selected**. You confirm the list, then see the result for each one; any that can't be approved say why and the rest go ahead. Clearing a queue used to take a confirmation per request. Rejecting is still one at a time, because each rejection needs a reason.

### Changed

- **You can no longer approve your own request.** This applies to every workflow and to Platform Admins too; another approver must approve it. Before, a requester who was also an approver could sign off their own change. You can still reject your own request to withdraw it. Admins can turn this off under **Admin → Settings → Requests & Approvals → Approvals** with **Block approving your own request**.
- **Data access is now given to groups, not to individual people.** The workflows that granted Unity Catalog access live, to a person or a group, have been removed: data access request, batch data access, and catalog, schema and table access. Those live grants were undone by the next Terraform run, since Terraform owns access, so the record and Databricks disagreed. If you ask for access for yourself, the assistant now tells you which groups already have it so you can ask to join one. Requests made under the removed workflows keep their history and still open from **My Requests**, but any that were still waiting won't go further.

## 1.9.0 — 2026-10-15

### Added

- **Suggested next steps in chat.** After each reply, the assistant now offers two to four next steps as buttons, such as "Request access to ws-supplychain-prod" or "Find the table owner". When it asks you to choose, the options become the buttons, so you can answer in one click instead of typing. Clicking a button sends its text as your message, so the assistant still asks you to confirm before it submits anything. Admins can turn this off under **Admin → Settings → Features & Navigation** with **Suggest next steps**.
- **Duplicate request check.** Before submitting a request, the assistant now checks for open or recently completed requests for the same resource. Duplicates used to reach approvers, who had to spot and reject them by hand, and you could wait on a request that someone else had already filed. If you already have a matching request, the assistant links it. If a colleague does, it tells you one exists and its status without naming them. Either way, nothing is submitted until you choose to submit anyway, wait, or change the request. Admins can turn the check off, and choose how far back it looks and which statuses count, under **Admin → Settings → Agent** (Duplicate requests).
- **A built-in Guide.** A new **Guide**, in the sidebar and the account menu, explains how to ask the assistant, find data, make and track requests, and handle approvals, with a separate section for admins. Until now, the only help was scattered across release notes and word of mouth. The Guide ships with the app, so it always describes the version you're using. You can search it and share a link to any section. **My Requests**, **Approvals** and **Workflow Studio** link straight to the relevant section. Admins can hide it under **Admin → Settings → Features & Navigation**.

### Changed

- **The assistant clarifies before it starts a request.** When a request could mean more than one thing, or the assistant finds that what you asked for already exists, it now tells you what it found and asks which option you want. Before, it often picked a workflow straight away and listed every detail it needed, even when it had just found that you might not need a new resource at all. For example, asking for "a new domain called supply chain" when a supply chain workspace already exists now gets you a choice: access to that workspace, a new environment for it, or a catalog for its data. It asks for the details only once you've picked.
- **Workspace lookups are much faster.** The assistant's list of the account's workspaces, for example when it checks whether a workspace already exists, now comes from a copy that the background catalog sync keeps up to date. Each lookup used to run a live query on a SQL warehouse, which could take a minute or more when the warehouse was asleep. The assistant still fetches the latest list when it needs changes made since the last sync, or when you ask for it. Admins can turn the cached list off or set how old it may get under **Admin → Settings → Data Catalog**.
- Listing your target workspaces, in the assistant and in the OmniGuard and Preview Features pickers, no longer looks up each workspace's credentials, so the list now appears straight away.

### Fixed

- Opening a subdomain on **Discover** now takes you to the top of its metric views. Before, the page kept the scroll position from the domain picker, so you landed partway down the list. Switching domain or subdomain and opening a search result also start at the top. Going back, with the browser's Back button or by clearing the subdomain, returns you to where you were in the previous list, including the page of results you were on.
- The assistant panel on **Discover** no longer drags the whole page down while it replies. It now scrolls only its own conversation.
- **Read the authoring guide** in Workflow Studio opened the Context Catalog. It now opens the workflow authoring section of the Guide.
- **Metadata Manager** no longer fails to load its dataset list when a single data contract has an empty or malformed table entry, such as a stray `-` under `schema`. One bad contract used to break the list for every dataset. Malformed entries are now skipped and the rest of the contract is read as usual. OmniGuard data certification, the contract table check and contract drafting skip those entries the same way. Before, they could miss the contract's tables. Fixing the contract is still worthwhile, because a table listed only in a malformed entry isn't picked up.

## 1.8.0 — 2026-10-13

### Added

- Preview feature requests now notify different people for **workspace** vs **account** previews. Set the addresses under **Admin → Settings → Preview Features** (Approvals). When a request is opened, those people are emailed and assigned the approval; Platform Admins can still act. Blank keeps the previous inbox-only behaviour.

### Fixed

- You can request a preview **on** in workspaces that don't list it yet, even when another workspace already has it on. That used to hide the Request action and leave only **Request turn off**, which blocked Private Preview enrollment in the remaining workspaces.

## 1.7.0 — 2026-10-12

### Added

- Platform Admins can delete a single OmniGuard run from **Run History** with the trash button on its row. Use it to clear the latest run, or to unstick a run that stays in progress, for example after the app was redeployed in the middle of a scan. A run that is still scanning stops before its next workspace or before data certification, throws its results away and applies no remediations, so you can start a fresh scan right after. **Clear old runs** is still there for bulk clean-up.

### Fixed

- A long data certification pass, for example while the SQL warehouse is slow, no longer fails outright if its database connection drops partway through. Queries the scan gives up waiting for are now cancelled rather than left running, so a slow warehouse no longer gets slower with every scan.
- When the assistant hits its tool budget — for example after repeating the same internal-guidance search many times — it now answers from the results it already has instead of stopping with a "maximum processing limit" note. Repeated identical lookups are skipped, and knowledge lookups are capped per turn (configurable under **Admin → Settings → Agent**).
- **Build with assistant** in Workflow Studio no longer jumps you onto a blank new workflow. If a workflow is already open, the assistant stays on that one (same as the floating **Assistant** button). From the list, it stays on the list so you can describe a new workflow or name an existing one to edit; a save or preview still opens the editor.
- The authoring assistant no longer stops to ask whether a published workflow should be taken offline before it saves a draft. Environments where that would matter already lock workflow editing; where editing is allowed, it saves and continues. Publishing still needs an explicit go-ahead.

## 1.6.1 — 2026-10-09

### Fixed

- Data certification now works when **Data certification workspace** names a workspace other than the app's own. The scan uses that workspace's service principal but runs its queries in the app's own workspace, where the configured SQL warehouse lives. Previously every metadata read failed with "warehouse not found", so no table in the certified catalogs could be checked.

### Changed

- OmniGuard run reports now count violations the same way everywhere. The violation total, the severity cards, the run history's **Found** column and the tabs above the list all count one violation per resource and policy, which is one row of the list. Previously the totals counted every failed rule, so a data product failing three checks counted as three, and the total didn't match the list. The individual rules are still itemised in the **Checklist** view. Runs from before this change keep their original totals in the run history, so they can read higher than newer runs.
- Passing data products waiting to be certified, and findings covered by an approved exception, now appear in their own **ready to certify** and **approved exceptions** tabs instead of in **All**, so **All** only lists open violations.

## 1.6.0 — 2026-10-08

### Added

- Data catalog assets now display their accountable human or team data owner based on the governed `data_owner` tag instead of the raw service principal or system identity. Administrators can configure or customize the owner tag name in **Admin → Settings → Data Catalog**; if the setting is blank or the tag is not set on an asset, ownership seamlessly falls back to the direct object owner.

### Fixed

- Asking the agent for access to a catalog object no longer fails with “you don't have USE CATALOG” when you can already see that catalog. Owner and access-group lookup now uses Unity Catalog metadata the platform can already browse, instead of a live API that required a grant the app itself doesn't have.
- Data certification no longer reports every table in a catalog as "not found in Unity Catalog" when reading the catalog's metadata takes longer than 50 seconds. The scan now waits for slow metadata reads to finish. If a read does fail, the finding says the metadata couldn't be read and includes the error, instead of suggesting the table is missing or a permission is absent, and the product's certified status is left as it was rather than shown as uncertified.

## 1.5.0 — 2026-10-06

### Metadata Manager

- Tag Management is now called **Metadata Manager**, because it now edits more than tags. Old links to the page still work.
- You can now tag catalogs and schemas, not just tables and views. Search finds them by name or by tag and lists them in their own group, and they go through the same policy, typo, access-policy and risk checks before anything is applied. Tags on a catalog or schema are inherited by everything inside it, so the AI review calls that out.
- You can now edit the description of a catalog or schema. Open its row, change the **Description** box, and review the before and after with the rest of the change. Clearing the box removes the description.
- New **Rename a tag key everywhere** tool: enter the old and new key (for example, `domains` to `domain`), and every catalog, schema, table, view and column carrying the old key is added to your list with the key renamed and its value kept. Anything that already has the new key with a different value is left for you to sort out by hand. Nothing is applied until you review and run the checks.
- The bulk bar above your list can also rename a key on just the objects your filter shows.
- Catalog and schema changes need Local Execution Mode. When changes go through pull requests, catalogs and schemas can be viewed but not edited, and column uses of a key are left out of a rename.

## 1.4.0 — 2026-10-05

### Preview Features

- You can now add a preview that Databricks hasn't announced or listed yet, such as one your account team is turning on for you. Select **Add feature**, give it a name, a description, whether it's turned on per workspace or for the account, and its phase. It's requested and approved like any other preview. Because there's no setting to switch yet, a person arranges the change, and completing the Implement task records it as done. Hand-added features are marked **Added by hand**, and you can rename, re-describe or remove them from the feature's details.
- When Databricks starts listing the real setting, select **Match** on the hand-added feature and pick the setting it became. Likely matches are suggested, and the row shows **Setting may have appeared** when one turns up after the feature was added. Matching moves open requests, approvals and implemented statuses over to the real setting, so it can be switched and checked automatically from then on, and the hand-added entry is removed.
- The agent can also find and request hand-added features, and explains that a person arranges the change with Databricks.
- The Preview Features page now handles any number of target workspaces. With up to six workspaces you still get one status column per workspace. With more, the table shows one summary per feature, such as "3 Requested, 12 Already on, 85 Not requested", with the most urgent statuses first. Hover a count to see which workspaces it includes, and expand a feature to search its status in every workspace.
- Narrow the Workspace previews tab to the workspaces you care about: filter by environment, or type part of a workspace name. Pick a single workspace to see just its column.
- When you request a preview, you can now search the workspace list, see how many workspaces you've selected, and select every eligible workspace that matches your search.
- If many workspaces fail to sync, the warning names the first few and keeps the full error list behind **Show errors**.
- Account previews is now the first tab.
- Each feature's phase (Beta, Public Preview or Private Preview) now has its own column, so the list is easier to scan.

## 1.3.0 — 2026-10-02

### Preview Features

- A new **Preview Features** page (under Control Tower) lists the Databricks Beta, Public Preview and visible Private Preview features available in each of your target workspaces, refreshed daily. Workspace-level and account-level previews are on separate tabs, and features that become generally available drop off the list automatically.
- Each feature shows its description, a link to its documentation when one can be found (marked "suggested" when it was matched by name), and a link to the release note that announced it. You can set your own docs link if the one found is wrong.
- Every workspace has its own status for each feature: not requested, requested, approved or implemented. The page also shows features that are already on, and flags any that were implemented but have since been turned off.
- Select **Request**, choose one or more workspaces and add a justification to start a request. One approval covers every workspace in the request. After approval, the app turns workspace previews on itself and checks that the change took effect. Account-level previews, and any workspace where the automatic change fails, go to an Implement task for a person to complete.
- **Request turn off** turns a preview off through the same approval process.
- If you can approve a request, an **Approve** button appears on the feature's row, so you don't have to leave the page.
- You can also ask the agent to turn a preview on or off. It looks the feature up, explains what it does, its phase and where it's already on, checks it's the right feature (and suggests a better fit if there is one), and confirms the details with you before submitting the request.
- Approvers and implementers are set on the Preview Feature Request workflow in Workflow Studio. The sync schedule and sources are under Admin → Settings → Preview Features.

## 1.2.0 — 2026-10-01

### Tag Management

- You can now tag any table or view in the governed catalogs, not just tables that belong to a dataset. A single search box lists every governed dataset when you click it and finds tables and views as you type. Tick as many results as you like: a ticked dataset adds all of its tables, a ticked table adds just that one, and both can be edited and reviewed together in one change.
- Column tags are now supported. Open a table's **Columns** to see each column's tags and edit them, for example to mark a column as restricted. Column changes go through the same policy, typo and risk checks as table tags. They can be applied when changes are applied directly; when changes go through pull requests, column tags are shown read-only.
- The search box also finds objects by their tags: type `main.sales.*` for a whole schema, `classification=restricted` for objects with that value, or `!data_owner` for objects missing a tag. Press Enter to add every match.
- Filter the list by name or tag, then set or remove one tag on everything shown in a single step. Tables with many columns offer the same filter and bulk edit for their columns. Each row shows its tags at a glance, with new, changed and removed tags highlighted, and you can show only what you've changed.
- Tags governed by a Unity Catalog tag policy are now checked before anything is applied. If a policy limits a tag to certain values, the editor suggests those values and flags any other value as you type, and the review blocks the change instead of letting it fail partway through.
- Tag Management is now split into two tabs: **Edit Tags** for making changes and **Change History** for reviewing past changes, which now shows who submitted each one. Your edits are kept while you switch between them.
- The key field now suggests Unity Catalog governed tags as you type, showing how many values each one allows.
- If a column mask or row filter depends on a tag you're changing, the review now names that access policy and raises the risk score, because the change can affect who can see the data.
- Tables whose edit would add them to, move them between, or take them out of a dataset are now clearly marked.
- Tags on materialized views and streaming tables, and on their columns, are now applied with the statements those objects require.
- Lowering or removing a classification or PII tag now raises the risk score, since access and masking rules often depend on those tags. Tags meant for tables, such as `dataset` or `data_owner`, can no longer be set on columns by mistake.

### Fixed

- Changes to certified tables are now counted in the risk score as intended.
- When changes go through pull requests, the review now shows the exact SQL that will be committed.
- Table and column names are now validated and quoted before any SQL is run, so names with special characters can't produce unsafe statements.
- Trying to change the `system.certification_status` tag, which is set automatically by certification, now explains why it isn't allowed instead of producing an empty change. Other `system.` tags can now be viewed and edited like any other tag.

## 1.1.0 — 2026-09-30

### Added

- A new **Features & Navigation** page in **Admin → Settings** lists every capability in one place, grouped like the sidebar. Each capability has a single switch, and the sidebar pages it owns can be shown or hidden underneath it. Any new capability appears automatically under **Other**.
- The **Admin Dashboard** has been redesigned around what needs action. It now shows charts of requests over time, by status, by type, and median time to complete, plus a **Needs attention** panel that lists stuck requests, the latest failures, and the oldest requests awaiting approval.
- Every number and chart on the Admin Dashboard is clickable. Selecting a card, chart bar, status slice, or requester filters the request list below, and each active filter appears as a chip you can clear. Click any request to open its full details.
- A time range selector (7, 30, or 90 days, or all time) applies to the whole dashboard. Open requests are always included so older work that is still waiting doesn't disappear. The range and filters are kept in the page address, so you can share a filtered view.

### Changed

- The Admin Dashboard now hides automated runs, such as scheduled policy scans and scheduled reports, by default so people's requests aren't buried. A switch shows them again, and the dashboard always says how many are hidden.
- The dashboard's stuck-request count now works for requests waiting on an approval, and all times are shown in your local time zone. The "Active Workspaces" and estimated "Labor Saved" figures have been removed because they didn't reflect real activity.
- **Admin** is now the first link in the **Control Tower** section of the sidebar.
- **Admin → Settings** is now organized into the same sections as the sidebar (General, Discover & Analyze, Requests & Approvals, Learn & Share, Watch Tower, Control Tower, Platform), with sub-headings inside longer pages. The system banner now lives under **Appearance**, and each schedule sits with the feature it runs.
- The Event Calendar's feed URL can now be changed under **Admin → Settings → Calendar**, next to its sync schedule, without a redeploy.
- Settings for a capability that is switched off are collapsed under **Inactive settings** with a link to turn it on. A page whose capability is off is marked **Off** in the settings menu.
- Turning a capability off now also hides its pages from the sidebar, so what people see always matches what is enabled.
- The optional grid of quick-action cards on the home page has been removed, along with the switch between it and the chat. The home page now always opens straight into the chat.
- The Algolia option for documentation search has been removed. Documentation lookups now always use the built-in search.
- The Genie link and default Genie space settings are no longer editable in Settings.

## 1.0.0 — 2026-09-30

The first stable release. It brings a conversational agent, governed requests and
approvals, data discovery, governance tooling, and no-code building blocks together
in one place.

### Agent and requests

- Ask the agent in plain language to find data, answer questions about your data, or request access and resources. Before it changes anything, it shows you its plan and asks you to confirm.
- Answers about your data appear directly in the chat, with tables and charts rendered inline, and your conversations are saved so you can pick up where you left off.
- Every request is tracked under **My Requests**, with its live status, step-by-step progress, and a full history of what was done on your behalf.
- Long-running work continues in the background and resumes safely after an interruption, so a request is never applied twice.

### Discover and analyze

- Browse business domains, search the catalog, discover metric views, and explore data lineage from the **Discover** page.
- Every domain, subdomain, and asset on **Discover** has its own link. Use **Copy link** to share exactly what you're looking at, and anyone who opens it lands on the same view.
- **Reports** lets you manage recurring reports that the agent generates automatically.

### Approvals

- Higher-impact changes, such as granting access or creating infrastructure, are routed to the right approvers before they run.
- Approvers work from **Pending Approvals**, where each approval card includes a summary of the work completed so far, and can set up out-of-office delegations so nothing stalls while they are away.

### Governance

- Every action the agent takes is checked against policy and recorded in an audit trail, and the agent never has more access to data than the person asking.
- **OmniGuard** scans workspaces for policy violations and reports findings you can act on, while the **Allowlist** records approved exceptions it should leave alone.
- **Data Certification (ODCS)** tracks which datasets meet your standards for quality, documentation, access control, and classification.
- **Data Products (ODPS)** catalogs your logical data products and their contracts, and **Tag Management** keeps governance tags on data assets consistent.
- The **Context Catalog** holds the house rules and reference material the agent reads, so it steers people toward compliant choices before a request reaches an approver.

### Build and customize

- **Workflow Studio** lets administrators design request workflows visually, choosing the instructions, allowed actions, and approval rules without writing code.
- The **Tool Registry** shows every action that workflows and the agent are allowed to take, and who each one is available to.
- **Training Studio** is where administrators author learning tracks and courses, upload media, and see how the content is being used.

### Learn and share

- **Training** offers structured learning paths, and the **Event Calendar** lists upcoming sessions.
- **Templates & Assets** is a shared library of design patterns, templates, and reusable components, and **Community Links** collects the resources and tools your organization recommends.

### Administration

- Administrators can change the app's name, logo, colors, and enabled features, and adjust runtime settings live from **Admin → Settings** without a redeploy.
- Anyone can send feedback or clear their saved data from the account menu, and this **Release notes** page shows what has changed in each version.
