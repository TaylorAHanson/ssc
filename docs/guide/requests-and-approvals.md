# Requests and approvals

When you ask the assistant for access or a resource, it becomes a **request**.
Some requests run straight away. Others need an **approval** first, for
example from a data owner or a platform admin, or a **task** that a person
completes by hand. This page covers both sides: following your own requests,
and acting on the ones assigned to you.

Both happen on one page, opened from **Requests** in the sidebar. When
something is waiting for you, the sidebar entry shows a count and opens on
**Needs my approval**; otherwise it opens on **My requests**. The page has
these tabs:

- **Needs my approval** lists what's waiting for you. You only see this tab
  if something can be assigned to you, and its badge shows how many items are
  waiting.
- **My requests** lists the requests you've made.
- **All requests** lists everyone's requests. Only platform admins see this
  tab.

## Make a request

Requests start as a conversation. Tell the assistant what you need; it asks
for anything it's missing, shows you its plan, and waits for you to confirm.
You can also start from **New request** at the top of **My requests**, or from
**Request Access** on an asset in Discover. See
[Using the assistant](using-the-assistant.md#make-a-request).

## Track your requests

**My requests** shows your requests as a table, newest first. Each row shows
the title, the request ID (click it to copy), the workflow, the status, and
how long ago it was submitted and last updated. When a request is waiting on
someone, the status column says who, for example "Waiting on Data Owner".
Click a row, or its eye icon, to open the details. The bin icon deletes the
request.

### Find a request

- **Search** filters as you type. It matches the request ID, title, workflow,
  status, requester and the names in the request, such as a catalog, table or
  workspace. Type more than one word to narrow the results further. Press `/`
  to jump to the search box.
- The status buttons switch between **In progress**, **Completed**,
  **Failed / rejected** and **All**, and show how many requests match each.
  The list opens on **In progress** when you have something in progress, and
  on **All** when you don't.
- **Workflow** shows one kind of request at a time. The filter is saved in the
  page's web address, so it stays on when you reload and you can send a
  colleague a link to the same view. Select **Clear filter**, or **All
  workflows** in the list, to see everything again.

The count on the right shows how many rows match out of the total. Long lists
show the first 100 rows, with **Show more** at the bottom. The list holds your
100 most recent requests. An older request still opens from its link.

### Request details

The details window has three tabs:

- **Request Status** lists each step with its progress. A failed step shows
  what went wrong.
- **Workflow** shows the request's workflow as a diagram, with each step
  marked as done, in progress, pending or rejected.
- **Conversation History** shows the chat that created the request.

Press Escape or click outside the window to close it. Each request has its own
web address, so you can share a link straight to its details.

Long-running requests keep going in the background, so you can leave the page
and come back later. The list refreshes on its own every few seconds. Use the
refresh button at the top right to update it straight away.

## Approve or reject a request

If you're an approver, requests waiting for you appear under **Needs my
approval**. **Requests** in the sidebar and your initials at the bottom of the
sidebar both show how many there are. The list opens on **Waiting**, oldest first, so the request that has been
blocked longest is at the top. **Handled** shows the ones you've already dealt
with, and **All** shows both. Search and the **Workflow** filter work the same
way as for your own requests. See [Find a request](#find-a-request).

Everything here is something you can act on, so there's no separate list of
what your roles allow. If you can approve it, the **Approve** button is on its
row.

### What's on a row

Each row shows the request, the workflow and the kind of approval, who asked,
the status, and how long it has waited. Requests that have waited three days
or more show the number of days in amber, and in red after a week. A line
above the list sums up how many have been waiting a while. A **Report** tag
means earlier steps left a report for you, such as a code review. **Manual
task** marks a step that a person completes by hand.

Click a row to expand it. The expanded row shows:

- Who made the request, the approver, when it was requested and when it's due.
- **Workflow Parameters**: the exact values the request will use.
- **What to do**: instructions, when the step is a task for a person.
- Reports from earlier steps, so you can see what has already been checked.

### Make a decision

- **Approve** lets the request continue. You're asked to confirm, and the
  confirmation shows any reports from earlier steps, so you see them even if
  you didn't expand the row.
- **Reject** stops it. You must give a reason, which the requester sees.
- **View full details** in the expanded row opens the same details window the
  requester sees, including the live **Workflow** diagram. Only the requester
  and platform admins can open a request's full history. Other approvers see
  the request's parameters and reports in the expanded row instead.

If a decision can't be saved, the window stays open and says what went wrong,
so you can try again.

Platform admins also see **Edit & Restart** in the expanded row of platform
approvals. It changes the request's parameters, replaces the current approval
and restarts the planning step with the new values. Access rule changes don't
offer it: what gets approved must be exactly what was asked for, so to change
one, reject it and file a new one.

You can't approve a request you submitted yourself; another approver must.
Its **Approve** button is turned off and says why, and the **Requester**
column shows "You". You can still reject your own request to withdraw it. An
admin can change this under
[Approvals](admin/settings-and-access.md#approvals) in Settings.

### Approve several at once

Tick the box on each approval you want to approve, or the box in the header
to select every one on screen that you can approve. Then select **Approve
selected** in the bar that appears above the list. You see the list and
confirm, and then the result for each one. Any that couldn't be approved say
why, and the rest go ahead. Rejecting is always one at a time, since each
rejection needs its own reason. Only approvals that are showing are approved:
anything that search or a filter hides is left alone.

Some approvals go to a Databricks group rather than a role. Access rule
changes, for example, go to the approver group for their type of rule, and the
row says which, such as "UC securables approvers: `grp_uc_admins`". When that
type has no group, it says Platform Admins approve. If you're in that group,
you see them here.

### Manual tasks

Some steps are tasks rather than approvals, such as a change that a person has
to make by hand. Expand the row and follow the **What to do** instructions,
then select **Mark done**. If you can't do it, select **Can't complete** and
explain what blocked you, so the requester knows what happened.

## Out of office

To have someone else cover your approvals while you're away, select **Out of
office** at the top of **Needs my approval**. Enter who to **Delegate to**, a
**Start date** and an **End date**, then select **Enable delegation**.

A line above the list shows the cover you've set up. Select **Revoke** to end
it early. If someone has delegated their approvals to you, a line says whose
approvals you're covering, and until when.

## Handled approvals

**Handled** lists the approvals you've dealt with and their outcome:
approved, rejected, done, delegated, or **Superseded by edit** when an admin
changed the request's parameters and restarted it. Expand a row to see the
rejection note or why it was superseded.
