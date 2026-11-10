# Using the assistant

The assistant is the chat that opens when you start {{brand_name}}. You can
also reach it from **{{brand_short_name}} Agent** at the top of the sidebar.
Ask in plain language: it finds data, checks your access, answers questions
about this app, and starts requests for you. Analyzing data happens in Genie
One; see [Workspaces and Genie One](workspaces.md).

## Ask a question

Type in the **Ask a question...** box and press Enter. Some things to try:

- "Which datasets do we have about customer orders?"
- "Where can I find shipments data, and can I read it?"
- "I need read access to the sales orders table."

The assistant may suggest prompts when you start. After each reply it also
offers two to four suggested next steps as buttons under its latest message.
When it asks you to choose between options, the options are the buttons, so
you can answer in one click. Clicking a button sends its text as your message,
exactly as if you'd typed it, so the assistant still asks you to confirm
before it submits anything. The buttons disappear as soon as you send
something.

For a broad request, such as "What data should I look at?", the assistant
asks one short question about your role or what you're working on, with a few
suggested answers and the one it recommends based on what it knows about you,
such as your groups and recent requests. If that already makes the answer
clear, it skips the question and tells you what it assumed. It asks at most
once per conversation.

While the assistant works, you see each step it takes, such as a catalog
search or a data query, as a small label in the conversation. Expand a step's
**Raw output** to see exactly what came back. When the assistant has explained
its thinking, **Show reasoning** at the end of the conversation reveals it.

To stop a reply, select the square **Stop** button that replaces the send
button while the assistant works. If a reply is taking a while or many steps,
a **Still working on this** note offers the same **Stop** link. The steps
already shown stay in the conversation, so you can rephrase and ask again.

The assistant remembers what it looked up earlier in the conversation, such
as a table's owner or the workspaces it can reach, and answers follow-ups from
that instead of looking it up again.

## Answers about your data

The assistant helps you find data and get to it, but it doesn't analyze it in
the chat. Totals, averages, rankings, trends, breakdowns, comparisons and
forecasts are for Genie One, which is built for them. When you ask something
like "How many orders did we ship last month, by region?", the assistant sends
you to Genie One with a link, the full name of the table to use, and a question
you can paste in. See [Workspaces and Genie One](workspaces.md).

It still looks inside a table when that helps you find or check data:

- a few sample rows, the number of rows, the range of dates it covers or the
  values a column holds;
- whether you can read it, with a one-row read as you.

Under a query's results, **How this was answered** shows where the answer
came from:

- the tables the query read, each with whether it's certified (and why not),
  its data quality results, its owner, and the system its data comes from when
  your organization records it;
- the SQL itself, run with your permissions, with **Copy SQL**, **Open the SQL
  editor** and **Query history** to check or re-run it in Databricks.

Ask "What table is that from?" and the assistant answers from the query it
already ran, by full table name, without running it again. When a table isn't
certified or has failed quality checks, it says so, and it prefers certified
tables when more than one could answer.

Charts are rare. Admins asking about this app's own activity, such as "plot
requests over time", get a chart of it in the chat. Click **Customize** on a
chart to change its type, its axes, the field that sets the colors, and how
values are combined.

The assistant uses your own Databricks permissions, so it can only see and
query the data you already have access to.

### Ask about one table

Ask about a table by name, for example "Tell me about the product table", and
the assistant puts together what it knows: the description, owner, domain and
tags, whether it's certified, its data quality results and refresh promise
(SLA) when they're recorded, and its columns with their types and comments. It
also says when the table's definition last changed. That isn't the same as when
its data was last refreshed, so for freshness the assistant checks the table's
history or quotes the SLA, and says which it used. If a table has no quality
result or SLA, the assistant says none is recorded rather than guessing.

If several tables share the name you typed, the assistant says how many matched,
lists a few and asks which one you mean. It doesn't answer for one of them
until you choose, so give the catalog or schema name to skip the question.

### Ask what you can access

Ask "Do I have access to this schema?" or "What can I access?" and the
assistant checks the access rules against the groups you belong to. It tells
you which of your groups holds the access, or that none of them is recorded as
holding it. The recorded rules don't include every grant, so when none of your
groups holds it, the assistant also tries a one-row read of the table as you
and tells you the result. It says you don't have access only when Databricks
refuses the read, and says it couldn't check if something else went wrong.
It then offers to help you join a group or ask for access.

Access goes to groups, so when you ask for access for yourself the assistant
asks which group you mean instead of choosing one. If a group already has the
access, it tells you to join that group rather than starting a request that
wouldn't change anything.

