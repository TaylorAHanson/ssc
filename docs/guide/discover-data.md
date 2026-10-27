# Discover data

The **Discover** page is the data catalog. Open it from **View & Search
Catalog** in the sidebar to browse data by business area, search for a
specific asset, and see where data comes from and who looks after it.

## Search the catalog

Type in **Search metric views, tables, dashboards…** at the top of the page.
Results appear as you type. Press `/` anywhere on the page to jump to the
search box.

If nothing matches, or you'd rather describe what you need, press Enter or
**Ask AI**. An **Agent Assistant** panel opens on the page and answers from
the catalog. You can ask follow-up questions there.

## Browse by business area

Data is organized into business domains, such as finance or sales, and each
domain into subdomains. **Enterprise Data** always comes first and is
highlighted. It holds shared data that isn't owned by a single business area.

1. Under **What domain are you interested in?**, pick a domain card.
2. In **Step 2**, the left column lists the domain's subdomains. Click one to
   open it. The right column, **All of** the domain, jumps straight to one
   type across the whole domain, such as **Legacy Dashboards** or **Tables &
   Views**. **Explore all** opens everything.

If a domain has no subdomains, clicking its card skips Step 2 and opens the
domain's assets directly.

The domain view lists everything in that area by type: **Metric Views**,
**Tables & Views**, **Dashboards** and **Apps**. Some organizations also list
**Legacy Dashboards** from older reporting tools. Click a type to show or hide
it. Use **All Domains** and **All Subdomains** at the top to move back up.

Two switches narrow the list:

- **Accessible to me** shows only the data you already have permission to
  query.
- **Certified Only** shows only data that has been certified as meeting your
  organization's standards for quality, documentation and access control.

Unless your screen is very wide, these switches show only an icon. Hover over
one to see what it does.

## Asset details

Click an asset to open its details. Depending on the asset, you'll see some of
these tabs:

- **Overview**: the description, owner and tags.
- **Columns** (or **Schema** for a dataset): the fields it contains.
- **Lineage**: where the data comes from and what uses it. Click **Sources**
  on a table to see what feeds it, or **Consumers** to see what depends on it.
- **Data preview** (tables and views): a sample of the rows, read with your
  own permissions, so you only see data you're allowed to query. Search
  across all columns, click a column header to sort, or type in a column's
  filter box. Filters accept text, `null`, or comparisons such as `>20` on
  number columns. The preview only loads when you open the tab, and it shows
  at most a set number of rows, which your administrator chooses.
- **Quality**: service levels and data quality checks, where they're defined.
- **Team & Support**: the people responsible for a dataset and how to get
  help, plus the day-to-day support contact your administrator has set.

**Open in Catalog Explorer** opens the asset in Databricks.

To make the details panel wider or narrower, drag its left edge. Double-click
the edge to reset it. The width you choose is remembered.

## Pin assets you use often

Select the pin icon on an asset card, or **Pin** in its details panel, to add
it to the **Pinned** row at the top of the Discover page. Click a pinned item
to open it, or its **×** to unpin it. Pins stay on one line: if they don't
all fit, the rest appear under **+N**. Your pins follow you between browsers
and devices. If a pinned asset is removed from the catalog, it's shown
crossed out until you unpin it.

## Assets you won't see here

An admin can keep some assets out of Discover by tagging them in Databricks
with a tag key they choose, such as `hide_from_discover`. A tagged table,
view or metric view doesn't appear anywhere on this page, in search, in the
domain counts, in your pins or in lineage, and the assistant won't suggest it
when you ask what data exists. Tagging a catalog or schema hides everything
in it. This only affects what {{brand_name}} lists: if you already have
access to the asset, you can still open it in Databricks.

## Request access

If you can't query an asset yet, select **Request Access** on it. This opens
the assistant with an access request already written for you. Check it,
answer any questions, and confirm. Your request then appears under **My
Requests**. See [Requests and approvals](requests-and-approvals.md).

## Share what you're looking at

Every domain, subdomain and asset has its own link. Select **Copy link** to
copy it. Anyone who opens the link lands on the same view, as long as they
have access to {{brand_name}}.
