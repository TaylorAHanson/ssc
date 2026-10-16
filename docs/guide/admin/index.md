# Admin overview

This part of the guide is for the people who run {{brand_name}}: platform
admins, who configure the app and its workflows, and governance admins, who
look after data policy and certification. Everyone else can skip it.

## Who sees what

Admin screens appear in two sidebar sections, depending on your role:

| Section | Pages | Who sees it |
| --- | --- | --- |
| Watch Tower | **Data Certification (ODCS)**, **Data Products (ODPS)**, **Allowlist**, **OmniGuard**, **Metadata Manager** | Platform admins and governance admins |
| Watch Tower | **Access Rules** | Platform admins, viewer roles and members of the access rule approver groups |
| Control Tower | **Admin** | Platform admins |
| Control Tower | **Context Catalog**, **Workflow Studio**, **Tool Registry**, **Preview Features**, **Training Studio** | Platform admins and governance admins |

Roles come from the groups people belong to. See
[Roles & Access](settings-and-access.md#roles--access) to change who gets
which role.

## The Admin page

**Admin** in the Control Tower section opens the **Admin Dashboard**. Its tabs
are:

- **Dashboard**: charts of requests over time, by status and by type, and how
  long they take. **Needs attention** lists stuck requests, recent failures and
  the oldest requests waiting for approval. Click any number or chart to filter
  the request list below it, and click a request to open its details.
  Scheduled runs, such as policy scans, are hidden unless you switch them on.
- **Settings**: everything you can configure without a redeploy. See
  [Settings, features and access](settings-and-access.md).
- **Test Runner**: tools for managing the development database. You only need
  these in a development environment.
- **Training Upload**: upload a CSV of training completions from your learning
  system. These completions update people's progress on the **Training** page
  and satisfy any workflow step that requires a course.
- **Legacy Dashboards**: map dashboards people used before, for example in
  Tableau, to the metric views that replace them. People then find them in
  Discover under **Legacy Dashboards** and when they search for the old name.
- **Feedback**: everything people have sent with **Send feedback** in the
  account menu.

## Where to go next

- [Settings, features and access](settings-and-access.md): branding, which
  pages people see, roles and notifications.
- [Workflows, tools and knowledge](workflows-and-tools.md): Workflow Studio,
  Tool Registry, Context Catalog, Training Studio and Preview Features.
- [Governance tools](governance.md): OmniGuard, the Allowlist, Data
  Certification, Data Products and Metadata Manager.
- [Access rules](access-rules.md): which groups hold which privileges, the
  approver group for each type, and the CSV export the Terraform pipeline
  reads.

The **Admin reference** section of this guide has the full, longer documents
these pages summarize.