Ask "Which group do I need to read this table?" and the assistant names the
groups that hold access in the access rules, and where that access comes
from. Many tables and views get their access from their schema or catalog
rather than their own rule, so it looks there too and says so, for example
"access to this view comes from its schema, through the planning-readers
group". If nothing is recorded at any of those levels, it may mention groups
with access to nearby tables in the same schema, clearly labelled as a hint.
A group named in a table's access-group tag is included too. It never suggests
the built-in groups everyone is already in, such as "users". It tells you
which of the groups you're already a member of.

### Report a problem with data

If a table looks stale, wrong or broken, tell the assistant. It looks up the
owner and offers to report the problem to the team, and it sends the report
once you say yes. You can also say "report this" straight away. You don't need
to pick a category. A repeat of the same report within a few minutes is
recorded once.

A question about feedback, such as how it works or who reads it, is answered
and not filed. Super, Application and Governance Admins read what has been sent in
**Admin → Feedback**; if you ask the assistant where to see it, it points you
there. Anyone else is told that the admins review it. The assistant can't read
what has been filed, so it won't tell you the status of a report.

### Work that happens in Databricks

The assistant answers questions about data, but it doesn't write queries for
you to keep, build dashboards, or change tables and their data. If you ask for
one of these, it sends you to the right place in your Databricks workspace,
such as the SQL editor, Dashboards or Genie Code. It does this straight away:
it doesn't look the table up or run your query first, so if it suggests a fix
to a query it tells you it is a likely fix it hasn't run. It brings the full
table name you gave, and for a change to a table or its data it tells you the
change is made by someone with write access to the table, following your
organization's process where one is documented. For a dashboard it gives you the
next step in Dashboards first, without searching for data, and then offers to
help you find a dataset to build it on.

Some questions are better answered by Genie Code, which runs as you in your
Databricks workspace and can explore the system tables freely: cost and usage
analysis (such as spend by workspace last month), audit-log investigations
(such as who dropped a table), access audits across all of Unity Catalog, and
exploratory SQL or notebook work. When you ask one of these, the assistant says
so and gives you a short prompt to paste into Genie Code. It can still check
who has access to one specific catalog, schema, table, volume, function or
model.

Changing a table's tags or description is still done here, in the Metadata
Manager, which is limited to data owners and governance. If you don't have
access to it and spot a wrong description or tag, the assistant tells you who
can change it and offers to report the problem where feedback is switched on.

## Make a request

Ask for what you need, for example access to a table or a new resource.

Before it asks for any details, the assistant checks what already exists.
If your request could mean more than one thing, or what you asked for
already exists, it tells you what it found and asks which option you want.
For example, if you ask for a new domain workspace and one already exists, it
asks whether you need access to it, a new environment for it, or something
else. Only once the right request is clear does it ask for the details it
needs.

Before submitting, the assistant also checks for open or recently completed
requests for the same thing. If you already have one, it links it. If a
colleague does, it tells you one exists and its status. Either way, it asks
whether to submit anyway, wait, or change your request.

Access to data and workspace items is given to groups, not to individual
people. If you ask for access to a table or schema for yourself, the assistant
tells you which groups already have it, so you can ask to join one. To give a
group access, name the group, what it needs and why. If the group already has
that access, the assistant says so. An ask that covers both workspace and data
access becomes linked requests, one for each, because different people approve
them.

The assistant then shows you its plan. Nothing changes until you confirm.
If another open request changes the same rule, the plan says which request and
which object it's for. It's a warning when both requests ask for that rule,
so you can check one won't undo the other. It's a note, with nothing to
resolve, when one of them only needs the rule as a prerequisite, such as access
to a schema that's needed to read a table in it.

Once you confirm, the request is created and tracked under **My Requests & Approvals** → **My requests**.
Requests that need sign-off go to the right approvers first. See
[Requests and approvals](requests-and-approvals.md).

## Start over or keep a copy

- **New Chat**, next to the send button, clears the conversation and starts a
  fresh one.
- **Download transcript** saves the whole conversation, including each step
  the assistant took, as a Markdown file.

Your conversation is saved, so it's still there when you come back, even on
another device. To delete it, use **Clear my data** in the account menu.

## The home screen

Before you start a conversation, the home screen may also show your data
catalog:

- **Your Pinned Items** lists data products and datasets you've pinned. Click
  the pin icon on any card to pin or unpin it. Pins are saved in your browser.
- **Data Products** and **Datasets** show some of what's available. Click a
  card to open its details in Discover, or **Browse all** to see everything.

See [Discover data](discover-data.md) for more about browsing the catalog.

## Tips

- Be specific about the data you mean: name the table, the business area or
  the time period.
- If an answer looks wrong, say so and explain what you expected. The
  assistant takes the whole conversation into account.
- To share exactly what you're looking at in the catalog, use **Copy link** on
  the Discover page rather than a screenshot of the chat.
