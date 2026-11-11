# Workspaces and Genie One

{{brand_name}} helps you find data, check your access and request what you're
missing. Analyzing the data happens in Databricks, and the **Data & AI** section
of the sidebar takes you there.

## Genie One

Genie One is where you analyze data. Ask a question in plain language, such as
"How many orders did we ship last month, by region?", and it answers with
numbers, charts and dashboards. Use it for totals, trends, comparisons and
anything else that means working with the data itself.

**Genie One** in the sidebar opens Genie One for your whole organization, in a
new tab. It appears once an admin has set its address. You can also open the
Genie One of a single workspace from the [Workspaces](#workspaces) page.

The assistant doesn't analyze data in the chat. When you ask it something like
"total sales by month", it sends you to Genie One with a link, the full name of
the table to use and a question you can paste in. It can still help you find
the right table first, check whether you can read it, or show you a few sample
rows. See [Using the assistant](using-the-assistant.md#answers-about-your-data).

## Workspaces

**Workspaces** in the sidebar opens a page that starts with a short explanation
of the lakehouse and when to use it rather than Genie One. Below that is a card
for every Databricks workspace one of your groups has access to, according to
your organization's access rules. Each card shows:

- the workspace's name and environment, and **Admin** if one of your groups
  administers it;
- the groups that give you access (the first three, then how many more; hover
  over the count to see the rest);
- **Lakehouse** along the bottom, which opens the workspace itself, for
  writing SQL or Python, building pipelines and jobs, or publishing
  dashboards;
- **Genie One** beside it, which opens Genie One in that workspace.

The page starts on your production workspaces. Use the environment buttons
above the cards to switch, or **All** to see every one; each button shows how
many of your workspaces it holds. Type in the search box to find a workspace
by its name or by one of your groups. The page's address keeps both choices,
so you can bookmark or share a filtered view.

If a workspace's address isn't known yet, its links are greyed out; ask your
{{brand_name}} admin to add it. If no workspaces are listed, none of your
groups has workspace access in the access rules. Ask the
{{brand_short_name}} Agent to help you request it.
