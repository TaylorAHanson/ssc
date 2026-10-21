"""Metadata Manager (ADR-0006): desired tags and descriptions in Lakebase, reconciled into Unity Catalog.

Modules:

* ``types``        DTOs shared by every surface (the frozen contract)
* ``errors``       exceptions the API maps to status codes
* ``store``        reads and the single write path over the desired-state tables and the change log
* ``changes``      the change service: resolve ops, preview, submit, promote, revert, adopt, import
* ``report``       the approver-facing markdown for a change
* ``authz``        who may edit what (owner tags, admins, principals, sensitivity)
* ``policy``       key policies, governed values and sensitivity rules
* ``environments`` catalog-to-environment mapping and counterparts
* ``principals``   registered machine callers
* ``identity``     recognizing users and service principals from the Apps proxy headers
* ``observed``     the information_schema snapshot (sweep, refresh, new-object poll)
* ``search``       search, facets and key listing over desired + observed
* ``diff``         desired vs observed for one object
* ``sql``          statement builders
* ``apply``        executing statements against UC
* ``reconciler``   the queue, runs, modes and circuit breaker
* ``requests``     filing approval requests and the workflow's prepare and record steps
"""
