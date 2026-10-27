# Using the assistant

The assistant is the chat that opens when you start {{brand_name}}. You can
also reach it from **Ask Anything** at the top of the sidebar. Ask in plain
language: it finds data, answers questions about your data, and starts
requests for you.

## Ask a question

Type in the **Ask a question...** box and press Enter. Some things to try:

- "Which datasets do we have about customer orders?"
- "How many orders did we ship last month, by region?"
- "I need read access to the sales orders table."

The assistant may suggest prompts when you start. After each reply it also
offers two to four suggested next steps as buttons under its latest message.
When it asks you to choose between options, the options are the buttons, so
you can answer in one click. Clicking a button sends its text as your message,
exactly as if you'd typed it, so the assistant still asks you to confirm
before it submits anything. The buttons disappear as soon as you send
something.

While the assistant works, you see each step it takes, such as a catalog
search or a data query, as a small label in the conversation. Expand a step's
**Raw output** to see exactly what came back. When the assistant has explained
its thinking, **Show reasoning** at the end of the conversation reveals it.

## Answers about your data

When you ask about the numbers in your data, the assistant queries it for you
with Databricks Genie and shows the answer in the chat, often with a table or
a chart. **Genie is working…** means the query is still running.

- Click **Customize** on a chart to change its type, its axes, the field that
  sets the colors, and how values are combined.
- Open **Genie details** to see the SQL that was run. From there you can copy
  the SQL, or open the same conversation in Databricks Genie to keep exploring.
- Genie can take a minute or two, so the assistant uses it only when a faster
  lookup, or a query on a table it already knows, won't do. A follow-up such as
  "now by region" continues the same Genie conversation instead of starting over.

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

### Report a problem with data

If a table looks stale, wrong or broken, tell the assistant. It looks up the
owner and offers to report the problem to the team, and it sends the report
once you say yes. You can also say "report this" straight away. You don't need
to pick a category. A repeat of the same report within a few minutes is
recorded once.

### Work that happens in Databricks

The assistant answers questions about data, but it doesn't write queries for
you to keep, build dashboards, or change tables and their data. If you ask for
one of these, it sends you to the right place in your Databricks workspace,
such as the SQL editor, Dashboards or Genie Code. It does this straight away:
it doesn't look the table up or run your query first, so if it suggests a fix
to a query it tells you it is a likely fix it hasn't run. It brings the full
table name you gave, and for a change to a table or its data it points you to
the owner and can help you draft the request. Changing a table's tags or
description is still done here, in the Metadata Manager.

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

Once you confirm, the request is created and tracked under **Requests** → **My requests**.
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
