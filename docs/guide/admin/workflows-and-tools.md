# Workflows, tools and knowledge

The Control Tower pages let platform and governance admins decide what the
assistant can do, which approvals each request needs, and what the assistant
knows. None of it needs code or a redeploy.

## Workflow Studio

**Workflow Studio** is where you define what happens when someone makes a
request. A workflow has two parts:

- **Details & instructions**: what the request is for and the instructions the
  assistant follows when someone asks for it, such as which details to collect.
- **Workflow graph**: the steps it runs, in order. A step is either an
  approval the request waits for, such as a manager or data owner, or an
  action, such as creating a schema or recording an access change.

Each published workflow becomes something the assistant can do. People can
then ask for it in chat.

### Create or edit a workflow

- **Build with assistant** lets you describe the workflow in plain language.
  The assistant drafts the graph, the instructions and a few test cases, and
  you edit from there. With a workflow open, the button reads **Edit with
  assistant** and works on that workflow.
- **New workflow** starts from an empty form, if you'd rather build it
  yourself.
- **Duplicate** copies an existing workflow as a new draft.

Select **Save** to keep your changes as a draft. A draft doesn't affect any
real request.

### Check it before you publish

On the **Workflow graph** tab:

- **Validate** checks the structure: that every approval type and action is
  real and every value is well formed.
- **Evaluate** scores the workflow for risk and completeness, and lists what
  to fix.
- **Test** runs a dry run against a sample request. It shows which approvals
  would be granted automatically and exactly what each step would receive,
  without running anything.

The **Tests** tab holds saved test cases you can run again whenever the
workflow changes.

### Publish, turn off and roll back

- **Publish** makes the workflow live. You see a summary of its approvals and
  the changes it can make before you confirm. **Unpublish** takes it offline.
- **Turn off** hides a workflow from the assistant without changing it, and
  **Turn on** brings it back.
- **History** lists earlier published versions. Restoring one creates a new
  draft, so nothing changes until you publish again.
- **Delete** removes the workflow.

### Move workflows between environments

Use **Export** to download workflows as a file, and **Import** in another
environment to load them. Some environments, typically production, lock
authoring: Workflow Studio shows a banner, and the only way to change
workflows there is to import ones you've built and tested elsewhere. You can
still turn individual workflows on or off.

The [Workflow administration](../../PLATFORM_ADMINISTRATION.md) reference covers
the authoring loop, versioning and environment promotion in more depth.

## Tool Registry

**Tool Registry** is the catalog of every action the assistant and workflows
can use. For each tool you choose:

- where it's available: in the main assistant chat, to the workflow authoring
  assistant, or as a building block inside workflows;
- which roles may use it;
- whose identity it runs as: the app's own service principal, or the person
  making the request.

**Sync Local Tools** refreshes the list with the tools built into
{{brand_name}}. You can also connect tools from other servers.

## Context Catalog

The **Context Catalog** holds the house rules and reference material the
assistant reads, such as internal processes and standards. Good content here
steers people toward the right choice before a request ever reaches an
approver.

- Organize content into domains. Select **Add** to create one.
- Write documents in Markdown, or upload Word, PowerPoint or PDF files.
  Published documents become searchable by the assistant.
- Use the **Test retrieval** box to search the catalog the way the assistant
  would, to check it finds the right passage.
- **Export** and **Import** move the catalog between environments.

### Keeping documents current

Every document has an **owner**, the person responsible for keeping it
accurate. It starts as whoever created the document; change it in the
**Owner (email)** box in the document editor. A document with no owner falls
back to its domain's primary owner for reminders.

- The document list shows each document's owner and how long ago it was last
  updated. Sort it by last updated, title or owner, filter it by owner (or
  **No owner**), or tick **Stale only**.
- A published document is marked **Stale** when nobody has edited it or marked
  it reviewed within the stale threshold (90 days unless changed). Only
  changes to the title, body, link or type count as an edit. The assistant
  reading a document doesn't, and neither do changes to the status or owner.
- If a stale document is still accurate, open it and select
  **Mark reviewed**. That restarts its clock without changing the content.
- Change the threshold under **Admin → Settings → Context Catalog**.

### Teams reminders

{{brand_name}} can post a weekly message to a Microsoft Teams channel that
lists the stale published documents, grouped by owner. Each owner is
@mentioned once, and each document links straight to its editor.

