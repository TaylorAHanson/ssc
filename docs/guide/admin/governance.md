# Governance tools

The **Watch Tower** section of the sidebar holds the governance tools. Platform
admins and governance admins can use them. For the policy model behind these
tools, see the [Governance operating guide](../../GOVERNANCE.md).

## OmniGuard

**OmniGuard** scans your target workspaces for policy violations.

- **Run Scan** checks every resource against every policy. Safe, reversible
  fixes, such as certifying or uncertifying data or warning its owner, are
  applied automatically. Anything destructive is never automated: it's listed
  for you to review. Scans also run on a schedule; the next run is shown on the
  page.
- **Advanced options** narrows a scan to one workspace or environment.
- **Email Digest** emails a summary of the findings.
- **Run History** lists past scans with how many violations each found. The
  bin icon on a row deletes that run, which also stops a scan that's still in
  progress. **Clear old runs** removes older history in bulk.

Open a run to see its report. **Violations** lists one row per resource and
policy; **Checklist** itemizes every rule that failed. Tabs above the list
separate open violations from data products that are **ready to certify** and
findings covered by **approved exceptions**. Select **Review and Act** on a
violation to see the details and choose what to do.

## Allowlist

The **Enforcement Allowlist** records approved exceptions that OmniGuard should
leave alone. Select **Add Exception** and give the resource, its type and
workspace, a justification and, optionally, an expiry date.

## Data Certification

**Data Certification (ODCS)** tracks which datasets meet your standards. For
each dataset it shows the data contract, data quality results and
certification status. Search by table or location, and open a dataset to see
its checklist and version history.

## Data Products

**Data Products (ODPS)** catalogs your data products. Draft a new one by
combining certified data contracts with the interfaces people use to reach the
data.

## Metadata Manager

**Metadata Manager** keeps the tags and descriptions on catalogs, schemas,
tables, views and columns correct. {{brand_name}} holds the list of what each
object should carry, and a reconciler makes Unity Catalog match it. Owners edit
their own objects, sensitive changes go for approval, and pipelines can make
changes through an API. See [Metadata Manager](metadata-manager.md) and
[Metadata API and MCP](metadata-api.md).

## Access Rules

**Access Rules** records which groups hold which privileges on Unity Catalog
securables, metastore objects and workspace items, and exports it for the
Terraform pipeline. Unlike the other Watch Tower pages, members of the access
rule approver groups can open it too. See [Access rules](access-rules.md).
