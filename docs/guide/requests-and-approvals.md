# Requests and approvals

When you ask the assistant for access or a resource, it becomes a **request**.
Some requests run straight away. Others need an **approval** first, for
example from a data owner or a platform admin, or a **task** that a person
completes by hand. This page covers both sides: following your own requests,
and acting on the ones assigned to you.

## Make a request

Requests start as a conversation. Tell the assistant what you need; it asks
for anything it's missing, shows you its plan, and waits for you to confirm.
You can also start from **New request** at the top of **My Requests**, or from
**Request Access** on an asset in Discover. See
[Using the assistant](using-the-assistant.md#make-a-request).

## Track your requests

**My Requests** lists every request you've made. Use **Pending**,
**Completed** and **Failed** at the top to switch between them. Failed also
includes rejected requests.

Each row shows the title, the request ID (click it to copy), the type, the
status, and when it was created and last updated. Under **Actions**, the eye
icon opens the details and the bin icon deletes the request.

### Request details

The details window has three tabs:

- **Request Status** lists each step with its progress. A failed step shows
  what went wrong.
- **Workflow** shows the request's workflow as a diagram, with each step
  marked as done, in progress, pending or rejected.
- **Conversation History** shows the chat that created the request.

Long-running requests keep going in the background, so you can leave the page
and come back later.

## Approve or reject a request

If you're an approver, requests waiting for you appear under **Pending
Approvals** in the sidebar, and the bell at the bottom of the sidebar shows how
many there are. The page lists them under **Pending**, with your finished
decisions under **Completed**. **My Roles & Approval Capabilities** shows which
kinds of request your roles let you approve.

### What's on an approval card

Each card shows who made the request, the type of request and approval, and
when it's due. A **waiting N days** badge appears on requests that have been
waiting a while, and overdue items are marked in red. Depending on the
request, the card may also show:

- **Workflow Parameters**: the exact values the request will use.
- **What to do**: instructions, when the step is a task for a person.
- Reports from earlier steps, such as a code review, so you can see what has
  already been checked.

### Make a decision

- **Approve** lets the request continue. You're asked to confirm.
- **Reject** stops it. You must give a reason, which the requester sees.
- **Delegate** hands this one approval to someone else. Enter their email
  address.
- **View Details** opens the same details window the requester sees,
  including the live **Workflow** diagram.

Platform admins also see **Edit & Restart** on platform approvals. It changes
the request's parameters, replaces the current approval and restarts the
planning step with the new values.

### Manual tasks

Some steps are tasks rather than approvals, such as a change that a person has
to make by hand. Follow the **What to do** instructions, then select **Mark
done**. If you can't do it, select **Can't complete** and explain what blocked
you, so the requester knows what happened.

## Out of office

To have someone else cover your approvals while you're away, select **Manage
OOO / Delegation** at the top of the page. Enter who to **Delegate To**, a
**Start Date** and an **End Date**, then select **Enable Global Delegation**.

**Your Active OOO Delegations** lists the cover you've set up. Select
**Revoke** to end one early. If someone has delegated their approvals to you,
they're listed under **Incoming Delegations (You are Acting Admin)**.

## Completed approvals

**Completed** lists the approvals you've dealt with and their outcome:
approved, rejected, delegated, or **Superseded by Edit** when an admin changed
the request's parameters and restarted it.