1. Ask your platform administrator to connect a Teams channel (see
   *Platform Administration*).
2. Select **Reminders** at the top of the Context Catalog. The panel shows
   whether a channel is connected, the schedule, the documents due a reminder,
   and the last run. Select **Send test message** to check the channel works.
3. Turn on **Send stale-document reminders** under **Admin → Settings →
   Context Catalog**, where you also set the schedule, how often the same
   document can be reminded about again, and how many documents to list per
   owner.

**Run now** in the panel sends reminders straight away, even with scheduled
reminders turned off. A document isn't reminded about again until the
re-notify interval passes, it's edited, or it's marked reviewed. For the
links to work, **App base URL** must be set under **Admin → Settings →
Notifications**.

### Keeping the catalog in Git

Your platform administrator can connect the catalog to a GitHub repository.
Each environment then keeps its catalog in its own branch, so every change
has a history and can be reviewed and promoted with pull requests. In the
repository, each domain is a folder and each document is a Markdown file
named after its title. Uploaded Word, PowerPoint and PDF files are stored as
the text {{brand_name}} extracted from them.

- **Saving in {{brand_name}}** commits the change to the environment's branch
  within a few seconds, with you as the author. If GitHub can't be reached,
  your save still goes through and is committed once the connection is back.
- **Changes merged in Git** appear in {{brand_name}} on a schedule (every 15
  minutes unless changed). Select **Sync from Git** in the **Git sync** bar
  to bring them in straight away.
- If the same document was changed in {{brand_name}} and in Git since the
  last sync, the version saved in {{brand_name}} wins. The other version stays
  in the branch's history, and the file is listed under **Recent conflicts**.
- Expand the **Git sync** bar to see the branch, the last commit, the last
  import, changes still waiting to be committed, any error, and files that
  couldn't be imported (for example because of a typo in their front matter).

Some environments only *read* a branch. Staging, for example, shows exactly
what production's branch holds, so any change made in staging itself is
replaced at the next sync.

When **Allow editing in the app** is turned off under **Admin → Settings →
Context Catalog**, the catalog becomes read-only in that environment. A banner
links to the branch, and changes are made there through pull requests.
**Sync from Git** keeps working.

## Training Studio

**Training Studio** is where you author the learning tracks and courses people
see on the **Training** page, upload their media, and see how much each one is
used. Completions from another learning system can be uploaded with
**Academy CSV** here, or under **Admin → Settings → Training**, which also
shows how many rows were skipped or failed.

### Linking courses to Databricks Academy

{{brand_name}} keeps a copy of the public Databricks Academy course catalog,
refreshed nightly. The bar at the top of Training Studio shows when it last
synced; select **Sync Academy catalog** to refresh it now.

- When adding or editing a course, search **Pick from Academy** and choose a
  course. Its title, code, link, type and duration are filled in, and the
  course is *linked*: from then on, every sync keeps the code, link, type and
  duration current. The title also follows Academy unless you change it.
- When Academy replaces a course with a new version, linked courses move to
  the new version automatically. A linked course Academy no longer lists, with
  no newer version, is marked **Retired** and keeps its last details.
- Courses that aren't linked are listed under the sync bar with a suggested
  match where one is found (same code, a newer version of the same course, or
  a closely matching title). Open the list, check each match, and select
  **Link**, or **Link all suggested**. Courses with no match can be linked by
  hand with **Pick from Academy**, or left as custom courses.
- Workflow steps that require a course accept a completion of any version or
  delivery of that course. A step written against an older course code still
  passes for someone who completed the current version.

The catalog address, sync schedule and course languages are under
**Admin → Settings → Training**.

## Preview Features

**Preview Features** lists the Databricks Beta, Public Preview and visible
Private Preview features available in your target workspaces, refreshed daily.
**Account previews** and **Workspace previews** are on separate tabs.

- Select **Request** to ask for a preview to be turned on in one or more
  workspaces, or **Request turn off** to switch one off. Both go through
  approval.
- After approval, workspace previews are turned on automatically and checked.
  Account previews, and any change that couldn't be made automatically, become
  a task for a person to complete.
- **Add feature** adds a preview that Databricks hasn't listed yet, such as one
  your account team is enabling for you.

Who approves these requests is set on the Preview Feature Request workflow in
Workflow Studio.
