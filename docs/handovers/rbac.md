Portable summary of everything this branch added relative to `develop`, for reimplementation by another coding agent.
Comparison base: merge-base `3c832e69` (not the current `develop` tip, which has diverged). 44 commits, ~147 files, ~30k lines added.
Method: each part below was generated from the diff by reading the code. Snippets are verbatim from the branch. No design judgements are made here.
Parts: 1 Intent/config/deploy · 2 Tools · 3 Workflows/instructions/policies/engine · 4 Backend API/DB/services · 5 Frontend/tests
Part 01 - Intent, Scope, Architecture, Requirements, Config, Deployment, History
Repo: `edh-self-service-center`, branch `feature/EDTFNDTN-4251`. Compared against merge-base `3c832e69` (44 commits, 147 files, ~30.5k lines added). Do not diff against current origin/develop (diverged).
1. Purpose and design intent
Add RBAC automation to the EDH Self-Service Center (a governed agentic platform on Databricks Apps: FastAPI + LangGraph + Lakebase/Postgres; React SPA). Users ask for Databricks access in chat; the agent validates, routes approvals, and the grant/revoke is recorded in an RBAC desired-state store, from which Terraform (existing, external pipeline) applies it.
Phase 1 (what exists): the Excel RBAC workbook (`RBAC_WS_and_UC_Securables_Privileges_updated.xlsx`) is the desired-state source of truth, mutated by the automation (not Terraform apply directly). The external Terraform GitOps pipeline reads the workbook. Phase 2 (designed, NOT built): YAML desired state + GitOps (branch -> plan -> approve -> merge -> apply), callback API, deprecate Excel. Flag `yaml_desired_state: false`.
Pre-existing project rules that bind the work (AGENTS.md): logging not print; never hardcode brand; prefer no-code config (Admin Settings, databricks.yml, YAML); check backend.log; venv at backend/venv; local DB SQLite `backend/app_hub.db`, deployed Lakebase.
Architectural pillars reused from the platform: ToolExecutor chokepoint, OPA/Rego guardrails (hard_deny / extra_approval / warn / allow), workflows-as-data (JSON specs in `backend/app/workflows/graphs/catalog/`), per-workflow agent instruction markdown (`backend/app/agents/instructions/rbac_.md`), golden transcripts for the test harness (`backend/app/workflows/golden_transcripts.json`), MCP exposure of 4 tools (`rbac_read_workbook`, `rbac_validate_privilege`, `rbac_get_effective_access`, `rbac_detect_drift`; `external=True`).
Evolution of the design (important): TWO generations.
Gen 1 (Aug 2026, commits 8fb913c..66283ef): 9 workflows + ~20 tools + 7 rego policies + 3-backend storage abstraction (filesystem/SharePoint/Git PR via `rbac_commit_workbook`). Flow: validate -> approvals -> mutate workbook (with `.backup`) -> open PR -> Terraform team merges.
Gen 2 (Sep 2026, from 3c41a2c onward): Lakebase-centred. `selfservice.rbac_replica` = canonical store the grid UI edits; `selfservice.requests` + new `bundle_id` column = request/approval tracking; approved bundles are exported as per-environment CSVs (not PR); `rbac_mutation_records` = audit. Commit `00477ff` introduced 6 separate lifecycle tables + `/rbac-lifecycle` API (12 endpoints) + `rbac_v2_` files + `build_frontend.sh`; commit `db1f1e5` deleted all of that ("simplify RBAC to unified design - no separate lifecycle tables": 11 files removed, `_v2_` names dropped). A reimplementation should follow the db1f1e5 unified design; V2 lifecycle tables do not exist at HEAD.
2. Nine request types and acceptance criteria
Source: `docs/RBAC_Request_Types_Acceptance_Criteria.md` (agent "pushback guide", generated 2026-08-30, "pending validation with Mano"; derived from specs, instructions, rego, estate.yaml).
#
key
purpose
approval chain
1
`rbac_uc_grant`
Grant UC privilege to a group on a securable
Manager -> Data Owner (+Platform Admin if extra_approval)
2
`rbac_uc_revoke`
Revoke / remove
Manager -> Data Owner
3
`rbac_ws_assignment`
Assign group to workspace
Manager only
4
`rbac_ws_entitlement`
Workspace entitlement (databricks_sql_access, cluster_create, token_usage, CAN_MANAGE)
Manager -> Workspace Admin (+PA if extra)
5
`rbac_group_membership`
Add/remove member of account group
Manager -> Group Owner
6
`rbac_ownership_transfer`
Transfer UC object ownership
Current Owner -> Platform Admin (always)
7
`rbac_role_assumption`
ASSUME on role-group (Public Preview, feature-gated off)
Manager -> Role Owner
8
`rbac_drift_detection`
Workbook vs live-state compare, read-only
none
9
`rbac_bulk_access`
Package-based grant of one privilege over many securables
Manager -> Data Owner (+PA if > bulk threshold)
Stage sequences (names persist in specs; `commit_to_git` naming survives even though gen-2 exports CSV):
grant: `validate_privilege -> resolve_prerequisites -> manager_approval -> data_owner_approval -> dry_run_preview -> confirm_plan -> apply_grant -> commit_to_git -> verify_changes -> notify_completion`
revoke: `check_existing_grant -> manager -> data_owner -> dry_run -> confirm -> apply_revoke -> commit -> verify -> notify`
ws_assignment: `check_entitlement_mode -> manager -> dry_run -> confirm -> apply_assignment -> commit -> verify -> notify`
ws_entitlement: `check_entitlement_mode -> validate_entitlement -> manager -> workspace_admin -> dry_run -> confirm -> apply_entitlement -> ...`
group_membership: `resolve_principal -> validate_group -> manager -> group_owner -> ... apply_membership ...`
ownership_transfer: `validate_securable -> resolve_new_owner -> check_pipeline_restriction -> current_owner_approval -> platform_admin_approval -> ... apply_transfer ...`
role_assumption: `check_feature_gate -> validate_role_group -> resolve_grantee -> manager -> role_owner -> ... apply_assumption ...`
drift: `read_workbook_state -> detect_drift -> notify_completion`
bulk: `expand_package -> validate_all_privileges -> check_existing_bulk -> manager -> data_owner -> ... apply_bulk_grants ...`
Universal pattern: dry-run (compute change plan) -> requester confirm -> apply; agent never skips the confirmation gate.
Cross-cutting rules (agent + OPA)
Groups-only: direct user grants hard_deny (exceptions in estate.yaml `allowed_principal_exceptions`).
System groups (`users`, `admins`, `account users`, plus admin patterns) immutable via self-service.
Justification: min 10 chars, the requester's own words; agent must NOT fabricate/embellish/suggest. Rubric: good = names project/deliverable/why this level/time bound/ticket; weak = generic, circular, "same as last time", exploratory without scope, "manager said"; suspicious = on behalf of someone else, "just in case", cross-team without agreement.
Hard deny: ALL_PRIVILEGES, MANAGE on catalog, USAGE (deprecated -> USE_CATALOG/USE_SCHEMA), direct user, workspace_admin/CAN_MANAGE to non-admin-pattern group, system-group mutation.
extra_approval (platform admin): schema-level SELECT/MODIFY (inheritance widening), catalog-level SELECT/MODIFY/READ_VOLUME/WRITE_VOLUME, cross-env (principal name has dev/sandbox/nonprod but env prd), administrative privilege on catalog/schema, MANAGE on schema, bulk > 10 securables, catalog-level inheriting privilege (except USE_CATALOG), env=all on catalog/schema, CAN_MANAGE entitlement.
warn: no expiry on SELECT/MODIFY/WRITE_VOLUME/WRITE_FILES, MV/ST securables, SP principal, schema MODIFY, CREATE_TABLE on catalog, workspace_access in legacy mode, cluster_create in prd, token_usage.
Per-type extras: revoke -> `not_found`; shadow grant (`in_live_only`, removing from workbook does not revoke); revoking a prerequisite (USE_SCHEMA) warns of cascade; last-accessor warn. ws_entitlement -> group must already be assigned to workspace (BLOCK). group_membership -> already-member no-op; nested groups extra approval; workspace-local groups out of scope. ownership -> MV/ST must stay owned by the pipeline SP (BLOCK); new owner needs USE_CATALOG+USE_SCHEMA on parent; prefer group owner. role_assumption -> blocked if estate flag `role_assumption` false; name must match `role-`; should be time-limited. drift -> warn if discovery data older than 240 min; offer follow-up grant/revoke requests; suggest scoping a full-estate scan. bulk -> >10 securables extra approval; suggest schema-level if equivalent; all-envs extra approval; dynamic package without filter pushed back.
Decision tree per request: recognized type -> OPA pre-flight -> justification quality -> scope vs use case -> existing grants (consistent / pending workbook-only / shadow live-only / not found) -> prerequisites (auto-add + inform) -> dry-run plan + confirm.
Approval matrix in doc Appendix B; approval routing tiers in estate.yaml (3.2).
Related docs: `docs/RBAC_Phase1_Capability_Code_Mapping.md` (spec/tool/instruction/rego/model inventory; tool side_effect classes: read, `data_grant` for mutate/compensate, `infra` for commit), `docs/rbac/RELEASE_NOTES_PHASE1.md` (opt-in via `rbac_automation_enabled` flag in Admin Settings; `RBAC_STORAGE_BACKEND` filesystem|sharepoint|git; deps openpyxl>=3.1, databricks-sdk>=0.28; Phase 2 preview), `docs/rbac/USER_GUIDE.md`, `USER_JOURNEYS.md` (per-type happy/error scripts), `UAT_FRAMEWORK.md` (TC-01..TC-10 incl. concurrent handling, estate missing), `docs/rbac/documents/HLD_Gap_Analysis.md` (712 lines; says the HLD docx is narrative-only; 16 gaps: architecture diagrams, policy framework, Terramate spec, CI/CD, state mgmt, AI agent architecture, identity resolution, rollback/compensation, Jira, observability, threat model, sequence diagrams, scalability, ops procedures, compliance mapping, error handling; plus Phase 1 documented-vs-actual, Phase 2 spec, recommended agent topology, risk register, implementation inventory). Binary docs (not read): `docs/RBAC_Automation_Design_plan_v3docx.docx` (design plan; v2 copy in docs/rbac/documents), `docs/RBAC_Automation_Documentation_Package.docx`, `docs/RBAC Request Types Acceptance Criteria Review.docx`, `docs/rbac/documents/EDH_Service_RBAC_Automation_HL_Plan.docx` (HLD), and workbook xlsx (3 copies: `docs/`, `docs/rbac/`, `rbac/`).
Code-review findings fixed in gen 1 (`docs/CODE_REVIEW_FINDINGS.md`, 2026-08-13) - do not reintroduce
mutate wrote `.mutated.xlsx` but commit read the original -> PRs with unchanged workbook. Fix: mutate writes same path with `.backup`.
`rbac_ownership_transfer.json` check_pipeline_restriction lacked required `principal_name` (`{"$var":"current_owner"}`).
`rbac_grants.rego` had `package data.rbac.grants` (mounts at data.data...). Must be `package rbac.grants`; no `data.` prefix. All rego use `import rego.v1`; `default decision` must be an object, not a string.
Parser file-handle leak: `finally: parser.close()` in drift/check_existing.
Sync Databricks SDK calls in async tools block the loop -> `asyncio.to_thread` (resolve_principal, get_effective_access, get_workspace_entitlement_mode).
`RbacWorkbookSerializer.__new__` hack -> `_determine_target_sheet` is a `@staticmethod`.
SCIM filter injection: escape `"` in `userName eq "..."`.
`SecurableType` enum needs `WORKSPACE`, `GROUP`, `ROLE_GROUP` (+ parser `SECURABLE_TYPE_MAP`, serializer sheet routing) else ws/group/role workflows crash on `SecurableType("workspace")`.
Parser methods are `parse_uc_records()` / `parse_workspace_records()` (no `parse()`).
Deprecated-token bypass: `USAGE: null` in providers.yaml; test key existence (`normalized in provider_map.deprecated_tokens`), not value non-None.
`rbac_role_assumption.json` passed `require_feature` to `rbac_read_estate_config` (schema accepts only `config_path`; Pydantic silently dropped it so the gate never gated). Use a `post_check` block.
Duplicate prerequisites for WRITE_VOLUME (READ_VOLUME added twice) -> set-dedupe.
`estate.get("valid")` does not exist; estate response keys are `{estate, workbook, mutation_enabled, validation_errors, message, config_path}` -> use `mutation_enabled`.
rbac_entitlements.rego used plural `workspace_entitlement_modes[ws]`; model dump is singular `workspace_entitlement_mode`.
drift `workspace_id` filter was dead code.
Known unfixed then: bulk workflow passes list `expanded_securables.names` into `securable_name: str` (needs fan-out/array support); dotted-path `$var` needs engine support; compensation (revoke) re-triggers extra_approval; sequential awaits in drift; `RBAC_WORKBOOK_REPO(_PATH)` read via getattr, not declared in settings.
3. Config file formats
3.1 `rbac/config/rbac_config.yaml` (642 lines; loaded at startup by `_load_rbac_config()`, cached; path via `RBAC_CONFIG_PATH`, default `rbac/config/rbac_config.yaml`). camelCase keys.
Sections: `approval`, `environments`, `enumerations`, `writeDeniedPermissions`, `writeDeniedRequestTypes`, `validation`, `crossFieldValidation`, `agenticPolicies`, `fieldPatterns`, `crossFieldConsistency`, `denyPatterns`, `knownPrefixes`.
```yaml
approval:
securityApprovalGroup: security-rbac-approvers
approvalTimeoutMinutes: 240
environments: # keys must match workbook values dev/tst/stg/prd; aliases allow test/stage/prod
dev: {access: read_write, aliases: [dev], exportPath: /tmp/rbac/exports/dev} # commented volumePath: /Volumes/enterprise_dev/gold_sales/rbac_exports
tst: {access: read_only, aliases: [tst, test], exportPath: /tmp/rbac/exports/tst}
stg: {access: read_only, aliases: [stg, stage], exportPath: /tmp/rbac/exports/stg}
prd: {access: read_only, aliases: [prd, prod], exportPath: /tmp/rbac/exports/prd}
exportPath types handled by _write_csv_to_path: /Workspace/... (SDK), /Volumes/<cat>/<sch>/<vol> (SDK files.upload, persistent),
/tmp or relative (container-local, EPHEMERAL on redeploy). To use Volumes: CREATE VOLUME IF NOT EXISTS <cat>.<sch>.rbac_exports
```
Enumerations: `environment` [dev,tst,stg,prd]; `principalType` [user, group, service_principal, "service principal"] (workbook uses the space form, both accepted); `objectType` (metastore, catalog, schema, table, view, materialized_view, streaming_table, volume, function, model, service, secret, feature, external_location, storage_credential, service_credential, connection, external_metadata, share, provider, recipient, clean_room, workspace, "workspace entitlement", folder, "service principal", group, role_group); `requestType` [grant, revoke, modify]; `permission` = UNION of ~60 UC privileges + workspace privileges (ADMIN, USER, CAN_MANAGE, CAN_READ, CAN_RUN, CAN_EDIT, CAN_ATTACH_TO, CAN_RESTART, CAN_MANAGE_RUN, CAN_VIEW, IS_OWNER, databricks_sql_access, workspace_access, workspace_consume, roles/servicePrincipal.user, roles/servicePrincipal.manager). Compound values are `;`-delimited ("USE_CATALOG;USE_SCHEMA;SELECT") and validated per atom.
`writeDeniedPermissions` (auto-denied in tst/stg/prd): MODIFY, DELETE, ALL_PRIVILEGES, MANAGE, WRITE_VOLUME, all CREATE_* (schema-level, catalog/metastore-level, sharing), SET_SHARE_PERMISSION, ADMIN, CAN_MANAGE, IS_OWNER. `writeDeniedRequestTypes: [modify, revoke]`.
```yaml
validation:
caseSensitive: {environment: true, principalType: true, objectType: true, permission: true}
requiredColumns: [environment, principal_type, privilege]
maxBatchSize: 50
allowedEditColumns: [environment, securable_type, catalog, schema_name, object_name, owner, principal_type, principal_name, privilege, workspace_name] # unknown cols rejected (injection defence)
crossFieldValidation: # WS securables only WS privileges; UC securables only UC privileges
workspaceSecurableTypes: [workspace, "workspace entitlement", folder]
workspacePrivileges: [ADMIN, USER, CAN_, IS_OWNER, databricks_sql_access, workspace_access, workspace_consume, roles/servicePrincipal.]
ucSecurableTypes: [metastore, catalog, schema, table, view, ..., clean_room]
agenticPolicies: # applied in before_insert hook on every agentic request
denyPrincipalTypeInEnv: # 6 entries: principalType service_principal | "service principal" x environment tst|stg|prd
principalType: service_principal
environment: tst
reason: "Service principals cannot be granted tst access via agentic flow. Submit through the admin grid with security approval."
hardBlockedPrivileges: [ALL_PRIVILEGES, MANAGE, ADMIN, IS_OWNER, CREATE_CATALOG, CREATE_EXTERNAL_LOCATION, CREATE_STORAGE_CREDENTIAL, CREATE_SERVICE_CREDENTIAL, CREATE_FOREIGN_CATALOG, CREATE_MANAGED_STORAGE, CREATE_CLEAN_ROOM, SET_SHARE_PERMISSION, EXTERNAL_USE_LOCATION, EXTERNAL_USE_SCHEMA]
escalationRequiredPrivileges: [MODIFY, DELETE, WRITE_VOLUME, CREATE_TABLE, CREATE_VIEW, CREATE_FUNCTION, CREATE_SCHEMA, CREATE_CONNECTION, CREATE_SHARE, CREATE_RECIPIENT, CREATE_PROVIDER, CAN_MANAGE, CREATE_MODEL, CREATE_SERVICE] # flagged, not denied
deniedRequestTypes: [revoke, modify]
writeOnlyEnvironments: [dev]
```
`fieldPatterns` (mined from 2243 production rows in `selfservice.rbac_replica`; enforced by BOTH grid commit/add-row/batch AND agentic before_insert; regex fully anchored, case-insensitive; rule attrs `regex`, `description`, `scope` (securable_type list), `envAware` ({env} placeholder), `atomDelimiter` (";"), `exceptions` (bypass regexes)):
```yaml
catalog:
regex: "^[a-z][a-z0-9_](_(dev|tst|stg|prd))$"
exceptions: ["._edh_federate$", "._edh_federate_(dev|tst|stg|prd)$"]
regex: "^[a-z][a-z0-9_]$"
workspace_name:
regex: "^[a-z][a-z0-9](_[a-z0-9]+)_ws_(dev|tst|stg|prd)$" # envAware: true
object_name: # scoped by securable_type
regex: "^[a-z][a-z0-9](_[a-z0-9]+)_ws_(dev|tst|stg|prd)$" # scope [workspace], envAware
regex: "^sp_[a-z][a-z0-9]_dbx_[a-z0-9_](dev|tst|stg|prd)([a-z0-9_])$" # scope ["service principal"], envAware
exceptions: ["^sp_dbx[a-z]+_[a-z0-9_]+(dev|tst|stg|prd)$", "^sp_dbx_[a-z0-9_]+$"]
regex: "^edh_[a-z][a-z0-9_]$" # scope ["workspace entitlement"]
regex: "^[a-z][a-z0-9_](_assets/|_aibi_|_ws_|/)[a-zA-Z0-9_;/._-]$" # scope [folder]; exceptions ["^controltower_[a-z0-9_/;.-]+$"]
regex: "^[a-z][a-z0-9_]$" # scope [view, schema, catalog, connection]
schema_name:
regex: "^(gold|silver|bronze|platinum|landing|txn|outbound)_[a-z][a-z0-9_]$"
exceptions: ["^(audit|common|compute_finance|data_quality|framework_agent_operations|pc_bu_lifecycle|rapidresponse|salesforce|security_fgac|sixsense|collection_aibi)$"]
regex: "^[a-z][a-z0-9_]$"
principal_name: # atomDelimiter ";", each atom validated
regex: "^edh_[a-z][a-z0-9_]$"
regex: "^sp_[a-z][a-z0-9_]_dbx_[a-z0-9_]$" # exceptions ^sp_dbx[a-z]+_[a-z0-9_]+$ , ^sp_dbx_[a-z0-9_]+$
regex: "^app-[a-z0-9]+ [a-z][a-z0-9_-]+$" # Databricks App SPs e.g. "app-5enjjg data-app"
regex: "^app-[a-z0-9]+ [a-z][a-z0-9_-]+;sp_dbxapp_[a-z0-9_]+$"
owner:
regex: "^edh_dbx_metastore_admin$"
crossFieldConsistency:
{field: catalog, rule: envSuffixMatch, exemptPatterns: ["._edh_federate$", "._edh_federate_(dev|tst|stg|prd)$"]}
{field: workspace_name, rule: envSuffixMatch}
{field: object_name, rule: envSuffixMatch, scope: [workspace, "service principal"], exemptPatterns: ["^sp_dbx_[a-z0-9_]+$"]}
{field: catalog, crossField: workspace_name, rule: envSuffixConsistency}
denyPatterns: # applied to ALL string fields; whole row rejected
regex: "^[a-z][bcdfghjklmnpqrstvwxyz]{3,}[a-z]$" # junk like dsfsdf (3+ consecutive consonants, no separators)
regex: "._ws_$" # workspace name missing env suffix
knownPrefixes: {businessUnit: [13 items], catalogLayer: [11 incl ""], spnSystem: [~25: dbxwf,dbxai,dbxapp,dbxgrc,idmc,iics,controlm,fivetran,celonis,plm,tm1,ods,ssm,lambda,aiop,deai,rf360,acceldata,dean,snow,ai_fin,collibra], schemaTier: [7]} # autocomplete only, NOT enforced
```
Enforcement code: `backend/app/api/v1/rbac_grid.py` (`validate_field_pattern()` order: deny patterns -> field patterns -> scope filter -> atom split -> exceptions; returns None if valid else message; `validate_cross_field_consistency()`), `backend/app/db/request.py` (`_auto_bundle_rbac_request` before_insert hook: on failure sets `status=denied`, `denial_reason="Pattern validation: {err}"` / `"Cross-field consistency ..."`). Grid returns HTTP 422. Enum-governed columns exempt from the junk-consonant deny pattern.
3.2 `rbac/estate.yaml` (282 lines; "Mutation DISABLED until this file is present, valid, and versioned"; must not be inferred from free text)
```yaml
estate:
cloud: aws
account_id: "auto" # resolved at runtime from metastore summary or DATABRICKS_ACCOUNT_ID
provider_version: "1.52.0" # pinned TF databricks provider; written tokens must be valid for it
privilege_model_version: "2024.12"
identity_federation: identity_federated
workspace_entitlement_mode: explicit # explicit enforced for all workspaces from 2026-09-14
idp_type: entra
provisioning_mode: scim
git_provider: github
terraform_runner: existing-gitops
verification_workspace: null
default_timezone: UTC
source_of_truth: # G-03
requests_and_approvals: edh
idp_memberships: idp
desired_state: workbook # Phase 1 workbook | Phase 2 yaml
runtime_verification: databricks_live
terraform_state: operational_evidence # NOT authorization truth; workbook-only or TF-only grants are not verified access
policy:
system_groups: ["users","admins","account users"]
admin_group_patterns: ["admins","platform_admin","-admins","_admins","admin"] # prefix / suffix / contains
role_group_prefix: "role-"
bulk_threshold: 10
cross_env_keywords: ["dev","sandbox","nonprod"]
allowed_principal_exceptions: # G-04
{principal_type: user, context: legacy_migration, environments: [dev, stg], requires_approval: platform_admin, sunset_date: "2027-06-30"}
{principal_type: service_principal, context: service_account, environments: [dev, stg, prd], requires_approval: platform_admin, sunset_date: null}
approval_routing: # G-05
deny_self_approval: true
require_active_approver: true
invalidate_on_intent_change: true
tiers: # standard:[manager]; elevated:[manager,data_owner]; high:[manager,data_owner,security]; critical:[manager,data_owner,security,platform_admin]
sod_rules:
{conflicting_privileges: [MODIFY, SELECT], same_securable: true, action: warn}
{conflicting_privileges: [ALL_PRIVILEGES], action: hard_deny}
discovery:
catalog: controltower_dev # override via env RBAC_DISCOVERY_CATALOG (per target); job derives enterprise_{env} otherwise
schema: audit
max_staleness_minutes: 240
freshness_slo: {uc_grant: 240, workspace_acl: 120, membership: 60, entitlement: 240, ownership: 480} # G-10
workbook:
file_path: "/Volumes/controltower_dev/framework_agent_operations/agent_volume/RBAC_WS_and_UC_Securables_Privileges_updated.xlsx" # overridden by RBAC_WORKBOOK_PATH
sheets: # name / type / environment
WS_Securables_RBAC_{Dev,Tst,Stg,Prd} workspace_rbac dev/tst/stg/prd
UC_metastore_RBAC metastore all
UC_Securables_RBAC_{Dev,Tst,Stg,Prd} uc_securable dev/tst/stg/prd (ALL securable types in one sheet per env)
expiry: # G-09
max_ttl_days: {standard: null, elevated: 365, high: 90, critical: 30, emergency: 7}
require_utc: true
revoke_worker: {poll_interval_minutes: 15, retry_attempts: 3, retry_backoff_minutes: [5, 15, 60], escalation_group: "platform_admin", reference_counting: true}
extension_requires_new_request: true
mutation_audit: {table_name: rbac_mutation_records, enabled: true} # override via RBAC_MUTATION_TABLE
feature_flags: {role_assumption: false, yaml_desired_state: false, emergency_access: false} # G-14; mutation blocked if referenced feature disabled
```
G-xx IDs refer to the design plan's gap list. Expiry enforcement code: `backend/app/workers/expiry_worker.py` + `rbac_expiry.rego`.
3.3 `rbac/registry/.yaml`
`privileges.yaml` (345 lines): `provider_version: "1.52.0"`; entries with `token, display_name, applicable_to[], prerequisites[], inherits_to_children, is_administrative, verifier (e.g. effective_permissions), feature_flag (estate flag key or null), test_gate, status (ga|preview|deprecated; preview fails closed if flag off)`. Regenerated by `backend/scripts/generate_privilege_registry.py`. Header: never use USAGE; use USE_CATALOG / USE_SCHEMA; tokens written must already be accepted by the existing parser. Example:
```yaml
token: CREATE_CATALOG
display_name: Create Catalog
applicable_to: [metastore]
prerequisites: []
inherits_to_children: false
is_administrative: true
verifier: effective_permissions
feature_flag: null
test_gate: test_metastore_grants
status: ga
```
`providers.yaml` (70): `token_map` (canonical identity maps incl. READ_FILES/WRITE_FILES), `deprecated_tokens`, `terraform_resources` (databricks_grant(s), _catalog, _schema, _sql_table, _volume, _external_location, _storage_credential, _connection, _group, _group_member, _permissions, _mws_permission_assignment).
```yaml
deprecated_tokens:
USAGE: USE_CATALOG # context decides USE_CATALOG or USE_SCHEMA
ALL_PRIVILEGES: null # rejected outright
ALL PRIVILEGES: null
READ: SELECT
WRITE: MODIFY
CREATE: CREATE_TABLE # ambiguous, flag for review
DROP: null
DELETE: MODIFY # part of MODIFY (DML)
```
`null` = reject (see gotcha on key-presence tests).
`securables.yaml` (137): entries `{securable_type, display_name, parent_type, naming_pattern ("<catalog>.<schema>.<table>"), dot_segments, supports_ownership_transfer, ownership_transfer_constraints?}`: metastore -> catalog -> schema -> table/view/materialized_view/streaming_table/volume/function/model/... Header rule: a securable is "supported" only with registry entry, canonical id + validation rule, mutation adapter, TF mapping, parent-prereq rule, inheritance/blast-radius rule, ownership/MANAGE rule, revoke+expiry rule, live verification, and tests. Unsupported types MUST fail closed with an actionable reason. Metastore ownership transfer: account admin only; views: owner restricted to self/own group unless metastore admin.
`workflow_taxonomy.yaml` (161; G-01 canonical ID map, `version: "1.0.0"`): `workflows[]` = `{canonical_id, name, description, aliases[], spec_file, access_planes[], supports_bulk?}`. Rules: canonical_id authoritative; request classifies to exactly one id else intake asks clarification. Includes the 9 implemented plus planned/unimplemented `rbac_sp_provisioning`, `rbac_emergency_access`, `rbac_cross_env_access`, `rbac_group_deprecation`. access_planes: uc_grant, workspace, identity, workspace_acl, execution. e.g.
```yaml
canonical_id: rbac_uc_grant
name: "UC Securable Grant"
aliases: [uc_grant, securable_grant, data_access_grant]
spec_file: rbac_uc_grant.json
access_planes: [uc_grant]
supports_bulk: true
```
(role_assumption: [identity, uc_grant]; ownership_transfer: [uc_grant, execution]; drift: all planes; ws_: [workspace]; group_membership: [identity].)
4. Lifecycle model (gen 2, at HEAD)
Request statuses (added to `RequestStatus` in `backend/app/models/request.py` and TS `src/types/index.ts`): `pending_approval -> approved -> exported` (or `export_failed`), plus `denied`, `rejected`.
Submit: via conversational agent (`execute_workflow` tool) or grid `POST /submit`. A SQLAlchemy `before_insert` event (`_auto_bundle_rbac_request`, db/request.py) assigns `bundle_id = "RBAC-{date}T{hour}"` (same-hour submissions share a bundle) using the new `bundle_id` column on `selfservice.requests` (+ Lakebase `ALTER TABLE` in migrate.py). The same hook auto-denies: non-dev write/edit (read-only envs), hard-blocked privileges, SP in non-dev env, revoke/modify via agent, enum violations, field-pattern and cross-field failures.
Duplicate detection (`_check_rbac_duplicate_for_agent` in execute_workflow.py), layered: L1 mutation audit, L3 in-flight request scan (with `db.refresh(req)` + status re-check - the stale-request fix), L4 replica table (authoritative). Audit-only hit with no replica row now ALLOWS resubmission.
Approve: security group (`RBAC_SECURITY_APPROVAL_GROUP`, default `security-rbac-approvers`) / Platform Admin on Approvals page: `POST /api/v1/rbac-grid/bundle/{bundle_id}/approve` flips all `pending_approval` to `approved`, then auto-calls `_run_export(bundle_id, db, log)` (try/except with `[APPROVE]` logging). Also `/reject`, `/export` (manual re-export), `GET /my-requests` (bundle-centric), `GET /pending-approvals`, `GET /bundle/{id}`.
Export: `_run_export` queries `RequestModel.type.in_(["rbac_uc_grant","rbac_uc_revoke"])`, groups by environment, writes ONE CSV per env named `{bundle_id}_{YYYYMMDDTHHMMSS}.csv` with columns `environment, securable_type, catalog, schema_name, object_name, owner, principal_type, principal_name, privilege` to that env's `exportPath` via `_write_csv_to_path()` (Volumes -> `w.files.upload()`; Workspace -> `w.workspace.mkdirs()` + `import_()`; else `os.makedirs` + `Path.write_text()`). On success inserts `RbacMutationRecord` rows (must set `target_sheet=f"export_{env}"` - NOT NULL), upserts RbacReplica, sets requests `exported`. Listing `GET /api/v1/rbac-grid/exports`; download `GET /api/v1/rbac-grid/exports/{env}/{filename}` (attachment). Debug (dev-only, no auth): `GET /api/v1/rbac-grid/debug/export/{bundle_id}`.
Grid (admin): Lakebase `selfservice.rbac_replica` canonical store (`rbac_replica.py`; `row_version` + `row_hash` optimistic concurrency, hash dedupe); `rbac_sync.py` syncs workbook -> replica (all 9 sheet types, hash upsert/delete; Postgres advisory lock + double-checked locking against concurrent first-access duplicate inserts; sync failures try/except'd so endpoints don't 500). Endpoints: `/data /permissions /commit /audit /sync /refresh /sheets /add-row (+batch) /submit`. Permissions (`rbac_grid_permissions.py`): Platform Admin view+edit+sync; Governance Admin view only; others none; viewer/editor groups and editable columns from env (`RBAC_GRID_VIEWER_GROUPS`, `RBAC_GRID_EDITOR_GROUPS`, `RBAC_GRID_EDITABLE_COLS_EDITOR` default `principal_name,privilege`, `..._ADMIN` default ``). Grid does direct multi-edit on replica (no request submission); every commit audited in `rbac_mutation_records` (e.g. `grid_add`); add-row also appends to the physical workbook (sheet-type-aware column order, `_append_row_to_excel`). Sheet-aware columns: UC=9, WS=7, Metastore=8; UI hides internal `id` but keeps it for commit.
Frontend: `RbacGrid.tsx` (per-column filters, global search, Insert Row modal, diff-review modal, role badges, colour tabs), `RbacBundles.tsx`, `RbacDriftReport.tsx`, `RbacRequest.tsx`, `components/RbacStatusCard.tsx`; `Requests.tsx` (search by id/title/type; RBAC Bundles section with status counters; filters: Pending=[pending_approval, approved], Completed=[exported, completed], Failed=[rejected, denied, failed, export_failed]; non-RBAC filters exclude RBAC-only statuses), `Approvals.tsx` (pending bundles + approve). Sidebar "RBAC Admin" under Control Tower gated `allowedPersonas: ['Platform Admin','Governance Admin']`; route `/rbac-grid`.
Gen-1 remnants still present: workbook mutate/compensate/commit tools (`.backup`), Git/SharePoint providers, `rbac_expiry.rego`, `expiry_worker.py`, `send_notification.py`.
OPA files (`backend/policies/`, registered in `manifest.yaml`): `rbac_grants`, `rbac_blast_radius`, `rbac_entitlements`, `rbac_expiry`, `rbac_drift`, `rbac_risk`, `rbac_sod`; `common.rego` modified. Local target sets `agent_tool_opa_enforce: "false"` (shadow mode).
5. Discovery job (`jobs/rbac_privilege_discovery.py`, Databricks notebook, 568 lines)
Purpose: the app lacks account-admin API rights to read grants live, so a scheduled job (SP with read access) snapshots UC grants into Delta; tools (`rbac_discovery_reader`, `rbac_check_existing_grants`, `rbac_detect_drift`, `rbac_detect_privilege_drift`) read the snapshot.
Widgets/env: `env` (dev|stg|prd; or `RBAC_ENV`), `target_catalog` (or `RBAC_TARGET_CATALOG`), `target_schema` default `audit` (`RBAC_TARGET_SCHEMA`), `exclude_catalogs` default `system,__databricks_internal,samples,hive_metastore`. Catalog = `target_catalog` else `enterprise_{env}`; ValueError if neither (FAIL FAST). Target catalog itself is added to the exclude set.
`snapshot_id = run_YYYYmmdd_HHMMSS` (UTC).
Concurrency guard: selects `status='RUNNING' AND run_timestamp > now - 6h` from snapshots table; raises RuntimeError if found ("manually UPDATE its status to 'FAILED'"); missing table on first run tolerated. Writes a RUNNING sentinel row (UPDATEd at end; fallback append). Reason: overlapping MERGE + stale sweep would soft-delete grants the other run hasn't scanned.
Tables (CREATE IF NOT EXISTS, Delta): `{cat}.{schema}.rbac_discovered_grants` (grant_hash, securable_type, securable_full_name, principal_name, privilege, grantor, is_grantable, source_catalog, first_discovered_at, last_seen_at, is_current, removed_at, snapshot_id; TBLPROPERTIES change data feed + optimizeWrite) and `rbac_discovery_snapshots` (snapshot_id, run_timestamp, completed_at, catalogs_scanned, total_grants_discovered, grants_new/unchanged/removed, duration_seconds, status SUCCESS|PARTIAL_FAILURE|FAILED|RUNNING, error_details JSON, scan_scope JSON).
Scan: per catalog, `information_schema.{catalog,schema,table,volume,routine}_privileges` mapped to canonical schema (`IS_GRANTABLE='YES'` -> bool); volume/routine views optional (errors swallowed); other errors appended to `scan_errors`. Metastore-level: `SHOW EXTERNAL LOCATIONS / STORAGE CREDENTIALS / CONNECTIONS`, then `SHOW GRANTS ON <type> `name`` mapping `Principal`/`ActionType`, `source_catalog='metastore'`.
Natural key:
```python
F.sha2(F.concat_ws("|", F.upper(securable_type), F.lower(securable_full_name), F.lower(principal_name), F.upper(privilege)), 256)
```
then `dropDuplicates(["grant_hash"])`.
MERGE: matched -> refresh last_seen_at, is_current=true, removed_at=NULL, snapshot_id, grantor, is_grantable; not matched -> insert. Stale sweep (soft delete): `UPDATE ... SET is_current=false, removed_at=CASE WHEN removed_at IS NULL THEN run_ts ELSE removed_at END, snapshot_id=... WHERE is_current=true AND grant_hash NOT IN (SELECT grant_hash FROM _discovered_grants)`. Metrics via COUNT_IF on snapshot_id. Status SUCCESS or PARTIAL_FAILURE. Recommended cadence 1-4 h; consumer staleness threshold 240 min (per-type SLOs in estate).
`backend/scripts/generate_privilege_registry.py`: CLI (`--provider-version 1.52.0`, `--skip-live > ../rbac/registry/privileges.yaml`) emits `privileges.yaml` to stdout from a baseline KNOWN_PRIVILEGES matrix (2024.12 model: applicable_to, is_admin, inherits, notes e.g. "MANAGE does NOT grant data access") augmented by live INFORMATION_SCHEMA; new tokens flagged for review; preserves deprecated entries from the existing registry; loads backend/.env manually. Run inside venv.
6. Deployment / environment changes
`databricks.yml` diff:
App env vars added: `DATABRICKS_ACCOUNT_ID`, `RBAC_WORKBOOK_PATH`, `RBAC_STORAGE_BACKEND=filesystem`, `RBAC_DISCOVERY_CATALOG`, `RBAC_MUTATION_TABLE`, `RBAC_REPLICA_TABLE`, `RBAC_GRID_REFRESH_SECONDS`, `RBAC_GRID_VIEWER_GROUPS`, `RBAC_GRID_EDITOR_GROUPS`, `RBAC_GRID_EDITABLE_COLS_EDITOR`, `RBAC_GRID_EDITABLE_COLS_ADMIN`, `RBAC_CONFIG_PATH`, `RBAC_SECURITY_APPROVAL_GROUP` (each from a `${var.}`). Defaults: account id "auto"; mutation table `rbac_mutation_records`; replica `rbac_replica`; refresh "30"; viewer/editor groups `" "` (single space); editor cols `principal_name,privilege`; admin cols ``; config path `rbac/config/rbac_config.yaml`; approval group `security-rbac-approvers`. `rbac_workbook_path` default `""` deliberately (MUST be set per target so a missing override is caught at deploy). `rbac_discovery_catalog` default `""`, set per target.
`sync.include` CHANGED: removed `package.json, vite.config.ts, tsconfig.json, tailwind.config.js, postcss.config.js, eslint.config.js, index.html, src/, public/`; kept `app.yaml, backend/, mcp_app/`; added `rbac/` and `scripts/`. So frontend source is no longer synced; a pre-built frontend must be in `backend/static` (gitignored; "to avoid OOM on MEDIUM compute"). Existing excludes include `node_modules/`.
New `local` target (personal dev): host `<dev-workspace-name>`, `app_name: edh-ssc-${workspace.current_user.short_name}`, personal Lakebase instance `edh-ssc-${short_name}-v5`, `rbac_workbook_path: rbac/RBAC_WS_and_UC_Securables_Privileges_updated.xlsx` (bundled with source; mutations don't persist across redeploys), `agent_tool_opa_enforce: "false"`, `account users` CAN_USE, extra LMWS_* env vars.
Per-target values: dev `controltower_dev` + `/Volumes/controltower_dev/framework_agent_operations/agent_volume/RBAC_WS_and_UC_Securables_Privileges_updated.xlsx`; a second dev-flavoured target uses `enterprise_dev` and `/Volumes/enterprise_dev/framework_agent_operations/agent_volume/...`; tst `controltower_tst`; stg `enterprise_stg`; prd `controltower_prd`. All `rbac_mutation_table: rbac_mutation_records`. (Pre-existing malformed `enterprise_stg..data_quality_results` is unrelated.)
Python settings: `backend/app/core/config.py` (+14) and `default_config.py` (+36).
`.gitignore`: `node_modules/`, `.nodeenv/`, `dist/`, `dist-ssr/` (trailing slashes); added `.databricks/`, `_test_deploy/`, `.claude/`; comment on `backend/static/` explaining bundle OOM.
`package.json`: `"build": "test -d node_modules && tsc && vite build || echo 'Skipping frontend build (no node_modules)'"` (was `tsc && vite build`) so deploy without node_modules doesn't fail. Caution: a `tsc` error makes vite build get skipped -> stale JS deployed.
`index.html`: injected `<style>th,td,thead th,thead td,thead {text-transform:none!important}</style>` (CSS uppercase transform was mangling camelCase column headers); `src/index.css` +7 lines and `vite.config.ts` (6 lines) edited in the same camelCase commits.
`backend/.deploy_marker`: single line `deploy_ts=1789401463.084248` - deploy-bust artifact (force a source change so Apps redeploys fresh code); no logic.
`_test_deploy/` (gitignored but committed): `test_app.py` = minimal FastAPI app (`/` uptime, `/health`) that pip-installs `requirements.txt` then runs uvicorn :8000; `requirements.txt` pins full backend deps (fastapi 0.135.2, databricks-sdk 0.100.0, langgraph 1.2.4, openpyxl 3.1.5, pandas 2.3.3, etc.). Purpose: bisect Databricks App startup/OOM crashes. Related: `backend/run.py` instrumented with crash logging (sys.excepthook, SIGTERM/SIGINT handlers, 5s heartbeat printing RSS/threads, crash text uploaded via `WorkspaceClient().workspace.upload` to hard-coded personal path `/Workspace/Users/<developer-email>/RBAC_Automation/_crash_log.txt`; uses the Linux-only `resource` module; uses print). Debug scaffolding - replace with proper logging.
`runtime/rbac/exports/dev/test_export.csv`: content `col1,col2` / `val1,val2` - leftover test artifact from exercising the relative export path.
`backend/app/main.py` SPA hardening: the 404 handler only serves index.html for extension-less paths and never for `/api/`, `/.`, `/_`, `/health`, `/logz`, `/favicon.ico` (misses on `.js/.css/.map` return JSON 404); index is served with `Cache-Control: no-cache, no-store, must-revalidate`, `Pragma: no-cache`, `Expires: 0`; `GET /` 302-redirects to `/?_cb={unix_ts}` unless `_cb` is present (CDN cache-bust); added `GET /api/v1/cache-debug` (static info, js/css refs, `has_style_tag`). All to defeat a stale frontend bundle.
7. Commit history themes (git log 3c832e69..HEAD; messages are terse "Committing ...")
Core build (8fb913c, 5171af8 EDTFNDTN-4048; 35bbcb1 "Config driven"; 9e81e18, 9f7b8ba golden transcripts; 13bd39b "full parity with other workflows"; a063396 gaps; c43d98b placeholder values; 8cbba52 tests + golden transcript harness; da3bc36 hardcoding/wiring gaps; 181cd13 version identification; 45f9ae2 notification; 2da9189 discovery job + tests; 51f19ed Rego policies): 9 workflows, tools, instructions, policies, tests, estate/registry config, notification, discovery job.
Grant flow wired to Lakebase (42bb038 bug fixes, 80887c7 workflow corrections, 952c1db Lakebase mutation table, 60e31f7 rbac mutation, 4556f2f grant flow wired to Lakebase for state, f885cfc workflow details shown, 66283ef validation, 8283161 unified "ask me anything" view): `rbac_mutation.py`, state persistence, UI details.
Grid / replica (3c41a2c grid/view, dc25719 path sync + grid creation, 74641fa per-column search + global search + insert-row, 4583a66 merge origin/develop, 9dc54c7 sync excel): rbac_replica, rbac_sync, rbac_grid, permissions, RbacGrid.tsx; workbook path sync (Volumes/Workspace/external S3 volumes).
Bug fixes: 946b2ab "internal 500 error" (workflows.py, migrate.py, workflow_service.py: migration/schema robustness, workflow listing 500); 83e9134 grid sync (rbac.py, rbac_sync, render.py, spec_loader.py, tool_registry.py, poller.py, model_serving client); d65413a generic `governance_search` tool.
camelCase saga + stale frontend (9f546b3, 11a30e7, bb8807b, ab72586, 5578f72): grid column headers/keys appeared uppercased/mangled in the UI. Fixes: global CSS `text-transform:none` on th/td (index.html + index.css), RbacGrid.tsx key/header handling, vite.config.ts changes, main.py cache-busting/no-store SPA serving + cache-debug endpoint. 5578f72 removed a `package.json` that was producing an old stale frontend build in the deploy. Root issue: deployed app kept serving an old bundle (CDN/browser cache plus build silently skipped).
Stale request verification (353800c): `db.refresh(req)` + status recheck in duplicate check; Requests.tsx.
Docs (ec37e9b, 10bd97a README/CHANGELOG).
Lifecycle experiment then simplification (00477ff -> db1f1e5): V2 6-table lifecycle with `/rbac-lifecycle` API, `rbac_v2_config.yaml`, `build_frontend.sh` (nodeenv), RbacMyRequests/RbacBundleApprovals pages added then deleted; unified design on `requests`+`bundle_id`; enum validation from rbac_config.yaml on grid commit + add-row; auto-deny writes in non-dev.
Approval/export automation (0e160d2 "RBAC automation": RbacBundles.tsx, request.py hook, grid endpoints; 17b3c19 "the split and writing into different env paths": per-env CSV export, per-env exportPath, rbac_uc_grant.json + instructions rewritten, Approvals/Requests UI, golden transcripts; f06c0ca changelog/readme; 51766ff "code changes enum": rbac_config.yaml +270 lines fieldPatterns/crossFieldConsistency/denyPatterns/knownPrefixes, rbac_grid.py +274 validators, request.py +41).
Other changelog-recorded fixes: Sidebar TS build blocker (unused `Package` import from lucide-react made `tsc` fail so `vite build` was silently skipped and stale JS deployed; also removed rbac_bundles nav); Requests.tsx TS errors (`resultSummary: undefined` on two synthetic step objects; unused destructured `k` -> `_k`); `target_sheet` NOT NULL on mutation record; export filter now includes revoke; auto-sync race (advisory lock); stale input after refresh (`<input key={row.id-row.row_version}>`); `RbacReplicaRow` must be imported in `db/__init__.py` so `create_all` registers it; `metadata` field = `req.state_context` for request detail (requests.py). Authors named in docs: Aravind Ratha (changelog/release notes), Mano (validation), reviewer "Genie Code".
8. Gotchas / safety mechanisms (checklist)
Mutation disabled unless estate.yaml present+valid+versioned; check `mutation_enabled`, not `valid`.
Feature gates fail closed (role_assumption false; preview privileges). Unsupported securables fail closed with actionable reason.
rego: `package rbac.grants` (no `data.`); `import rego.v1`; `default decision` as object; singular `workspace_entitlement_mode`.
Deprecated/rejected tokens: test dict key presence since `null` is meaningful.
Pydantic silently drops unknown tool args (e.g. `require_feature`); gates built on them never fire. Use post_check.
Sync SDK in async code must use `asyncio.to_thread`; close parsers in `finally`.
Packed-cell workbook format (`;`-separated values within cells) required for backward compat with the existing Terraform parser; trailing separators stripped; serializer routes target sheet by securable type; keep `.backup`.
Excel limits: concurrent mutation is last-write-wins; openpyxl fails if file locked.
Container-local `/tmp` and bundled `rbac/` workbook are ephemeral across redeploy; use `/Volumes/...` or `/Workspace/...` for persistence; app SP needs write permission there.
Read-only envs: only dev allows writes via agent/automation; tst/stg/prd write privileges, revoke, modify auto-denied; SPs not grantable via agentic flow outside dev.
Hard-block list (ADMIN, IS_OWNER, MANAGE, ALL_PRIVILEGES, CREATE_CATALOG, ...) no override; escalation list flagged for security approval.
Self-approval denied; approver must be active; material change invalidates collected approvals.
Validation parity: the same enum + field + cross-field rules must run in grid commit, add-row, batch add-rows (validate ALL rows before any DB write, fail fast) and the agent before_insert hook. Env-suffix mismatch (e.g. `enterprise_dev` + `_ws_prd`) rejected; federated catalogs exempt.
Junk-data deny regex (3+ consecutive consonants) can reject real words; enum-governed columns exempt; legit unusual schema names enumerated as exceptions.
Bundle id is hour-granular (`RBAC-{date}T{hour}`): same-hour requests share a bundle; approval is per bundle.
Duplicate check: replica authoritative; audit-only hit must not block; always re-read status (stale in-flight rows).
Discovery job concurrency sentinel (6h window): a crashed run leaves a RUNNING row that blocks further runs until manually set FAILED.
Freshness: warn when snapshot older than 240 min (per-type SLOs).
Frontend deploy fragility: tsc error -> vite build skipped -> stale bundle; sync.include no longer ships src/, so the bundle must be built before deploy into `backend/static`; SPA 404 fallback must not serve index.html for assets; cache-bust redirect on `/`.
Postgres advisory lock for first-access sync; `row_version` optimistic concurrency conflicts on stale edits.
Debug endpoints (`/debug/export/{id}` unauthenticated, `/api/v1/cache-debug`) and `run.py` crash logging with a hard-coded personal workspace path are dev scaffolding.
Terraform state is evidence only; only live state (discovery snapshot / effective permissions) verifies access.
Agent must not fabricate justification, must never skip the confirm gate, must not suggest justification text.
02 - Tools added/changed (RBAC automation, branch feature/EDTFNDTN-4251)
Scope: diff from merge-base 3c832e69 to HEAD for `backend/app/tools/` and `backend/app/workflows/tool_registry.py`. Paths below are relative to `edh-self-service-center/backend/app/`.
All RBAC tools live in `tools/self_service/` and are registered with the `@tool(...)` decorator from `app.tools.mcp` (kwargs used: `name`, `description`, `args_schema` (Pydantic model), `side_effect_class` in {`read`,`data_grant`,`infra`}, `external`, `friendly_label`, `friendly_completion_label`). Tools are sync (`def`) or `async def`. They return plain dicts with `success` plus either `error` or a payload; they generally do not raise.
---------------------------------------------------------------------
0. Architecture note (read first)
Two design generations coexist at HEAD:
Original design: the Excel workbook `RBAC_WS_and_UC_Securables_Privileges.xlsx` is the system of record. Tools parse it (`rbac_workbook_parser`), mutate it (`rbac_workbook_serializer`), commit it to Git/PR (`rbac_commit_workbook`); a Terraform parser applies it later. Storage is abstracted by `rbac_workbook_storage` (filesystem / SharePoint / Git).
Current design (commit "simplify RBAC to unified design"): the Lakebase table `rbac_replica` (model `RbacReplicaRow`, `app/db/rbac_replica.py`) is the single source of truth. `rbac_mutate_workbook` NO LONGER writes the xlsx. It writes an audit row (`RbacMutationRecord`, `app/db/rbac_mutation.py`) and upserts/deletes rows in `rbac_replica`. The workbook is a read-only initial seed. Agent RBAC requests park at `pending_approval` (see execute_workflow) and a Platform Admin approves/exports a "bundle" through REST endpoints (`/rbac-grid/bundle/{id}/approve`, `/export`) that are outside this section.
Consequence: several read/verify tools (`rbac_read_workbook`, `rbac_check_existing_grants`, `rbac_resolve_prerequisites` existing-check, `rbac_validate_changes`, `rbac_detect_drift`) still read the xlsx, not the replica. Only the idempotency guards in `rbac_mutate_workbook` and `execute_workflow` read the replica. A re-implementation should pick one source of truth and apply it consistently (see section 8).
External config required (outside this section): `rbac/estate.yaml` (sections estate / policy / workbook / discovery), `rbac/registry/privileges.yaml`, `providers.yaml`, `securables.yaml`, OPA policy `rbac_grants.rego`.
Settings consumed (via `app.core.config.settings`, mostly `getattr(..., default)`): `RBAC_ESTATE_PATH`, `RBAC_REGISTRY_PATH`, `RBAC_WORKBOOK_PATH`, `RBAC_STORAGE_BACKEND` (filesystem|sharepoint|git|auto), `RBAC_WORKBOOK_REPO`, `RBAC_WORKBOOK_REPO_PATH`, `RBAC_WORKBOOK_GIT_BRANCH` (default main), `RBAC_BRANCH_PREFIX` (default `rbac/`), `RBAC_WORKBOOK_BACKUP_SUFFIX` (default `.backup`), `RBAC_HTTP_TIMEOUT_SECONDS` (60), `RBAC_SQL_WAREHOUSE_ID` (also env), `SQL_WAREHOUSE_ID`, `AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET`, `SHAREPOINT_SITE_ID/DRIVE_ID/FILE_PATH`, `GITHUB_TOKEN/GITHUB_ORG/GITHUB_WEB_BASE_URL/GITHUB_API_BASE_URL`, `get_git_token()`, `NOTIFICATION_SLACK_WEBHOOK_URL`, `NOTIFICATION_TEAMS_WEBHOOK_URL`, `ENVIRONMENT`, `get_workspace_client()`, `opa_provider_config()`.
---------------------------------------------------------------------
1. Summary table
Tool name
File
One-liner
R/W
rbac_read_estate_config
tools/self_service/rbac_read_estate_config.py
Load+validate `rbac/estate.yaml`, auto-detect account_id, gate `mutation_enabled`
read
rbac_validate_privilege
.../rbac_validate_privilege.py
Validate/normalize privilege vs registry + provider map; flags + prerequisites
read
rbac_resolve_prerequisites
.../rbac_resolve_prerequisites.py
Compute USE_CATALOG/USE_SCHEMA/READ_* chain; mark which exist (xlsx)
read
rbac_resolve_principal
.../rbac_resolve_principal.py
SCIM lookup of user/SP/group; system-group, role-prefix checks; principal-exception helpers
read
rbac_list_securables
.../rbac_list_securables.py
List securable types / validate name format / expand packages from `securables.yaml`
read
rbac_read_workbook
.../rbac_read_workbook.py
Parse xlsx to canonical records with filters
read
rbac_check_existing_grants
.../rbac_check_existing_grants.py
Existence check in xlsx + Delta discovery; MV/ST ownership-transfer guard
read
rbac_mutate_workbook
.../rbac_mutate_workbook.py
The write tool: validate, OPA, prerequisites, idempotency guard, audit row, replica upsert/delete
write (data_grant)
rbac_compensate_workbook
.../rbac_compensate_workbook.py
Build (default dry-run) or execute inverse mutation via rbac_mutate_workbook
write (data_grant)
rbac_commit_workbook
.../rbac_commit_workbook.py
Filesystem: add fact + mark audit rows committed. Git: branch+commit+PR
write (infra)
rbac_validate_changes
.../rbac_validate_changes.py
Cross-check Lakebase audit vs xlsx -> verdict
read
rbac_capture_pre_state
.../rbac_capture_pre_state.py
Snapshot live UC effective grants before apply
read (live UC)
rbac_compensate
.../rbac_compensate.py
Classify partial apply results; emit rollback/retry plan (plan only)
read
rbac_verify_outcome
.../rbac_verify_outcome.py
Live UC verification -> outcome enum, `can_close_request`
read (live UC)
rbac_get_effective_access
.../rbac_get_effective_access.py
Read live UC effective permissions
read (live UC)
rbac_detect_drift
.../rbac_detect_drift.py
Diff xlsx grants vs Delta discovery snapshot
read
rbac_detect_privilege_drift
.../rbac_detect_privilege_drift.py
Diff privilege registry vs live tokens
read
rbac_get_workspace_entitlement_mode
.../rbac_get_workspace_entitlement_mode.py
legacy vs explicit workspace entitlement mode
read
send_notification
.../send_notification.py
Email/Slack/Teams workflow completion notice
non-mutating (declared read)
(library) rbac_discovery_reader
.../rbac_discovery_reader.py
Delta discovery-table reader helpers
read
(library) rbac_models, rbac_workbook_parser/serializer/storage, sharepoint_provider
.../
Models, xlsx IO, storage backends
n/a
governance_search
tools/governance/governance_search.py
Keyword fan-out over assets/requests/approvals/tools tables
read
execute_workflow (changed)
tools/execute_workflow.py
Alias normalization, RBAC duplicate guard, park RBAC requests at pending_approval
write
McpTool.__call__ (changed)
tools/mcp.py
Direct call of tools from other tools
n/a
tool_registry (changed)
workflows/tool_registry.py
pkgutil self-service tool scan
n/a
---------------------------------------------------------------------
2. Data model, parser, serializer, storage (re-implementation spec)
2.1 rbac_models.py (Pydantic v2)
Enums (str, Enum):
PrincipalType: user | group | service_principal
SecurableType: metastore, catalog, schema, table, view, materialized_view, streaming_table, volume, external_location, storage_credential, connection, function, model, share, recipient, provider, workspace, group, role_group
AccessPlane: identity, workspace, workspace_acl, uc_grant, execution
GuardrailTier: hard_deny, extra_approval, warn, allow
VerificationOutcome: verified_granted, verified_removed, residual_access, indeterminate, failure (API 2xx never closes a request; only verified_* may)
RiskTier: standard, elevated, high, critical
RetryClass: retryable_transient, retryable_stale, non_retryable, compensating
RequestLifecycleState: draft, validating, awaiting_approval, approved, planning, plan_confirmed, applying, partial_applied, committing, merge_pending, verifying, completed, rejected, cancelled, expired, stale_plan, compensating_required, manual_remediation, conflict
WorkbookSheetType: workspace_rbac, metastore, uc_securable, sample (sample skipped)
EntitlementMode: legacy, explicit
RbacCanonicalRecord (atomic grant): environment, principal_type, principal_name (min 1), securable_type, securable_name (dotted FQN, min 1), privilege (min 1), optional owner, justification, expiry (datetime), request_id, intent_hash (SHA-256 of principal+env+target+privileges+scope+classification+expiry+incident_ref; approvals bind to it), risk_tier, provenance source_sheet, source_row, is_packed_origin. Validators: privilege strip+upper; principal_name/securable_name stripped. `identity_key` = `env|principal_type|principal_name|securable_type|securable_name|privilege`. The model has NO catalog/schema_name/object_name/requested_by_email fields (code calls getattr on them and always gets None).
WorkspaceRbacRecord: environment, workspace_id, principal_type, principal_name, access_plane, entitlement_or_acl, value (default "granted"), owner, source_sheet, source_row, is_packed_origin. identity_key = `env|principal_name|plane|entitlement`.
WorkbookSheetConfig{name, sheet_type, header_row=0, columns{canonical->header}, packed_columns, skip}; WorkbookConfig{file_name="RBAC_WS_and_UC_Securables_Privileges.xlsx", sheets, get_sheet(), active_sheets}.
EstateConfig (estate: section): cloud (aws|azure|gcp), account_id, provider_version, privilege_model_version, identity_federation (legacy|identity_federated), workspace_entitlement_mode (legacy|explicit), idp_type (entra|okta|other), provisioning_mode (scim|aim|hybrid), git_provider="github", terraform_runner (existing-gitops|tfe|atlantis|spacelift), verification_workspace?, default_timezone="UTC". Mutation stays disabled until this validates. account_id validator:
```python
placeholders = {"REPLACE_WITH_ACCOUNT_ID","YOUR_ACCOUNT_ID","PLACEHOLDER","TODO","TBD",""}
if stripped.upper() in placeholders or stripped.startswith("REPLACE"): raise ValueError(...)
if not re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", stripped.lower()): raise ValueError(...)
```
PrivilegeDefinition{token, display_name, applicable_to[SecurableType], prerequisites[str], inherits_to_children, is_administrative, deprecated, preview_only, verifier, feature_flag, test_gate, status="ga", notes}. PrivilegeRegistry{provider_version, privileges}: get_privilege(token) (upper/strip exact), valid_tokens_for(securable_type) (applicable and not deprecated).
SecurableDefinition{securable_type, display_name, parent_type, naming_pattern, dot_segments, supports_ownership_transfer, ownership_transfer_constraints, cloud_specific, preview_only}; SecurableRegistry.validate_name = `name.count(".") == dot_segments`. (rbac_list_securables does not use this class; it reads raw YAML.)
ProviderMapping{provider_version, token_map, deprecated_tokens}.normalize(token): upper; if deprecated -> replacement (None/"" raises ValueError "deprecated with no replacement"); elif in token_map -> mapped; else unchanged.
MutationDelta{action add|remove|modify, record, previous_record?, target_sheet, target_row?, requires_repack}; MutationPlan{request_id, deltas, policy_result, prerequisites_added, warnings, blocked, block_reason, intent_hash, risk_tier, sod_result}; total_changes = len(deltas)+len(prerequisites_added).
2.2 Workbook format
Sheets (env suffix Dev/Tst/Stg/Prd): `WS_Securables_RBAC_<Env>` (workspace level; legacy `WS_RBAC_`), `UC_Securables_RBAC_<Env>`, `UC_metastore_RBAC` (shared across envs), plus a SAMPLE sheet that is skipped.
UC columns (9): `Environment, Securable Type, Catalog, SchemaName, ObjectName, Owner, PrincipalType, PrincipalName, AccessPrivilege`. UC_metastore_RBAC has 8 (no ObjectName). WS columns: `Environment, Workspace ID, Principal Type, Principal, Entitlement, Value, Owner`.
Packed cells: one cell holds several values separated by `;` in PrincipalName and/or AccessPrivilege (WS: Principal/Entitlement). Parsed as cartesian product principals x privileges; `is_packed_origin=True` if either side has more than one value.
2.3 rbac_workbook_parser.py
`RbacWorkbookParser(path, config=None)`: openpyxl `load_workbook(read_only=True, data_only=True)`; `WorkbookParseError` if missing/unopenable. Non-fatal problems accumulate in `.parse_errors`. `close()` required (tools use try/finally).
```python
PACKED_SEPARATOR = ";"
UC_COLUMN_DEFAULTS = {"environment":"Environment","securable_type":"Securable Type","catalog":"Catalog","schema_name":"SchemaName","securable_name":"ObjectName","principal_type":"PrincipalType","principal_name":"PrincipalName","privilege":"AccessPrivilege","owner":"Owner"}
WS_COLUMN_DEFAULTS = {"environment":"Environment","workspace_id":"Workspace ID","principal_type":"Principal Type","principal_name":"Principal","entitlement_or_acl":"Entitlement","value":"Value","owner":"Owner"}
```
Principal type map: user, group, service_principal, sp, "service principal"; unknown/blank defaults to GROUP.
Securable type: lower, replace "-" and " " with "_", look up in a map of all SecurableType values (underscore and space variants). Fallback by sheet-name substring: catalog, schema, table/view -> TABLE, volume/extloc -> VOLUME, metastore; else None -> row error "Unknown securable type".
Sheet discovery: with config use UC_SECURABLE/METASTORE (or WORKSPACE_RBAC) non-skipped sheets; heuristic: UC = name starts `UC_` or equals `metastore` (case-insens); WS = starts `WS_`.
`_find_headers`: scan first 5 rows; per field the first column where `header_lower in cell_text or cell_text in header_lower` (lowercase, substring either way). Accept if >= 3 fields matched; else (0, None) -> error "Could not find headers". Gotcha: empty cells become "" and `"" in header_lower` is True, so blank leading cells can match.
UC row: securable_name = ".".join(non-empty of Catalog, SchemaName, ObjectName) (fallback ObjectName); skip rows with empty securable and principal; environment default "all"; source_row is the 1-based Excel row (`enumerate(rows[header+1:], start=header+2)`).
WS row: environment from cell or inferred from sheet name (last "_" part lowercased if >= 3 parts, e.g. WS_RBAC_Dev -> dev, else "all"); value default "granted"; access_plane always workspace assignment.
Per-row exceptions caught and appended to parse_errors.
`parse_workbook(path, config)` returns {uc_records, ws_records, errors, summary{total_uc_records, total_ws_records, total_errors, packed_uc_records, unique_principals, unique_securables, sheets_parsed}}.
2.4 rbac_workbook_serializer.py
`RbacWorkbookSerializer(path)` loads the full (non read-only) workbook. Principle: only affected rows are rewritten; save to a new path or after `save_backup()` (`shutil.copy2` to `<file>.xlsx.backup`). `WorkbookSerializeError` on failure.
`apply_mutation_plan(plan)`: raises if plan.blocked; groups deltas by target_sheet; prerequisites become add deltas to `determine_target_sheet(prereq)`; missing sheet is an error. Per sheet: removes first, then modifies, then adds. Returns change strings (`ADD [sheet]: principal -> priv on securable`, `REMOVE`, `REPACK-ADD`, `REPACK-REMOVE`, `MODIFY`).
`_add_record`: if requires_repack -> `_repack_add`: find a row with same env (case-insens) and securable_name; append principal to the packed cell as `" ; ".join(parts)` if absent; else append new row. Otherwise `ws.append(row)`.
`_remove_record`: packed origin + source_row -> `_repack_remove` (remove principal from principal cell, else privilege from privilege cell; if list becomes empty delete the row); else delete `target_row`; else `_find_and_remove` (match principal+securable+privilege upper, scanning from row 2).
`_modify_record` = remove previous (by source_row) + add new.
`_record_to_row`: splits securable_name on "." into catalog/schema/object. 9-col: `[env, sec_type, catalog, schema, object, owner or "", principal_type, principal_name, privilege]`; `UC_metastore_RBAC` 8-col: `[env, sec_type, catalog, schema, owner or "", p_type, principal, privilege]`.
`determine_target_sheet(record)` (static; still used by mutate and execute_workflow):
```python
_ENV_SUFFIX = {"dev":"Dev","tst":"Tst","test":"Tst","stage":"Stg","stg":"Stg","prd":"Prd","prod":"Prd"}
env = (record.environment or "").strip().lower() or settings.ENVIRONMENT.lower()
suffix = _ENV_SUFFIX.get(env, "Dev") # unknown env (incl. "all") -> Dev
_METASTORE_TYPES = {METASTORE, CONNECTION, SHARE, RECIPIENT, PROVIDER} -> "UC_metastore_RBAC"
_WS_TYPES = {WORKSPACE, GROUP, ROLE_GROUP} -> f"WS_Securables_RBAC_{suffix}"
else -> f"UC_Securables_RBAC_{suffix}"
```
`_get_col_map` reads header from row 1 only; uses WS defaults only if the sheet name starts with `WS_RBAC` (not `WS_Securables_RBAC`); needs >= 3 matches.
At HEAD the serializer's apply/save is not called by any tool here.
2.5 rbac_workbook_storage.py
Facade `WorkbookStorage` with drivers chosen by `from_settings()`:
`RBAC_STORAGE_BACKEND == "sharepoint"` -> SharePoint driver; `"git"` -> Git driver.
Otherwise (filesystem/auto) auto-detect from `RBAC_WORKBOOK_PATH` (required, else ValueError): `https://...sharepoint...` -> SharePoint (file_path = SHAREPOINT_FILE_PATH or path); `github:` prefix or `https://...github...` -> Git (file_path = RBAC_WORKBOOK_REPO_PATH or path); else filesystem.
API: sync `acquire()/release()`, async `acquire_async()/release_async()`, `local_path` (RuntimeError before acquire), `backend_name`, `create_backup(suffix="")` (copy2 to `<local>.<suffix>`, default suffix RBAC_WORKBOOK_BACKUP_SUFFIX), `cleanup()` (idempotent), `path_info()`, classmethod `resolve_path()` (filesystem only; raises NotImplementedError for sharepoint/git so callers fall back to `from_settings().acquire()`), static `_classify_path(path)` -> {path_type, persistent, resolved_path}: sharepoint | github | uc_volume (`/Volumes/`) | workspace (`/Workspace/`) | bundle_local. Only bundle_local is non-persistent (WARNING logged: a bundle deploy resets runtime changes).
Filesystem driver: relative paths resolve against the repo root (not CWD). `acquire()` runs `_preflight_checks()`, collecting ALL problems into one `WorkbookStorageError`: empty path; UC-volume structure `/Volumes/<catalog>/<schema>/<volume>/<file>` (>= 6 parts) with placeholder tokens (REPLACE, TODO, XXX, CHANGEME, example) rejected and volume root existing; workspace parent exists; parent dir exists; file exists; readable; writable; extension .xlsx/.xlsm; size not 0 and >= 4096 bytes; `zipfile.is_zipfile`. `release()` no-op.
SharePoint driver: temp dir `rbac_sp_`, file `rbac_workbook.xlsx`; acquire downloads, release uploads back (returns webUrl); cleanup removes the temp dir.
Git driver: temp dir `rbac_git_`; acquire GETs `{api_base}/repos/{org}/{repo}/contents/{path}?ref={branch}` with `Accept: application/vnd.github.v3.raw` and Bearer token; release is a no-op (commit tool pushes). api_base: GITHUB_API_BASE_URL, else `https://api.github.com` for github.com, else `{web_base}/api/v3` (GHE).
2.6 sharepoint_provider.py (Microsoft Graph)
`SharePointProvider(tenant_id, client_id, client_secret, site_id, drive_id)`. OAuth2 client credentials: POST `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token` with `scope=https://graph.microsoft.com/.default` (app needs Sites.ReadWrite.All or Files.ReadWrite.All). Content URL `https://graph.microsoft.com/v1.0/sites/{site_id}/drives/{drive_id}/root:/{path.lstrip('/')}:/content`. Async (`async with`): `download_file(path, local)`, `upload_file(local, path)` -> webUrl. Sync: `download_file_sync`, `upload_file_sync` (fetch a token per call). Upload is a simple PUT with `application/octet-stream`: no etag/If-Match (no concurrency control) and Graph simple upload is limited to about 4 MB. Timeout = RBAC_HTTP_TIMEOUT_SECONDS.
---------------------------------------------------------------------
3. Tool-by-tool reference
3.1 rbac_read_estate_config (read)
Input: `config_path?`. Path: arg -> settings.RBAC_ESTATE_PATH -> env RBAC_ESTATE_PATH -> `<repo root>/rbac/estate.yaml`.
Output: {estate, policy, workbook, mutation_enabled, validation_errors[], config_path, message}. Missing file / invalid YAML / non-dict -> mutation_enabled false with errors. `estate_dict = EstateConfig.model_dump()` then `.update(policy_section)` so OPA can read `input.estate.system_groups` etc. `mutation_enabled` = no validation errors. Every write/check tool calls this first; not cached.
account_id auto-detect: if the YAML value is a placeholder (REPLACE_WITH_ACCOUNT_ID, YOUR_ACCOUNT_ID, PLACEHOLDER, AUTO, TODO, TBD, startswith REPLACE) call `_auto_detect_account_id()`: env DATABRICKS_ACCOUNT_ID -> `metastores.summary().owner` if UUID -> SDK `config.account_id` if UUID -> UUID part of `global_metastore_id` (split ":") -> `metastore_id`. The last two are metastore UUIDs, not real account ids ("sufficient for Phase 1"). None -> EstateConfig validation fails.
Stores: YAML file; Databricks SDK only for auto-detect.
3.2 rbac_validate_privilege (read)
Input: `privilege`, `securable_type`, `include_prerequisites=True`.
Output: valid, original_token, normalized_token, securable_type, prerequisites[{privilege,reason}], warnings, error, is_deprecated, is_administrative, inherits_to_children, display_name, notes.
Logic: load privileges.yaml into PrivilegeRegistry (registry path: RBAC_REGISTRY_PATH -> env -> `<root>/rbac/registry`) and optional providers.yaml into ProviderMapping. Normalize: upper/strip; in `deprecated_tokens` with no replacement (None/""/"null") -> REJECT (error "deprecated and REJECTED"); with a replacement -> warn and use it; else apply token_map. Securable type: `lower().replace(" ","_").replace("-","_")` into SecurableType (error lists valid types). Then: token must exist in registry; securable_type in `applicable_to`; registry `deprecated` -> error; `preview_only` -> warning; WIDENING warning when inherits_to_children and securable is catalog/schema. Prerequisites straight from the registry.
3.3 rbac_resolve_prerequisites (read)
Input: `privilege, securable_type, securable_name, principal_name?, check_existing=True`.
Output: {target_privilege, target_securable, target_type, principal, prerequisites[{privilege,securable_type,securable_name,reason}], already_granted, needs_granting, totals, error}.
Blank principal_name returns an error dict.
Chain = registry prerequisites mapped by `_resolve_prereq_target`: USE_CATALOG -> catalog (first segment; skipped when the target is a catalog); USE_SCHEMA -> `catalog.schema` (needs >= 2 segments; skipped when target is a schema); READ_VOLUME / READ_FILES -> same securable. Hardcoded extras: WRITE_VOLUME adds READ_VOLUME; WRITE_FILES adds READ_FILES.
Existing check parses the xlsx (RBAC_WORKBOOK_PATH repo-relative; missing/failed parse -> all treated as missing), key `principal.lower()|securable.lower()|PRIV`; ignores environment and the replica.
The tool description says prerequisites are "DISPLAYED ONLY ... NEVER silently auto-added" (G-06), but `rbac_mutate_workbook(auto_prerequisites=True)` adds them by default. Intended flow: show to user, confirm via confirm_mutation_plan gate, then mutate.
3.4 rbac_resolve_principal (read, async)
Input schema: principal_name, principal_type="group", reject_system_groups=False, validate_role_pattern=False, environment?, exception_context? (the last two are in the schema but NOT in the function signature).
Module-level policy helpers: `get_system_groups()` (policy.system_groups lowercased; default {users, admins, account users}), `get_role_group_prefix()` (default "role-"), `get_principal_exceptions()` (policy.allowed_principal_exceptions), `is_principal_exception_allowed(principal_type, environment, context)` (G-04): groups always allowed; otherwise find an exception matching principal_type, optional `environments`, optional `context`, and not past `sunset_date` -> {allowed, exception, requires_approval}.
Flow: empty -> error. System group: with reject_system_groups -> {success False, is_system_group True}; else success True with warning, source "platform". validate_role_pattern requires the prefix. SCIM lookup in a thread (`asyncio.to_thread`): users `userName eq "<n>"`; service principals `displayName eq "<n>" or applicationId eq "<n>"` (returns applicationId as principal_name); groups `displayName eq "<n>"`. Injection guard: `safe_name = p_name.replace('"', '\"')`. Provenance: externalId present -> scim_federated; group meta.resourceType containing "Account" -> account; user default local; SP platform. Not found -> {success False, exists False}.
3.5 rbac_list_securables (read)
Input: securable_type?, securable_name?, package_name?, catalog_filter?, schema_filter?, check_ownership_constraints=False.
Reads securables.yaml raw dict: `securables` (type -> name_format, dot_segments, parent_prerequisites, supports_inheritance, valid_privileges, ownership_constraints) and `packages` (name -> description, securable_type, securables[] | pattern). Modes: package (static list filtered by `catalog_filter` via startswith(cat+".") and `schema_filter` via ".schema." in name; dynamic packages return `["<dynamic:PATTERN>"]` unexpanded; unknown package returns available_packages); type (info; with securable_name returns `name_valid = name.count(".") == dot_segments` and name_error); no args (all types).
3.6 rbac_read_workbook (read)
Input: filter_principal, filter_securable (fnmatch, case-insens), filter_privilege (exact upper), filter_environment (case-insens equality), sheet_type ('uc'|'workspace'|None), limit=100.
Path via `WorkbookStorage.resolve_path()`; on NotImplementedError uses `from_settings().acquire()`. Output {uc_records[], ws_records[], summary{total_uc_records, total_ws_records, _unfiltered, truncated, packed_records, unique_principals, parse_errors}, parse_errors[:10]}. Missing file returns an `error` key (no `success`). Limit applies per list after filtering. WS records filter only by principal and environment.
3.7 rbac_check_existing_grants (read)
Input: principal_name (req), securable_name?, securable_names?[], privilege?, check_type "existence"|"ownership_transfer_safety", securable_type?, environment?.
Requires estate mutation_enabled. Parses xlsx UC records, case-insensitive filters.
ownership_transfer_safety: matching records of type materialized_view or streaming_table go to `pipeline_restricted[]`; `safe_to_proceed` = none restricted (MV/ST are refreshed by a pipeline SP; changing owner can break refresh).
existence: workbook matches (cap 20) plus live matches from Delta via `rbac_discovery_reader.check_grant_exists` (cap 20) and `check_discovery_freshness()`. Delta failure is non-fatal (live_state.error). Flags grant_exists, in_workbook, in_live_state, and `drift_hint` in {consistent, in_workbook_only, in_live_only, not_found}.
3.8 rbac_mutate_workbook (WRITE, side_effect_class="data_grant", sync)
Input: action ('grant'|'revoke'|'modify'), privilege, securable_type, securable_name, principal_name, principal_type="group", environment (required in schema; function default "all"), owner?, justification?, request_id?, auto_prerequisites=True, expires_at? (only used as a `has_expiry` flag; NOT stored), dry_run=False.
Pipeline (failures return {success False, error...}, no raising):
`rbac_read_estate_config()`; not mutation_enabled -> "Mutation is DISABLED: ...".
`rbac_validate_privilege`; invalid -> error; use normalized_token.
`PrincipalType(principal_type.strip().lower())`; 3. `SecurableType(securable_type.strip().lower().replace(" ","_"))`.
OPA policy input:
```python
{"request": {"privilege","securable_type","securable_name","principal_type","principal_name","environment","is_administrative","inherits_to_children","has_expiry"}, "estate": estate_dict}
```
tier hard_deny -> {success False, error "Request BLOCKED by policy (hard_deny).", policy_messages, action_required}. extra_approval and warn do not block here; they surface in warnings/policy_messages (approval handled upstream by workflow/bundle).
Build RbacCanonicalRecord; request_id defaults to `rbac-YYYYmmddHHMMSS` (UTC).
If auto_prerequisites and validate returned any: `rbac_resolve_prerequisites(check_existing=True)`; each `needs_granting` becomes a record (same env/principal/owner/request_id); creation errors logged and skipped.
target_sheet = `RbacWorkbookSerializer.determine_target_sheet(record)`.
action_map {"grant":"add","revoke":"remove","modify":"modify"} (else error); build in-memory MutationPlan. dry_run returns {success True, dry_run True, mutation_plan{request_id, action, target_record, target_sheet, prerequisites_to_add[], total_changes, policy_tier, policy_messages, warnings}, message} and writes nothing.
Idempotency guard `_check_mutation_already_applied`; if exists returns {success True, already_applied True, source, message} (success so retried workflows proceed). Each layer is try/except (logged, falls through):
Layer 1 audit: RbacMutationRecord with same request_id, action == action.lower(), principal_name, securable_name, privilege and status in ("applied","committed").
Layer 2 replica: RbacReplicaRow with principal, privilege, environment and catalog/schema_name/object_name matching the dotted parts supplied. grant + row exists -> "Grant already exists ... Duplicate submission rejected"; revoke + row absent -> "Grant already revoked ... Duplicate revoke rejected". No sheet_name filter; not applied to modify.
Apply: `changes = [{"action","target_sheet","record"}]`; (a) `_persist_mutation_audit` (NON-FATAL, errors only logged; single commit, rollback on error): one RbacMutationRecord (uuid id, request_id, action, environment, securable_type, securable_name, catalog/schema_name/object_name via getattr (None), principal_type/name, privilege, owner, target_sheet, status="applied", requester_email (None), justification, change_detail{changes_applied, policy_tier, policy_messages, prerequisites_count}) plus one row per prerequisite (action "grant", change_detail{"is_prerequisite":True,"parent_request":request_id}); (b) `_sync_mutation_to_replica` (FATAL: failure returns {success False, "Replica mutation failed: ..."}).
Success return: {success, dry_run False, request_id, changes_applied, total_changes, policy_tier, policy_messages, warnings, prerequisites_added (int), message "...Ready for bundle approval and export."}.
`_sync_mutation_to_replica(action, record, target_sheet, prereq_records, actor_email)` writes rbac_replica (RbacReplicaRow; `compute_row_hash` in app/db/rbac_replica.py):
securable_name -> (catalog, schema_name, object_name) by splitting on "."; for the WS sheets (WS_Securables_RBAC_{Dev,Tst,Stg,Prd}) the whole name goes in object_name, catalog/schema None.
grant/modify = upsert keyed by (sheet_name, principal_name, privilege, environment) + (WS: object_name | UC: catalog+schema_name+object_name). Existing row: update securable_type, owner, principal_type, request_id, `row_version += 1`, updated_at, `updated_by = actor_email or "agent"`, row_hash. New row: row_version=1, workspace_name=None, `row_hash = compute_row_hash(target_sheet, env, sec_type, catalog or "", schema or "", object or "", owner or "", p_type, p_name, priv, "")`.
revoke = DELETE matching rows (`synchronize_session="fetch"`).
prerequisites (always grants): exists-check filters sheet_name, principal, privilege, env and catalog only; inserts with owner None.
Single commit; rollback + re-raise on error.
`_evaluate_opa_policy(policy_input)`: first tries `OpaProvider(settings.opa_provider_config()).evaluate(policy_path="rbac_grants.rego", query="data.rbac.grants.decision", input_data=...)` using `asyncio.run` (skipped when an event loop is already running); accepts only a dict containing "tier". On any failure uses an inline fallback (comment says it may drift from the rego):
```
ALL_PRIVILEGES -> hard_deny "ALL_PRIVILEGES is never allowed."
MANAGE on catalog -> hard_deny
principal_type == "user" -> hard_deny "Direct user grants not permitted. Use groups."
principal_name in ("users","admins") -> hard_deny (system group)
privilege == "USAGE" -> hard_deny "USAGE is deprecated. Use USE_CATALOG or USE_SCHEMA."
schema + SELECT/MODIFY + inherits_to_children -> extra_approval (widening)
catalog + SELECT/MODIFY/READ_VOLUME/WRITE_VOLUME -> extra_approval (major widening)
is_administrative and securable in (catalog, schema) -> extra_approval
no expiry and privilege in (SELECT, MODIFY, WRITE_VOLUME, WRITE_FILES) -> warn
else allow
```
Returns {"tier", "messages"}. The fallback ignores estate allowed_principal_exceptions (user grants always denied).
3.9 rbac_compensate_workbook (WRITE unless dry_run, data_grant)
Input: original_action (grant|revoke|modify), privilege, securable_type, securable_name, principal_name, principal_type="group", environment, original_request_id?, reason?, dry_run=True (DEFAULT TRUE).
Inverse map grant->revoke, revoke->grant, modify->modify (modify lacks original values, so executing it re-applies the same values). Plan includes `justification = "ROLLBACK: Compensating for <action> from request '<id|unknown>'. Reason: <reason>"` and a timestamp. dry_run returns the plan. Execute calls `rbac_mutate_workbook(..., request_id=f"compensate-{original_request_id or 'manual'}", auto_prerequisites=(compensating_action=="grant"), dry_run=False)` directly (relies on McpTool.__call__). Idempotency: the deterministic request_id means a repeat hits the audit layer and returns already_applied.
3.10 rbac_commit_workbook (WRITE, infra, async)
Input: request_id, change_summary, workbook_path?, target_branch (default "" -> RBAC_WORKBOOK_GIT_BRANCH, "main"), auto_merge=False, reviewers?.
Branch A (filesystem backend OR RBAC_WORKBOOK_REPO empty): verifies the file exists (else error "Was rbac_mutate_workbook called first?"); `add_fact(db, request_id, "workbook_committed", {backend:"filesystem", path, change_summary}, actor="system")` (app.state_machines.facts; non-fatal); flips every RbacMutationRecord for the request with status "applied" to "committed" and sets committed_at (non-fatal). Returns {success, backend:"filesystem", path, message}.
Branch B (git): needs token (GITHUB_TOKEN or get_git_token()), GITHUB_ORG, RBAC_WORKBOOK_REPO. Branch `f"{RBAC_BRANCH_PREFIX}{request_id.replace(' ','-').replace('/','-')[:60]}"`. With GitHubProvider(token, org): get_branch_ref(base) (error if missing) -> create_branch(from_sha) (failure tolerated: assumed to exist = retry-safe) -> create_or_update_file(path = RBAC_WORKBOOK_REPO_PATH or "workbooks/<name>", message "[RBAC] <summary>
Request: <id>
Timestamp: <UTC>") -> create_pull_request(title "[RBAC] <summary[:80]>", body with review checklist: diff correct / no unintended row deletions / tokens match provider version; head=branch, base=target) -> optional request_reviewers and enable_auto_merge (both non-fatal). Returns {branch, commit_sha, pr_number, pr_url, pr_title, request_id}. PR failure after commit returns an error that still includes branch and commit_sha.
Caveats: the Git path commits the unmodified xlsx at HEAD (mutate no longer writes it); in branch A the `db` variable may be unbound in the second try-block if the first failed.
3.11 rbac_validate_changes (read)
Input: request_id, principal_name, securable_name, privilege, environment?.
Layer 1 (Lakebase): RbacMutationRecord by request_id; if none, FALLBACK to (principal, securable, privilege[, environment]) created within the last 10 minutes, newest first (agent may pass the workflow `req-...` id instead of the `rbac-...` id). Layer 2: parse xlsx (resolve_path / acquire) and match principal with fnmatch (case-insens), securable (case-insens equality), privilege equality. Verdict: both found = confirmed; lakebase only = discrepancy (adds ephemeral-path warning when not persistent); workbook only = discrepancy; neither = not_applied; any error = partial_failure. Output includes lakebase_check, workbook_check, verdict, reasoning[].
3.12 rbac_capture_pre_state (read, async)
Input: request_id, securable_type, securable_name, principal_name, capture_planes=["uc_grant"]. Only uc_grant is implemented: `settings.get_workspace_client().grants.get_effective(securable_type=<SDK enum>, full_name=name)` filtered to principal == principal_name -> [{privilege, inherited_from, inherited_from_type}] plus total_assignments. SDK type map: metastore, catalog, schema, table, view->TABLE, volume, external_location, storage_credential, function, model->FUNCTION. Output {success True, pre_state{request_id, captured_at (UTC iso), securable_type, securable_name, principal_name, planes{uc_grant{captured, grants|error}}}}. Errors are recorded inside the plane. Does not persist; caller stores it as an immutable fact. Blocking SDK call inside async def.
3.13 rbac_compensate (plan only, async, external=True)
Input: request_id, pre_state, mutation_plan, apply_results[{resource, status success|failed|skipped, error}], strategy "rollback"|"retry". Classifies results into applied/failed/skipped/unknown. rollback -> per successful resource {action:"rollback", resource, description, pre_state_reference: pre_state.captured_at, requires_approval False}. retry -> per failed resource {action:"retry", retry_class, original_error, requires_fresh_plan: retry_class=="retryable_stale"}. `_classify_failure` keyword rules in order: transient (timeout, rate limit, 429, 503, network, connection), stale (conflict, changed, stale, outdated), non_retryable (denied, forbidden, 403, invalid, not found, 404), default "compensating". `needs_manual_remediation = unknown results exist or (retry and any non_retryable)`. Returns remediation_id `comp-{request_id}-{YYYY-MM-DD}`, summary counts, actions. Writes nothing and does not record a compensating_required fact. Rule from docs: never use Terraform state removal as a revoke.
3.14 rbac_verify_outcome (read, async, G-07)
Input: request_id, action 'grant'|'revoke', securable_type, securable_name, principal_name, privilege, access_planes=["uc_grant"], timeout_seconds=30. Only uc_grant implemented: `asyncio.wait_for(asyncio.to_thread(w.grants.get_effective, ...), timeout)`. has_direct = matching privilege with no inherited_from_name; has_inherited = with one.
```
grant : direct or inherited -> verified_granted ; else indeterminate (propagation delay)
revoke: neither -> verified_removed ; inherited only -> residual_access ; direct still present -> indeterminate
other action -> failure
PermissionError -> indeterminate ; asyncio.TimeoutError -> indeterminate ; other Exception -> failure
```
Overall = most conservative: priority {failure:0, indeterminate:1, residual_access:2, verified_removed:3, verified_granted:3}, lowest wins. `can_close_request = overall in (verified_granted, verified_removed)`. Unsupported securable type -> {success False, outcome "failure"}. Principal match is exact and case-sensitive. No plane results -> failure.
3.15 rbac_get_effective_access (read, async)
Input: securable_type, securable_name, principal_name?. Type map includes connection. `WorkspaceClient().grants.get_effective` in asyncio.to_thread; principal filter case-insens. Output {grants[{principal, privilege, inherited_from_type, inherited_from_name}], total, direct_grants, inherited_grants}; unsupported type returns supported_types.
3.16 rbac_detect_drift (read, async)
Input: environment?, workspace_id?, securable_type?, securable_name?, include_extra_grants=True, include_missing_grants=True. Estate gate; parse xlsx UC records; filters; `workbook_grants = {(principal.lower(), securable.lower(), PRIV)}`. Live state from Delta (`get_current_grants`, single bulk query scoped to workbook securables) + `check_discovery_freshness()` (stale -> error line). extra_grants = live - workbook ("ungoverned"); missing_grants = workbook - live ("unapplied"); each capped at 50. data_source "delta" or "delta_failed". Summary counts and has_drift. Ownership drift is advertised but not implemented.
3.17 rbac_detect_privilege_drift (read, async)
Input: catalog_filter?, include_deprecated_check=True. Loads registry; compares provider_version against `<registry>/../estate.yaml` estate.provider_version. Live tokens: if the discovery snapshot is fresh, distinct privilege from `get_current_grants(limit=0, securable_name="<catalog>.%")` (note: the Delta query uses equality on LOWER(securable_full_name), so the "%" never matches); else fallback SQL via `get_databricks_provider().execute_sql` against `system.information_schema.{table,schema,catalog}_privileges` with `_sanitize_sql_literal(catalog_filter)`. Output: new_tokens_in_workspace (live - registry - deprecated; drift), stale_tokens_in_registry (registry - live, minus admin-only allowlist CREATE_CATALOG, CREATE_CONNECTION, CREATE_EXTERNAL_LOCATION, CREATE_STORAGE_CREDENTIAL, CREATE_RECIPIENT, CREATE_SHARE, CREATE_PROVIDER, MANAGE, CREATE_MANAGED_STORAGE, SET_SHARE_PERMISSION; informational), deprecated_tokens_still_live (drift), provider_version_match, summary. Remediation hint: run `generate_privilege_registry.py --provider-version X`.
3.18 rbac_get_workspace_entitlement_mode (read, async)
Input: workspace_id. Order: estate `workspace_entitlement_modes[ws_id]` -> SDK `workspace_conf.get_status(keys="enableWorkspacePermissions")` (true = explicit, false = legacy) -> `default_entitlement_mode` (default "explicit"). EstateConfig has no such override fields, so they only work if present in the policy: section (merged into estate). `source` reports "workspace_api" even when the default was used.
3.19 send_notification (non-mutating, async)
Input: request_id, notification_type, message?, recipients?[], include_requester=True, include_approvers=True, channel?('email'|'slack'|'teams'), metadata?.
Recipients: explicit list, else from RequestModel.state_context (requested_by_email / requester_email / requested_by; approvers from manager_email, owner, approver_email), de-duplicated. Zero recipients -> {success True, warning "Notification logged but not delivered"} (never fails the workflow). Subjects (`[RBAC]` prefix): rbac_grant_complete, rbac_revoke_complete, rbac_membership_complete, rbac_bulk_grant_complete, rbac_assignment_complete, rbac_entitlement_complete, rbac_ownership_transfer_complete, rbac_role_assumption_complete, rbac_drift_report_ready, rbac_workflow_complete (generic fallback). Body: type sentence, Request ID, Summary, "Details:" bullets from metadata, footer saying the change was committed to a feature branch and the Terraform parser applies it after PR merge, signature "- EDH Service RBAC Automation".
Channels: explicit `channel` or auto: email always (`NotificationProvider.send_email(to, subject, body, metadata)` per recipient), Slack if NOTIFICATION_SLACK_WEBHOOK_URL (POST {text:"subject
body", unfurl_links:false}), Teams if NOTIFICATION_TEAMS_WEBHOOK_URL (legacy MessageCard, newlines -> `<br>`); 10 s timeout. Failures collected in channels_failed; `success = any channel succeeded`. An unknown channel string is attempted but never succeeds.
3.20 rbac_discovery_reader.py (library)
Reads Delta tables `` `<catalog>`.`<schema>`.rbac_discovered_grants `` and `rbac_discovery_snapshots`, populated by the `rbac_privilege_discovery` job (INFORMATION_SCHEMA scan, SCD-style MERGE). Config from estate.yaml `discovery:` (catalog and schema REQUIRED, else ValueError; max_staleness_minutes default 240). Executes via `WorkspaceClient().statement_execution.execute_statement(warehouse_id=...)` (module-level singleton). Warehouse: env RBAC_SQL_WAREHOUSE_ID -> settings.SQL_WAREHOUSE_ID -> first serverless -> first RUNNING -> first. Functions: `check_discovery_freshness()` (latest snapshot with status SUCCESS/PARTIAL_FAILURE; fresh if age <= threshold; timestamp treated as UTC), `get_current_grants(securable_type, securable_name, principal_name, privilege, environment (unused), limit=5000)` (`is_current = true`, case-insensitive equality filters, fetch limit+1 to detect truncation), `get_grants_for_securable`, `check_grant_exists`, `get_grant_history`. SQL safety: `_sanitize_sql_literal` strips control chars `[\x00-\x1f\x7f]` and doubles single quotes. Rows mapped using manifest column names. Defect: `get_grant_history` uses undefined `limit`.
---------------------------------------------------------------------
4. governance_search (tools/governance/governance_search.py)
`@tool(name="governance_search", args_schema=GovernanceSearchInput)`. Input: query (min 1), domains? (subset of assets|requests|approvals|tools), limit 1..50 default 10 per domain. Tokenizer: split on non-alphanumerics, lowercase, drop tokens < 2 chars and stopwords (data, table, tables, the, a, an, show, me, list, find, get, all, for, of, in, on, what, which, is, are, do, we, have, who, how, any, about). Each domain does ILIKE %token% across columns, OR across tokens; with no tokens it falls back to the raw query (approvals: no filter). Domains: assets -> DataAssetModel (id, table_name, description, owner, catalog, schema, domain, tags cast to String); requests -> RequestModel (id, title, status, requester_email; newest first); approvals -> ApprovalModel (id, approval_type, approver_email, status; newest first); tools -> ToolRegistryModel where enabled (tool_name, description) with `surfaces` from flags enabled_for_main_agent, enabled_for_workflow_agent, enabled_for_workflow_execution, exposed_via_mcp. Per-domain failures isolated ({count:0, items:[], error}); unknown domain -> error entry; DB session always closed. Output {query, domains_searched, total_hits, results{domain:{count, items}}, follow_up_suggestions} (suggests check_object_permissions, search_audit_logs, search_user_entitlements). Local DB only (cached data, not live UC).
---------------------------------------------------------------------
5. execute_workflow.py changes
Parameter alias normalization (before request creation): for each canonical key not already truthy, take the first truthy alias and pop it:
```python
_PARAM_ALIASES = {
"principal_name": ["principal","target_principal"],
"principal_type": ["grantee_type"],
"securable_name": ["securable","target","fqn"],
"securable_type": ["object_type","asset_type"],
"justification": ["reason","business_justification"],
}
```
Deliberately NOT aliased: `group_name` (target group in rbac_group_membership) and `grantee_name` (rbac_role_assumption), because popping them breaks `$var` resolution.
Duplicate guard for workflow_type in {rbac_uc_grant, rbac_uc_revoke}: `_check_rbac_duplicate_for_agent(parameters, db)`; if it returns a dict the tool returns it ({success False, duplicate True}) and creates no request. Needs principal_name, securable_name, privilege, environment (non-empty) else None (no check). Normalization: privilege upper, environment lower, securable_type lower, principal_type default "group", action default "grant". Three independent layers always run; errors are captured in `layer_errors` and never abort:
Audit: latest RbacMutationRecord with action in {grant, grid_add, grid_edit}, same principal/securable/privilege/environment, status in {applied, committed, exported}.
In-flight: RequestModel of the two RBAC types with status NOT in {completed, rejected, failed, denied, export_failed, exported} whose state_context principal_name/securable_name/privilege(upper)/environment(lower) match; `db.refresh(req)` re-reads and skips it if it just reached completed/rejected/failed (avoids false "in progress" for fast auto-approved grants). No action filter (a revoke request is compared with grants too).
Replica: RbacReplicaRow for `determine_target_sheet` (via a `_FakeRecord` carrying environment + securable_type), principal, privilege, environment; UC types match catalog/schema_name/object_name by supplied segments; workspace-type securables (workspace, workspace entitlement, folder, cluster, sql warehouse) match `object_name == last segment`.
Verdicts: none -> proceed. audit+replica -> confirmed_duplicate (block). audit only: in-flight -> in_flight_only (block), else log discrepancy and ALLOW resubmission (replica is the system of record and says no grant). replica only -> replica_only (block). in-flight only -> in_flight_only (block). Otherwise duplicate_detected. Result: {success False, duplicate True, verdict, action, principal_name, principal_type, securable_type, securable_name, privilege, environment, message, evidence[audit_hit, replica_hit, in_flight_hit], checks_performed{lakebase_audit|in_flight|replica: hit|miss|error}, layer_errors?}. Because it checks grant presence regardless of action, a revoke of an existing grant is blocked as a duplicate.
RBAC requests park at approval: for the two types the RequestModel is created with status="pending_approval" and current_state="pending_approval" and NO `request_submitted` fact is added (so the state machine does not run); other workflows keep status/current_state "pending" and get the `request_submitted` fact (actor "agent"). `environment=parameters.get("environment")` is persisted to the model column (used for bundle drill-down, export partitioning, sheet routing). The response adds `bundle_id` (request.bundle_id, generated elsewhere) and status "pending_approval" (RBAC) or "initiated"; message says it awaits Platform Admin approval of the bundle. Admin then calls `POST /rbac-grid/bundle/{id}/approve` and `/export`.
Adds logging of workflow_type and params (minus conversation_history).
---------------------------------------------------------------------
6. mcp.py change
`McpTool.__call__(args, kwargs)` returns `self._func(args, kwargs)`, so decorated tools can be called like plain functions (mutate -> validate_privilege / resolve_prerequisites; compensate_workbook -> mutate). It bypasses the async `execute()` path, Pydantic arg validation, ToolExecutor chokepoint, policy and guardrails (trusted internal callers only). Calling an async tool returns a coroutine.
7. workflows/tool_registry.py change
Adds `_selfservice_registry()` (module-level cache `_selfservice_cache`): `pkgutil.walk_packages` over `app/tools/` (prefix `app.tools.`), skipping modules containing a segment in {"execute_workflow", "catalog", "mcp", "__pycache__"} (circular imports), importing each (exceptions swallowed), registering every module attribute with a truthy `name` or `tool_name` and a callable `invoke`/`__call__`. Lookup order for get_tool / has_tool: (1) code registry, (2) the new self-service scan, (3) DB-registered MCP tools; `all_tool_objects` merges code, self-service, DB (later wins). Caveats: process-lifetime cache (new tools need restart); can pick up non-tool objects with a `.name`; import errors silently dropped.
---------------------------------------------------------------------
8. Known defects / gotchas
`_sync_mutation_to_replica` uses `request_id` in its update and insert branches but it is not a parameter or global -> NameError on every grant/modify (revoke unaffected). The audit row is already committed as "applied", then the tool returns "Replica mutation failed"; a retry hits Layer-1 idempotency and reports already_applied without writing the replica. Pass request_id explicitly.
RbacCanonicalRecord lacks catalog/schema_name/object_name/requested_by_email, so audit rows have null catalog/schema/object and requester_email, and replica updated_by is always "agent".
`get_grant_history` NameError (`limit`).
Mixed source of truth: validate_changes, check_existing_grants, read_workbook, detect_drift, resolve_prerequisites still read the xlsx, which no longer receives mutations; validate_changes will report discrepancy for new grants and prerequisites satisfied only via replica rows are not recognized.
`expires_at` is accepted but never persisted. `intent_hash`, `risk_tier`, SoD fields exist in models but no tool here populates them.
rbac_resolve_principal schema has `environment`/`exception_context` that its function signature does not accept.
Unknown or "all" environment maps to the Dev sheets in determine_target_sheet.
Replica prerequisite exists-check filters on catalog only.
OPA call is skipped inside a running event loop, so the inline fallback ruleset is what usually runs; it hard-denies all `user` principals regardless of estate exceptions.
rbac_commit_workbook Git branch ships the unmodified workbook in the replica-first design; filesystem branch only records the fact and flips audit statuses to committed (later `exported` status is set by bundle export code elsewhere).
Parser header matching is substring-both-ways; blank header cells can match.
Serializer `_get_col_map` treats only `WS_RBAC` as workspace sheets, not `WS_Securables_RBAC_`.
rbac_capture_pre_state does not persist; rbac_compensate only plans.
Some async tools call blocking SDK code directly (capture_pre_state, discovery_reader); others correctly use asyncio.to_thread.
Discovery SQL is string-interpolated (sanitized by quote doubling + control-char stripping); SCIM filters escape double quotes only.
03 - Workflows, Instructions, OPA Policies, Workflow Engine Changes
Scope: diff `3c832e69..HEAD` (branch feature/EDTFNDTN-4251). Paths relative to `backend/` unless noted.
Workflow specs: `app/workflows/graphs/catalog/rbac_.json`. Playbooks: `app/agents/instructions/rbac_.md`. Policies: `policies/.rego` + `policies/manifest.yaml`.
0. Cross-cutting facts (read first)
All 9 workflows are "Phase 1": they end at `rbac_commit_workbook` (`complete_fact = "workbook_committed"`). Post-commit stages (PR merge gate, capture pre-state, live verification, notification) are stored under a non-executed top-level key `_phase2_stages` (plus `_phase2_original_complete_fact`, `_phase2_comment`: "Phase 2 stages (post-commit: live verification, notification) are preserved here but not executed. Move back to 'stages' when Phase 2 is enabled."). A reimplementation must keep these inert but preserved.
Mutation is a workbook (Excel) mutation + git commit, not a direct Databricks API call. Apply to Databricks happens externally (Terraform on merge) - outside these graphs.
Standard step pattern: `dry_run_preview` (rbac_mutate_workbook, dry_run=true, `writes_context: ["mutation_plan"]`) -> `confirm_mutation_plan` (gate type `requester_confirm`, `auto_approve: true`; comment "Phase 1: auto-approve - requester confirmed via agent chat") -> `apply_` (rbac_mutate_workbook, dry_run=false, `approvals: [...]` listing gate types that must have approved) -> `validate_changes` -> `commit_workbook`.
Every workflow's `allowed_tools` is the same 15-tool list: `get_workflow_instructions, rbac_check_existing_grants, rbac_commit_workbook, rbac_compensate_workbook, rbac_detect_drift, rbac_get_effective_access, rbac_get_workspace_entitlement_mode, rbac_list_securables, rbac_mutate_workbook, rbac_read_estate_config, rbac_read_workbook, rbac_resolve_prerequisites, rbac_resolve_principal, rbac_validate_privilege, send_notification`. `rbac_uc_grant` additionally allows `rbac_capture_pre_state, rbac_compensate, rbac_validate_changes, rbac_verify_outcome`.
`rbac_compensate_workbook` / `rbac_compensate` are in allowed_tools but NO active stage in any graph invokes compensation. Rollback/compensation is not wired into graph edges. Supporting machinery that exists: fact types `pre_state_captured, partial_applied, compensating_required, compensating_executed, stale_plan_rejected` (facts.py docstring), `classify_retry()` in executor.py, and the inactive `capture_pre_state` Phase-2 step. Golden scenario `git_commit_failure` states `mutation_applied: true, retryable: true` (workbook mutated but commit failed -> retry commit).
Args use the spec DSL: `{"$var": "name"}` (context lookup; dotted like `expanded_securables.names`), `{"$coalesce": [..]}`. `request_id` is injected into context by executor (section 4.5).
Graphs are linear: stages execute in listed order (edges implicit). Gates pause the durable run until an `approval_received` fact with matching `approval_type` exists.
Agent-level rule (prompts.py): for `rbac_uc_grant` / `rbac_uc_revoke` the agent does not ask for data owner / manager / justification and skips the confirmation table (section 3.3).
1. Summary table: workflows
Key
Purpose
Active stages (in order)
Human approval gates
Policy hooks
Mutation
rbac_uc_grant
Grant UC privilege to a principal
validate_request, resolve_prerequisites, dry_run_preview, confirm_mutation_plan, apply_mutation, validate_changes, commit_workbook
NONE in graph (instructions: auto-bundled hourly; Platform Admin reviews bundle in Pending Approvals)
rbac.grants, rbac.blast_radius at ToolExecutor; risk/sod/expiry pre-approval
grant
rbac_uc_revoke
Revoke UC privilege
check_existing_grant, manager_approval, data_owner_approval, dry_run_preview, confirm_mutation_plan, apply_revoke, validate_changes, commit_workbook
manager, data_owner
same
revoke
rbac_bulk_access
Same privilege on many securables
expand_package, validate_all_privileges, check_existing_bulk, manager_approval, data_owner_approval, dry_run_preview, confirm_mutation_plan, apply_bulk_grants, validate_changes, commit_workbook
manager, data_owner
rbac.blast_radius bulk (>10)
grant (securable_name = expanded list)
rbac_group_membership
Add/remove member of account group
resolve_principal, validate_group, manager_approval, group_owner_approval, dry_run_preview, confirm_mutation_plan, apply_membership, validate_changes, commit_workbook
manager, group_owner
rbac.entitlements (system-group immutability)
privilege "MEMBER", securable_type "group"
rbac_ownership_transfer
Transfer UC object ownership
validate_securable, resolve_new_owner, check_pipeline_restriction, current_owner_approval, platform_admin_approval, dry_run_preview, confirm_mutation_plan, apply_transfer, validate_changes, commit_workbook
data_owner (current owner), platform_admin
rbac.risk (+20 ownership_transfer)
action "modify", privilege "OWN"
rbac_role_assumption
Grant ASSUME on role-group (feature-gated)
check_feature_gate, validate_role_group, resolve_grantee, manager_approval, role_owner_approval, dry_run_preview, confirm_mutation_plan, apply_assumption, validate_changes, commit_workbook
manager, role_owner
feature gate via post_check
privilege "ASSUME", securable_type "role_group"
rbac_ws_assignment
Assign group to workspace
check_entitlement_mode, manager_approval, dry_run_preview, confirm_mutation_plan, apply_assignment, validate_changes, commit_workbook
manager
rbac.entitlements
grant on securable_type "workspace"
rbac_ws_entitlement
Grant workspace entitlement to group
check_entitlement_mode, validate_entitlement, manager_approval, workspace_admin_approval, dry_run_preview, confirm_mutation_plan, apply_entitlement, validate_changes, commit_workbook
manager, workspace_admin
rbac.entitlements (admin entitlement rules)
grant on workspace
rbac_drift_detection
Read-only drift report: workbook vs Delta discovery snapshot
read_workbook_state, detect_drift, notify_completion
none
rbac.drift (drift_worker)
none
2. Per-workflow detail
Every step has a `running_status` string and a `success_fact`.
2.1 rbac_uc_grant (complete_fact `workbook_committed`)
Top-level `_comment`: "Stage order per G-02: validate -> prerequisites -> plan (dry_run) -> confirm -> approval gates -> mutate workbook -> commit/PR -> merge gate -> capture pre-state -> verify live -> notify. Workbook mutation precedes commit (you must modify before committing). Terraform apply is external, triggered by merge."
`validate_request` - `rbac_validate_privilege(privilege, securable_type, include_prerequisites=true)`; ctx `privilege_validation`; fact `privilege_validated`.
`resolve_prerequisites` - `rbac_resolve_prerequisites(privilege, securable_type, securable_name, principal_name, check_existing=true)`; ctx `prerequisites`; fact `prerequisites_resolved`.
`dry_run_preview` - `rbac_mutate_workbook` action=grant, `owner = coalesce(requested_by_email, "self-service")`, `justification` literal `"Requested via agent"`, `auto_prerequisites=false`, `dry_run=true`; ctx `mutation_plan`; fact `plan_computed`.
`confirm_mutation_plan` - gate `requester_confirm`, auto_approve. description: "Requester reviews computed plan INCLUDING prerequisites (G-06). Prerequisites are NEVER silently auto-added." (hence auto_prerequisites=false).
`apply_mutation` - same args, dry_run=false; running_status `applying_grant`; fact `grant_applied`.
`validate_changes` - `rbac_validate_changes(request_id, principal_name, securable_name, privilege, environment)`; comment "Cross-references Lakebase audit records AND workbook read-back to confirm mutation applied correctly." Fact `changes_validated`. Only workflow using rbac_validate_changes; the others use rbac_read_workbook read-back.
`commit_workbook` - `rbac_commit_workbook(request_id, change_summary="Requested via agent")`; fact `workbook_committed`.
Phase 2 (inactive): `await_pr_merge` = `{"kind":"merge_gate","waiting_status":"merge_pending","target_branch":"main","reject_on_rebase":true,"validate_intent_hash":true}` ("G-02: Protected merge gate - apply happens AFTER merge, not before"); `capture_pre_state` (rbac_capture_pre_state; fact `pre_state_captured`; ctx `pre_state`; "G-08: Capture live state before Terraform apply for compensation"); `verify_effective_access` (rbac_verify_outcome action=grant; fact `grant_verified`; "G-07: Live Databricks verification with explicit outcome enum"); `notify_completion` (send_notification `rbac_grant_complete`). Original complete_fact `grant_verified`.
Approval: no human gates in graph. Instruction says approval is a Platform Admin "bundle review" (hourly auto-bundled; status `pending_approval`) - handled by the bundle subsystem documented elsewhere; poller excludes statuses pending_approval/approved/denied/exported (section 5).
Compensation: none active.
2.2 rbac_uc_revoke
`check_existing_grant` - rbac_check_existing_grants(principal_name, securable_name, securable_type, privilege); ctx `existing_grants`; fact `grant_found`.
gate `manager_approval` (type manager, waiting `awaiting_manager_approval`, approvers_from `manager_email`).
gate `data_owner_approval` (data_owner, approvers_from `owner`).
`dry_run_preview` (action=revoke, dry_run=true). 5. `confirm_mutation_plan` (auto). 6. `apply_revoke` (approvals [manager, data_owner]; fact `revoke_applied`). 7. `validate_changes` (rbac_read_workbook filter principal+securable+privilege, limit 10; "confirms the grant row was removed/expired"). 8. `commit_workbook` (change_summary = justification).
Phase 2 (inactive): verify_changes, notify_completion. Original complete_fact `revoke_applied`.
2.3 rbac_bulk_access
`expand_package` - rbac_list_securables(package_name, securable_type, catalog_filter=catalog_name, schema_filter=schema_name); ctx `expanded_securables`; fact `package_expanded`.
`validate_all_privileges` - rbac_validate_privilege(include_prerequisites=true); ctx `privilege_validations`; fact `all_privileges_validated`.
`check_existing_bulk` - rbac_check_existing_grants(principal_name, securable_names=`expanded_securables.names`, privilege); ctx `existing_grant_check`; fact `existing_checked`.
manager_approval; 5. data_owner_approval (approvers_from `owner`).
`dry_run_preview` (securable_name = `expanded_securables.names`, principal_type default group, `auto_prerequisites=true`, dry_run=true). Unlike the other workflows bulk uses auto_prerequisites=true.
confirm_mutation_plan (auto). 8. `apply_bulk_grants` (approvals [manager, data_owner]; fact `bulk_grants_applied`). 9. `validate_changes` (rbac_read_workbook filter principal+privilege, limit 50). 10. commit_workbook.
Golden error scenarios: partial_policy_block (blocked `CREATE_TABLE on finance_prod.sandbox`, allowed `SELECT on finance_prod.gold.revenue_summary`), package_not_found ("Package not found"), bulk_threshold_exceeded (>10 securables -> extra gate platform_admin, `rbac_blast_radius.rego:extra_approval`, message "Bulk grant affects").
2.4 rbac_group_membership
`resolve_principal` - rbac_resolve_principal(principal_name=member_name, principal_type=coalesce(member_type,"user")); ctx `principal_info`; fact `principal_resolved`.
`validate_group` - rbac_resolve_principal(principal_name=group_name, principal_type="group", `reject_system_groups=true`); ctx `group_info`; fact `group_validated`.
manager_approval; 4. `group_owner_approval` (type group_owner, approvers_from `group_owner_email`).
dry_run_preview: action={$var action} (add/remove), privilege "MEMBER", securable_type "group", securable_name=group_name, principal_name=member_name, environment "all", auto_prerequisites=false.
confirm; 7. `apply_membership` (approvals [manager, group_owner]; fact `membership_applied`); 8. validate_changes (read_workbook filter_principal=member_name, limit 10); 9. commit.
Gotcha: instruction example passes `principal_name`/`principal_type` but the graph reads `member_name`/`member_type` (golden required_vars say principal_name, principal_type, group_name, action). Align names when reimplementing.
2.5 rbac_ownership_transfer
`validate_securable` - rbac_list_securables(securable_type, securable_name, check_ownership_constraints=true); fact `securable_validated`.
`resolve_new_owner` - rbac_resolve_principal(new_owner, new_owner_type default group); fact `new_owner_resolved`.
`check_pipeline_restriction` - rbac_check_existing_grants(principal_name=current_owner, securable_type, securable_name, check_type="ownership_transfer_safety"); fact `pipeline_check_passed` (MV/ST pipeline guard).
gate `current_owner_approval` (data_owner, approvers_from `current_owner`); 5. gate `platform_admin_approval` (platform_admin, approvers_from `platform_admin_email`).
dry_run_preview (action "modify", privilege "OWN", principal_name=new_owner, owner=new_owner); 7. confirm; 8. `apply_transfer` (approvals [data_owner, platform_admin]; fact `ownership_transferred`); 9. validate_changes (read_workbook filter_securable); 10. commit.
Golden: mv_st_pipeline_restriction ("change the pipeline run-as identity"), current_owner_mismatch ("Current owner does not match").
Instruction quote: "MV/ST Pipeline Guard. Materialized Views and Streaming Tables MUST be owned by the pipeline service principal that refreshes them. Transferring ownership of these objects breaks the refresh pipeline. This workflow will BLOCK transfers on MV/ST securables unless explicitly overridden with acknowledgment." Also "Platform admin approval always required"; "Current owner must consent"; "New owner must have at least USE_CATALOG + USE_SCHEMA on the parent path."
2.6 rbac_role_assumption
`check_feature_gate` - rbac_read_estate_config(); ctx `estate_config`; fact `feature_gate_checked`; `post_check`: `{"field":"estate.role_assumption_enabled","expect":true,"fail_message":"Role assumption is not enabled in this estate. Contact your platform admin to enable it."}`. NOTE: `post_check` is parsed and stored on Step by spec_loader, but no code outside spec_loader.py/spec.py references it, so enforcement is not implemented in this diff - verify before relying on it.
`validate_role_group` - rbac_resolve_principal(role_group_name, principal_type group, validate_role_pattern=true); fact `role_group_validated`. Naming convention `role-<purpose>-<scope>`.
`resolve_grantee` - rbac_resolve_principal(grantee_name, grantee_type default group); fact `grantee_resolved`.
manager_approval; 5. `role_owner_approval` (role_owner, approvers_from `role_owner_email`).
dry_run_preview (grant, privilege "ASSUME", securable_type "role_group", owner=role_owner_email); 7. confirm; 8. `apply_assumption` (approvals [manager, role_owner]; fact `role_assumption_granted`); 9. validate_changes (read_workbook filter_principal=grantee_name); 10. commit.
Golden: invalid_role_pattern ("role-group naming convention"), feature_not_enabled ("Role Assumption is not enabled"). Instruction: "Time-limited: consider recommending an expiry date on the assumption grant."
2.7 rbac_ws_assignment
`check_entitlement_mode` - rbac_get_workspace_entitlement_mode(workspace_id); fact `mode_checked`.
manager_approval. 3. dry_run_preview (grant, privilege={entitlement}, securable_type "workspace", securable_name=workspace_id, principal_type fixed "group"). 4. confirm. 5. `apply_assignment` (approvals [manager]; fact `assignment_applied`). 6. validate_changes (read_workbook filter_principal, sheet_type "workspace", limit 10). 7. commit.
Instruction: groups only (account-level); legacy mode = all account users see workspace; explicit mode = only assigned.
2.8 rbac_ws_entitlement
check_entitlement_mode; 2. `validate_entitlement` (rbac_validate_privilege securable_type "workspace", include_prerequisites=false; fact `entitlement_validated`); 3. manager_approval; 4. `workspace_admin_approval` (workspace_admin, approvers_from `workspace_admin_email`); 5. dry_run_preview; 6. confirm; 7. `apply_entitlement` (approvals [manager, workspace_admin]; fact `entitlement_applied`); 8. validate_changes (sheet_type workspace); 9. commit.
Instruction: "Always check the mode first."; "system groups are protected: Cannot modify entitlements on `users`, `admins`."; "CAN_MANAGE requires extra approval: Platform admin must approve."; group must already be assigned to the workspace.
Golden: workspace_admin_to_non_admin (rbac_grants.rego hard_deny "only be granted to designated admin groups"), invalid_entitlement ("not a recognized entitlement").
2.9 rbac_drift_detection (read-only)
`read_workbook_state` - rbac_read_workbook(filter_environment, filter_securable_type, include_all_sheets=true); ctx `workbook_state`; fact `workbook_read`.
`detect_drift` - rbac_detect_drift(environment, workspace_id, securable_type, securable_name, include_extra_grants default true, include_missing_grants default true); ctx `drift_report`; fact `drift_report_generated` (= complete_fact).
`notify_completion` - send_notification type `rbac_drift_report_ready`, message coalesce(justification, reason, "Workflow completed successfully").
No gates. Instruction: live state comes from the `rbac_discovered_grants` Delta table (populated by periodic `rbac_privilege_discovery` job, typically every 1-4h), "NOT from direct SDK API calls. This removes the need for account-admin permissions at runtime." Response has a `freshness` block (`is_fresh`, `age_minutes`) - warn user if stale; `data_source: "delta" | "delta_failed"` (errors explain). Drift types: extra grants (shadow), missing grants, ownership drift. After: "Present the drift summary: N extra, M missing, K mismatched... Stop. Do NOT suggest remediation workflows unless the user asks." (Leftover orphan line "7. Audit trail: ... offer to run `get_grant_history`" follows the Stop rule - contradictory.)
Golden: estate_config_invalid ("Estate configuration is invalid"), workspace_unreachable (status partial, "Could not connect to workspace").
3. Agent instruction content and prompts.py diff
3.1 Structure of rbac_.md
Sections: Goal; "Use What You Already Know (do this FIRST)"; Information to Gather; Policy Awareness; Validation Before Execution (checklist); Execution (`execute_workflow` JSON with `workflow_type` + `parameters`); After Execution. Every After Execution ends: "State: changes validated and committed. Stop. Do NOT describe next steps (approvals, emails, Terraform, timelines)."
3.2 Critical verbatim rules
uc_grant: "> Policy - Groups only by default. Direct user grants are blocked by EDH policy. If the requester names an individual user, guide them to either use an existing group or request a new purpose-named group. Only proceed with `principal_type: group`." and "> Policy - No catalog-level MANAGE. MANAGE on a catalog is hard-denied. If requested, explain the blast radius and suggest schema-level MANAGE or specific table privileges instead."
uc_grant "Act Fast, Don't Interrogate": "Do NOT ask for data owner, manager email, or justification. Those fields are optional and auto-filled: owner defaults to the requesting user's email; justification defaults to "Requested via agent"; manager_email: not used - approval is via Platform Admin bundle review." Required: Privilege, Securable name (fully qualified e.g. `enterprise_dev.gold_sales.orders`), Principal (group name), Environment (dev, tst, stg, prd). securable_type inferred from dot-segments: 1 -> catalog, 2 -> schema, 3 -> table (default). "If the user provides all four in a single message, proceed directly to execution. Do NOT ask confirmation questions - just execute." Multiple grants: call `execute_workflow` once per grant; "They will be auto-bundled by the hour into a single bundle for approval." After: state status `pending_approval` and bundle ID; "a Platform Admin will review the bundle in Pending Approvals."; "Stop. Do NOT describe further steps."
uc_revoke: verify via `rbac_check_existing_grants` (checks BOTH workbook and Delta discovery) and interpret `drift_hint`: `consistent` (standard revoke), `in_workbook_only` (still revoke from workbook "to prevent future apply"), `in_live_only` (ungoverned shadow grant; "revoking from workbook won't remove it. Suggest a manual SQL REVOKE or an enforcement sentinel scan"), `not_found` (ask if they meant a different securable). Safety: "Never revoke without confirmation. Always show what will be removed before proceeding."; "Cascade warning: If USE_CATALOG or USE_SCHEMA is revoked, all downstream grants become ineffective."; "Last-accessor check: If this is the only principal with access to the securable, warn about potential lock-out." Justification >= 10 chars. (Conflicts with prompts.py RBAC shortcut which says skip confirmation.)
bulk_access: skip `consistent`; `in_workbook_only` -> "pending Terraform. Don't duplicate it."; `in_live_only` -> "Include it in the workbook mutation to bring it under governance."; only `not_found` get new grants. "If granting to > 10 securables, extra approval may be required." "Prerequisites auto-computed: USE_CATALOG/USE_SCHEMA added automatically." "Delta freshness verified - if stale, warn user about possible false negatives." Report "N grants added, M skipped, K prerequisites added."
group_membership: system groups (`account users`, `admins`, anything matching `system_group_pattern`) are hard-denied; nested groups trigger extra_approval (inheritance widening); default action `add` unless user explicitly says remove/revoke/delete.
role_assumption: "Public Preview. Role assumption is gated behind `role_assumption_enabled` in the estate config."
3.3 prompts.py diff (app/agents/prompts.py): persona shift from "concierge" to terse executor
Communication style replaced (was warm/proactive, "Aim to end most turns with one genuinely useful offer") with: "Professional, direct, and task-focused. Execute what the user asked and report the result." / "Do NOT suggest next steps, offer alternatives, or volunteer extras unless the user explicitly asks. No "Want me to also...?", no "You might also consider...", no unsolicited offers." / "Concise. Skip filler and padding." / "When you need information from the user, ask for exactly what you need - nothing more."
Section 2 Tone: "Professional and direct. Answer the question that was asked. Do not anticipate follow-up questions or offer unsolicited suggestions. Be concise - no filler, no padding."
"### Concierge Mindset (anticipate & go beyond)" replaced by "### Execution Mindset (direct & focused)": "Execute the requested action, report the result, and stop."; "No unsolicited offers. Do NOT close with "Want me to also...?", "Shall I...?", "You might also consider...", "Would you like me to...?", or any variant. If the user wants more, they will ask."
Provisioning bullet: "Use your tools (e.g. `list_workspaces`, `get_catalog_list`) to verify before creating. If something similar exists, state the fact. Do not suggest alternatives unless asked." FinOps: "Report findings factually." (removed proactive idle-resource suggestion). Discovery fallback: "State what you found factually. Do not offer unsolicited follow-ups."
Workflow Phase A new bullet: "RBAC Shortcut: For `rbac_uc_grant` and `rbac_uc_revoke` workflows, do NOT ask for data owner, manager email, or justification - those are auto-filled. Just gather privilege, securable, principal, environment and execute immediately. Skip the confirmation table for RBAC grants - execute directly."
Phase B confirmation: bullet list replaced by mandatory Field | Value markdown table (rows: Privilege `MODIFY`, Securable Type, Securable Name, Principal `(group)`, Environment, Data Owner "(auto-filled for RBAC)", Justification "(auto-filled for RBAC)"), ending "Shall I proceed?"; "Do NOT use bulleted lists for the confirmation summary. Always use the Field | Value table."
Phase D: short factual message only: "Request submitted." + bullets Request `req-12345`, Type, Resource, Privilege -> principal, then "Track progress in My Requests." Forbidden additions: "Your manager will receive an approval request", "The data owner will be notified", "You'll receive an email when processed", "Once approved, the grant will be applied via...", "Want me to also check..." Removed training/reusable-asset follow-up heuristics.
4. Workflow engine / spec / render / registry changes
4.1 spec.py
`Step.post_check: Optional[Dict[str, Any]]` ("tool + args to run after this step succeeds, used for validation/read-back assertions") - stored only.
New dataclasses: `MergeGate(name, waiting_status="merge_pending", target_branch="main", reject_on_rebase=True, validate_intent_hash=True)` (G-02 protected merge; validates PR from approved plan matching intent_hash, correct target branch, no scope change) and `ConditionalGate(name, inner_gate: Gate, when_expr: str, waiting_status="conditional_approval")` with `evaluate_condition(context)` using whitelisted vars `risk_tier` (default "standard"), `environment`, `principal_type` (default "group"); on error it returns True ("Fail closed: activate gate if expression fails"). `Stage = Union[Gate, Step, SubWorkflow, MergeGate, ConditionalGate]`.
`_safe_eval_expr(expr, variables)` / `_walk(node, variables)`: AST evaluator (no eval). Allowed: Constant; Name (must be in variables else `ValueError("Disallowed variable: '...'")`); List/Tuple; Compare with `==, !=, in, not in` (chained); BoolOp and/or; UnaryOp not. Anything else raises ValueError ("Only literals, names, comparisons (==, !=, in, not in), and boolean logic (and, or, not) are allowed.").
```python
def _safe_eval_expr(expr, variables):
tree = ast.parse(expr, mode="eval")
return _walk(tree.body, variables)
```
4.2 spec_loader.py
`GATE_TYPES` now: manager, platform_admin, data_owner, training, pr_merge, terramate, terramate_status, manual_task, children, + new `requester_confirm, security, group_owner, role_owner, workspace_admin`.
`_GATE_KEYS` adds `description`; `_STEP_KEYS` adds `post_check`; `_CONDITIONAL_GATE_KEYS = {"kind","name","$when","inner_gate"}`.
`_reject_unknown_keys` ignores keys beginning with `_` (permits `_comment`, `_phase2_`).
Stage kind validation accepts `gate, step, subworkflow, conditional_gate`. `merge_gate` is NOT an accepted kind: it lives only in inactive `_phase2_stages`; moving it into `stages` would fail validation.
`_validate_conditional_gate`: `$when` must be non-empty string; `inner_gate` must be an object with `type` in GATE_TYPES.
`spec_from_dict` compiles conditional_gate into a regular `Gate` with `auto_approve = lambda ctx: not eval(compiled($when), {"__builtins__": {}}, ctx)`; returns True (skip the gate) on any exception. Gotcha: this path uses raw `eval` with empty builtins, not the safe AST walker, and fails open (skips) whereas `ConditionalGate.evaluate_condition` fails closed.
`_step_from_dict` copies `post_check`.
None of the 9 catalog workflows currently use conditional_gate.
4.3 graphs/specs.py `_load_catalog`
Invalid catalog files no longer raise: `logger.warning("Skipping catalog file %s ('%s'): %s", fname, key, e); continue`. A SpecError silently drops that workflow instead of crashing startup.
4.4 tool_registry.py
Adds a middle lookup tier `_selfservice_registry()` (cached in `_selfservice_cache`): `pkgutil.walk_packages` over `app/tools/`, skipping modules whose path contains segment `execute_workflow, catalog, mcp, __pycache__` (avoids circular import); registers any module attribute with `name` or `tool_name` that is callable (`invoke` or `__call__`); import errors swallowed (`except Exception: continue`). Lookup order in `get_tool`/`has_tool`/`all_tool_objects`: code registry -> self-service scan -> DB MCP tools. This is how `rbac_` tools in `app/tools/self_service/` resolve.
4.5 executor.py
`_initial_state`: `ctx = dict(request.state_context or {}); ctx.setdefault("request_id", request.id)` so `{"$var":"request_id"}` works in step args.
New helper functions (G-05/G-08), NOT wired into graphs in this diff:
`validate_approval_eligibility(request_context, approver, estate_config)` -> `{"eligible", "reason"}`: denies if `approval_routing.deny_self_approval` (default True) and approver == requester ("Self-approval denied: '{approver}' is the requester."); denies if approver == principal_name ("Conflict of interest: approver '{approver}' is the target principal.").
`validate_intent_hash(request_context, stored_hash)`: sha256 of `json.dumps([principal_name, environment, securable_name, privilege, securable_type, str(expiry), incident_ref], sort_keys=True)`; mismatch -> `{"valid": False, "current_hash", "stored_hash", "reason": "Intent changed since approval. Approvals invalidated."}`.
`classify_retry(error)` by lowercase substring: `timeout|rate limit|429|503|network` -> `retryable_transient`; `conflict|stale|changed since` -> `retryable_stale`; `denied|forbidden|403|invalid|not found` -> `non_retryable`; else `compensating`.
4.6 render.py (UI state)
`_gate_satisfied`: `security` added to human gate types matched via `approval_received.approval_type` (explicit list: manager, platform_admin, data_owner, security, manual_task).
`render_state`: `gate_types` includes `conditional_gate` (via `inner_gate.type`); builds per-stage fact lists (step facts matched by `success_fact`; `approval_received` matched to stage by gate type; `request_rejected` attached to current stage) -> `StateInfo.facts` entries `{type, timestamp (iso + "Z"), data: scalar-only values as str}`; `StateInfo.resultSummary = node_results[node]` only when stage completed.
`live_graph.satisfied()`: `conditional_gate` -> satisfied (skipped) if `$when` evaluates false via `_safe_eval_expr(when, ctx)` or errors, else checks inner gate type; `merge_gate` -> `_gate_satisfied("pr_merge", ...)`. Response additionally returns `node_details` and `node_results`.
New helpers: `_resolve_var` ($var -> ctx value or `<key>` placeholder; $coalesce first non-empty), `_resolve_node_details` (resolved args + one-line description e.g. `"GRANT SELECT on x for y · env=dev · (dry run)"` / `(live)`; gates `"{type} gate"` or `"{type} gate - when {expr}"`), `_extract_node_results` (success_fact -> stage; prefers events carrying a `results` array over bare tool events), `_summarize_step_result` with special cases keyed on stage NAME: `validate_request`, `resolve_prerequisites`, `dry_run_preview` (reads `mutation_plan.target_record`), `apply_mutation`, `validate_changes` (verdict from rbac_validate_changes: `lakebase_check`/`workbook_check` records capped at 5, plus `reasoning`; fallback summary for old rbac_read_workbook output), `commit_workbook`; generic fallback truncates message to 120 chars. Other stage names (apply_revoke, etc.) get the generic summary.
5. Poller, state machines, workers
`workers/poller.py`: (a) open-request query now also excludes statuses `pending_approval, approved, denied, exported` (bundle-managed requests not advanced by state-machine poller); (b) `_v2_resume_value` human-gate resume set extended to `manager, platform_admin, data_owner, manual_task, requester_confirm, security, group_owner, role_owner, workspace_admin` (resume when a matching `approval_received` fact exists); (c) `_HUMAN_GATE_TYPES` adds `security` (deliberately NOT in the evaluator's APPROVAL_GATE_TYPES per existing comment).
`state_machines/facts.py`: docstring only, listing fact types: privilege_validated, prerequisites_resolved, plan_computed, plan_confirmed, approval_received, approval_invalidated, grant_applied, grant_verified, grant_revoked, grant_expired, workbook_committed, pr_merged, pre_state_captured, partial_applied, compensating_required, compensating_executed, stale_plan_rejected, callback_received, intent_hash_mismatch, sod_conflict_detected, expiry_revoke_escalated, notification_sent. `lock.py`: `_utcnow()` now `datetime.now(timezone.utc).replace(tzinfo=None)` (naive UTC; avoids deprecated utcnow).
`workers/expiry_worker.py` (NEW, G-09, 287 lines): `ExpiryWorkerConfig(estate_config)` reads `estate["expiry"]`: `revoke_worker.poll_interval_minutes` (15), `retry_attempts` (3), `retry_backoff_minutes` ([5,15,60]), `escalation_group` ("platform_admin"), `reference_counting` (True), plus `require_utc` (True), `max_ttl_days`. `ExpiryWorker.start()` loops `_poll_cycle` then `asyncio.sleep(poll_interval60)`; `stop()` clears flag. No scheduler registration seen in my scope. Per expired grant:
`_find_expired_grants`: RequestModel with `status=="completed"`, `expiry_at` not null and `< now (UTC)`, `revoked_at` null; identity (principal_name, securable_name, securable_type, privilege, environment) read from `state_context`.
Reference counting: `_count_active_references` counts completed, non-revoked requests with the same principal/securable/privilege (JSON `state_context[...]`); if `> 1` -> `_mark_expired` (fact `grant_expired {reference_decremented: True}`), no revoke.
Otherwise retry up to `retry_attempts`: `_revoke_grant` calls `rbac_verify_outcome(request_id, action="revoke", securable_type default "table", securable_name, principal_name, privilege)` (from `app.tools.self_service.rbac_verify_outcome`). Outcomes: `verified_removed`/`verified_granted` -> `_mark_revoked`; `residual_access` -> `_mark_revoked` with warning (direct grant removed, inherited remains); `indeterminate` -> retry; other -> warning. Between attempts sleep `retry_backoff_minutes[min(attempt, len-1)]` minutes.
`_mark_revoked`: fact `grant_revoked {outcome, actor:"expiry_worker"}`, sets `request.revoked_at = now(UTC)` and `request.status = "expired"` (new status), single commit.
All attempts failed -> `_escalate`: fact `expiry_revoke_escalated {escalation_group, reason: "Revoke failed after N attempts", grant}` actor `expiry_worker`; logs "ESCALATION: expiry revoke failed - notify <group>".
Gotchas: docstring says "Initiates revoke via the rbac_uc_revoke workflow" but `_revoke_grant` only calls the verify tool ("Verify current state first") and never issues a revoke; backoff uses `asyncio.sleep` inside the poll cycle (blocks the loop up to 5+15 min per grant); assumes `RequestModel.expiry_at` and `revoked_at` columns exist (see DB part of handover); "Extensions are new approved requests - not modifications to existing ones"; all timestamps absolute UTC.
6. Summary table: OPA policies
Bundle manifest `policies/manifest.yaml` (`bundle_version: "1.0.0"`, `provider_version: "1.52.0"`). Header: "Every policy package referenced by workflows or tools MUST be registered here. CI fails if a referenced package is missing, unregistered, unversioned, or not invoked at the ToolExecutor chokepoint." Each entry: package, file, description, input_schema.required, decision_schema.fields, invoked_at, precedence.
Package
File
Decides
invoked_at
Prec.
Required inputs
Decision fields
rbac.grants
rbac_grants.rego
hard_deny / extra_approval / warn / allow for grants
tool_executor
1
request, estate, registry
tier, messages
rbac.blast_radius
rbac_blast_radius.rego
inheritance widening, bulk and effective blast radius
tool_executor
2
request, estate
tier, messages, extra_approval_required, warnings
rbac.entitlements
rbac_entitlements.rego
workspace entitlement and system-group guardrails
tool_executor
3
request, estate
tier, messages, blocked, extra_approval_required, warnings
rbac.risk
rbac_risk.rego
risk tier standard/elevated/high/critical
pre_approval
1
request, estate, registry
risk_tier, risk_score, risk_factors
rbac.sod
rbac_sod.rego
separation of duties / self-approval
pre_approval
2
request, estate, existing_grants
action, sod_conflicts
rbac.drift
rbac_drift.rego
drift classification across planes
drift_worker
1
approved_state, live_state, estate
drift_items, remediation_required, severity
rbac.expiry
rbac_expiry.rego
TTL validity, revoke eligibility
[pre_approval, expiry_worker]
3
request, estate, active_references
expiry_valid, max_ttl_exceeded, revoke_eligible, reference_count
rbac.common
common.rego
shared helpers
library
0
none
none
Observations (recorded, not judged): rbac.risk reads `input.registry_entry.` while the manifest says `registry`; the rbac.risk file header says tiers come from `estate.approval_routing.tiers`, but thresholds are hardcoded; all new packages use `import rego.v1`; rbac_grants header: "Evaluated TWICE: before the Phase 1 Excel mutate AND again on the Phase 2 Terraform plan. A revised PR cannot sneak through an unapproved privilege delta."
7. Policy details
7.1 rbac.grants - three tiers
Inputs: `input.request.{privilege, securable_type, securable_name, principal_type (user|group|service_principal), principal_name, environment (dev/stg/prd), is_administrative, inherits_to_children, has_expiry}`; `input.estate.{workspace_entitlement_mode, system_groups[], admin_group_patterns[], cross_env_keywords[]}`.
hard_deny (blocked, no override), messages verbatim:
ALL_PRIVILEGES: "ALL_PRIVILEGES is deprecated and NEVER allowed. Use specific privileges."
MANAGE on catalog: "MANAGE on a catalog grants full administrative control over all child objects. This is not permitted via self-service."
principal_type user (and `not _is_exception_allowed_direct_grant`, a placeholder constant `false`): "Direct user grants are not permitted by EDH policy. Grants must be made to groups. Create a purpose-named group if a single-member grant is needed."
privilege `workspace_admin` to non-admin group: "Workspace admin entitlement can only be granted to designated admin groups."
principal in system groups: "Cannot modify system group '%s'. System groups have locked entitlements in explicit mode."
USAGE: "USAGE is deprecated. Use USE_CATALOG or USE_SCHEMA instead."
extra_approval:
schema + SELECT/MODIFY + inherits_to_children: "Granting %s on schema '%s' inherits to ALL current and future tables/views. This is a widening event requiring platform admin approval."
catalog + {SELECT, MODIFY, READ_VOLUME, WRITE_VOLUME}: "Granting %s at catalog level is a major widening event. Requires platform admin approval."
env prd + principal name contains a cross-env keyword: "Cross-environment grant detected: principal '%s' contains '%s' keyword but is requesting production access. Requires additional approval."
is_administrative, privilege != MANAGE, securable_type in {catalog, schema}: "Administrative privilege '%s' on %s requires platform admin approval."
MANAGE on schema: "MANAGE on schema '%s' grants ability to grant/revoke on all child objects. Requires platform admin approval."
warn: no `has_expiry` and privilege in {SELECT, MODIFY, WRITE_VOLUME, WRITE_FILES}: "This grant has no expiration date. Consider adding a review date or TTL for data access grants."; securable_type materialized_view/streaming_table: "Granting on %s - ownership may be tied to a Lakeflow pipeline. Verify the pipeline run-as identity owns this object."; principal_type service_principal: "Grants to service principals should be documented with the owning team and automation purpose."
Decision (verbatim logic): `default decision := {"tier": "allow", "messages": []}`; `hard_deny` if count(hard_deny)>0; `extra_approval` if no hard_deny and count>0; `warn` if neither and count(warn)>0.
Helpers: `_system_groups` = lowercased `estate.system_groups` else `{"users","admins","account users"}`; `_cross_env_keywords` else `{"dev"}`; `_is_admin_group(name)` uses glob patterns (`prefix`, `suffix`, `contains`) from `estate.admin_group_patterns`; fallback when none configured: startswith "admins" or "platform_admin".
7.2 rbac.blast_radius
Default decision `{"tier":"allow","messages":[],"extra_approval_required":false,"warnings":[]}`. extra_approval messages:
schema + SELECT + inherits_to_children: "Schema-level SELECT on '%s' grants read access to ALL current and future tables in this schema. Consider table-level grants for least privilege."
catalog, privilege not USE_CATALOG/MANAGE, inherits_to_children: "Catalog-level '%s' on '%s' inherits to ALL schemas and objects. This is a wide grant requiring extra approval."
`count(securable_names) > _bulk_threshold` (`estate.bulk_threshold`, default 10): "Bulk grant affects %d securables. Grants to >%d objects require additional approval."
Effective blast radius (G-16): `_effective_blast_radius := _effective_principals * _effective_environments * _effective_securables * _severity_weight`. principals = `count(request.nested_member_count)` else 1 (it calls count() on a field named like a number - will be undefined if numeric, then falls to 1); environments = `count(request.target_environments)` else 1; securables = `count(concrete_securables)` else `count(securable_names)` else 1; severity weight 3 for {MANAGE, ALL_PRIVILEGES}, 2 for {MODIFY, WRITE_VOLUME, WRITE_FILES, CREATE_TABLE}, else 1. Threshold `estate.effective_blast_radius_threshold` default 50. Message: "Effective blast radius is %d (%d principals x %d environments x %d securables x severity %d). Threshold: %d." (source uses the multiplication sign). Comment: "Wildcards are expanded before approval."
environment == "all" on catalog/schema: "Grant on '%s' with environment='all' affects ALL workspaces in the estate. Consider scoping to a specific environment."
warn: schema MODIFY: "Schema-level MODIFY on '%s' allows writing to ALL tables. Verify this is intentional (usually table-level MODIFY is preferred)."; catalog CREATE_TABLE: "CREATE_TABLE at catalog level allows creating tables in ANY schema. Consider schema-level grants."
Output: `tier` (extra_approval > warn > default allow), `messages` = extra_approval ++ warn, `extra_approval_required`, `warnings`. Never emits hard_deny.
7.3 rbac.entitlements
Inputs: request (securable_type "workspace"/"group", principal_name, securable_name, privilege, environment), estate (system_groups, admin_group_patterns, workspace_entitlement_mode). `admin_entitlements := {"CAN_MANAGE","workspace_admin","can_manage"}`.
hard_deny: workspace + principal in system groups: "Cannot modify entitlements for system group '%s'. System groups are managed by the platform."; securable_type "group" + securable_name in system groups: "Cannot modify membership of system group '%s'. System groups are immutable."; workspace + admin entitlement + non-admin group: "Cannot grant administrative entitlement '%s' to non-admin group '%s'. Only groups matching admin patterns may receive admin entitlements."
extra_approval: any admin entitlement: "Administrative entitlement '%s' requires platform admin approval due to elevated access level."
warn: legacy mode + `workspace_access`: "Workspace '%s' is in legacy mode. 'workspace_access' entitlement has no effect in legacy mode (all users already have access)."; `cluster_create` in prd: "cluster_create in production should be limited. Consider using instance pools and job clusters instead of interactive cluster creation."; `token_usage`: "token_usage allows creating personal access tokens. Ensure PAT monitoring and rotation policies are in place."
Decision: `tier` hard_deny > extra_approval > warn > allow; `messages` = hard_deny ++ extra_approval ++ warn; `blocked` (hard_deny>0), `extra_approval_required`, `warnings`. `_is_admin_group` fallback when no patterns configured: `contains(lower(name), "admin")` (looser than rbac.grants' startswith fallback).
7.4 rbac.risk (approval routing; weights sum)
Defaults tier "standard", score 0. Factors (name, weight): production_environment 20 (env prd); administrative_privilege 15 (`input.registry_entry.is_administrative`); manage_privilege 40 (MANAGE); inheriting_grant 15 (`registry_entry.inherits_to_children` and securable_type catalog/schema); cross_environment 25 (principal name contains `estate.cross_env_keywords` keyword AND env prd); bulk_grant 20 (`count(securable_names) > estate.bulk_threshold`); direct_principal 10 (principal_type != group); ownership_transfer 20 (`workflow_type == "rbac_ownership_transfer"`); emergency_access 35 (`workflow_type == "rbac_emergency_access"` - no such workflow exists in the catalog). Tiers: score >= 60 critical; 35-59 high; 15-34 elevated; <15 standard. Output `decision := {risk_tier, risk_score, risk_factors}`.
7.5 rbac.sod
`default action := "allow"`. `sod_conflicts` set of {type, action, message}:
self_approval (hard_deny) when `estate.approval_routing.deny_self_approval == true` and requester in `request.approvers`: "Self-approval not allowed: requester '%s' is also an approver"
approver_is_target (hard_deny): approver == `request.principal_name`: "Conflict of interest: approver '%s' is the target principal"
inactive_approver (hard_deny) when `approval_routing.require_active_approver == true` and approver in `input.inactive_approvers`: "Approver '%s' is inactive or unauthorized"
privilege_conflict from `estate.approval_routing.sod_rules[]` (fields `conflicting_privileges`, `same_securable`, `action`): `_rule_matches` true when (a) principal already holds one of the conflicting privileges in `input.existing_grants` AND `rule.same_securable == true` on the same securable, or (b) global: `request.privilege` in conflicting_privileges and `not rule.same_securable`. Message: "SoD conflict: principal already holds %v on the same securable".
Overall `action`: hard_deny if any conflict action hard_deny; else warn if any warn; else allow. Output `{action, sod_conflicts}`. (`_has_hard_deny`/`_has_warn` helpers avoid negation cycles under rego.v1.)
7.6 rbac.expiry
Defaults: `expiry_valid := true`, `max_ttl_exceeded := false`, `revoke_eligible := false`.
UTC requirement: if `estate.expiry.require_utc == true` and `request.expiry != null` and not endswith "Z" and not contains "+00:00" -> expiry_valid false; message "Expiry timestamp must be absolute UTC (ending in 'Z' or '+00:00')."
Max TTL by risk class: `_max_ttl_days := estate.expiry.max_ttl_days[input.risk_tier]` else null. `max_ttl_exceeded := true` if `request.ttl_days > max`, or if `request.expiry == null` (permanent) while a limit > 0 exists. Message: "TTL exceeds maximum for risk tier '%s': max %d days". Exceeded -> expiry_valid false.
Reference counting: `reference_count := count(input.active_references)` (default []); `revoke_eligible := true` if `reference_count <= 1`; warning when >1: "Grant is referenced by %d active requests. Revoke removes this reference only; grant persists until all expire."
Extensions: `request.is_extension == true` and `estate.expiry.extension_requires_new_request` -> "Policy requires extensions to be submitted as new requests with independent approval."
Output: `{expiry_valid, max_ttl_exceeded, revoke_eligible, reference_count, validation_messages, revoke_warnings}`.
7.7 rbac.drift
Inputs: `approved_state.{grants, ownership, memberships, entitlements}`; `live_state.{grants, ownership, deleted_securables, memberships, emergency_grants, entitlements}`. drift_items (type / plane / severity / remediation / message):
extra_grant / uc_grant / high / investigate_and_reconcile / "Ungoverned grant: %s on %s to %s" (live not in approved)
missing_grant / uc_grant / medium / apply_or_remove_from_desired_state / "Unapplied grant: %s on %s to %s"
ownership_drift / uc_grant / high / ownership_transfer_request / "Ownership drift on %s: live=%s, approved=%s"
deleted_securable / uc_grant / medium / remove_from_desired_state / "Securable deleted but still in approved state: %s"
extra_membership / identity / high / investigate_idp_change / "Ungoverned membership: %s in %s"
expired_emergency / uc_grant / critical / immediate_revoke / "Expired emergency grant still live: %s on %s" (live_state.emergency_grants with `expired == true`)
extra_entitlement / workspace / medium / investigate_and_reconcile / "Ungoverned entitlement: %s for %s"
Match keys: grants (securable_name, principal_name, privilege); memberships (group_name, member_name); entitlements (workspace_id, principal_name, entitlement). Severity rollup critical > high > medium > default "info". `remediation_required := true` if any items. Output `{drift_items, remediation_required, severity, drift_count}`. Listed as invoked by `drift_worker` (no such worker file in my scope).
7.8 common.rego / data_certification.rego (lint cleanup only, no semantic change)
`is_valid_expiry(exception, current_time)` -> `is_valid_expiry(exception, _)`; unused params of `resolve_action`, `resolve_reason`, `resolve_severity` replaced with `_`; unused `import data.databricks.governance.common` removed from data_certification.rego.
8. Golden transcripts (app/workflows/golden_transcripts.json)
Only additions: 9 new top-level keys (24 -> 33), no existing entry changed. Each has `gates`, `mutating`, `status:"completed"`, `tools`, `required_vars`, `approval_sequence`, `success_facts`, `error_scenarios`. Note several diverge from the graph JSON (golden is a coarse expectation file).
rbac_uc_grant: gates 0, mutating 2; tools validate_privilege, resolve_prerequisites, mutate_workbook, commit_workbook; required privilege, securable_type, securable_name, principal_name, environment. Error scenarios: hard_deny_all_privileges, hard_deny_direct_user (bob@example.com), extra_approval_schema_select (awaiting_extra_approval, gates 3, extra_gate platform_admin), estate_config_missing ("Mutation is DISABLED" when estate.yaml account_id is a placeholder), hard_deny_manage_catalog, hard_deny_usage_deprecated, extra_approval_cross_env (dev-analytics -> prd, "Cross-environment grant detected"), workbook_locked ("Workbook is currently locked", retryable), git_commit_failure ("Failed to commit to Git", mutation_applied true, retryable).
rbac_uc_revoke: gates 2, approval_sequence ["manager"]; tools read_workbook, mutate, commit. Errors: grant_not_found ("grant does not exist"), prerequisite_dependency (USE_SCHEMA revoke -> awaiting_confirmation, "other grants depend on this prerequisite").
rbac_bulk_access: gates 2 (manager, data_owner); errors partial_policy_block, package_not_found, bulk_threshold_exceeded.
rbac_group_membership: gates 2, approval_sequence manager, data_owner (graph uses group_owner); errors principal_not_found ("does not exist"), system_group_mutation (`account users` -> rejected "System groups cannot be modified").
rbac_ownership_transfer: gates 2, approval manager, data_owner (graph: data_owner, platform_admin); errors mv_st_pipeline_restriction, current_owner_mismatch.
rbac_role_assumption: gates 2 (manager, data_owner in golden; graph uses role_owner); errors invalid_role_pattern, feature_not_enabled.
rbac_ws_assignment: gates 1; errors system_group_blocked ("admins" -> "System groups cannot be assigned"), workspace_not_found ("ws-nonexistent" -> "Workspace not found").
rbac_ws_entitlement: gates 2 listed but approval_sequence ["manager"]; errors workspace_admin_to_non_admin, invalid_entitlement ("not a recognized entitlement").
rbac_drift_detection: gates 0; optional_vars environment, workspace_id, securable_type, securable_name; errors estate_config_invalid, workspace_unreachable (status partial).
9. Gotchas / reimplementation checklist
Keep `_phase2_` keys in specs but inert; `merge_gate` is not a valid `stages` kind per spec_loader.
`post_check` and `rbac_compensate` are declared but not enforced/wired anywhere in the executor in this diff.
uc_grant has zero in-graph human gates; approval is via hourly bundle (Pending Approvals). Poller must skip `pending_approval/approved/denied/exported`.
Param-name mismatches: group_membership instruction (principal_name/principal_type) vs graph (member_name/member_type); revoke/bulk graphs read `owner` and `manager_email` from context for approvers.
New gate types must be in `GATE_TYPES` (spec_loader), in poller `_v2_resume_value`, and be satisfiable in render `_gate_satisfied` (render's explicit list only has manager, platform_admin, data_owner, security, manual_task; check how group_owner/role_owner/workspace_admin/requester_confirm resolve in the rest of that function when porting).
Agent prompt RBAC shortcut (no confirmation for grant/revoke) conflicts with uc_revoke.md ("Never revoke without confirmation").
`expiry_worker` only verifies (does not revoke) and blocks with sleep; no registration of the worker seen in my scope.
Catalog loader now skips invalid specs with a warning instead of failing startup.
conditional_gate: loader uses raw eval fail-open; spec.py/render use safe AST (render: fail-open skip too; `ConditionalGate.evaluate_condition`: fail-closed).
04 - Backend API, DB, Services, Config (RBAC automation)
Branch `feature/EDTFNDTN-4251`; diff base = merge-base `3c832e69`. Paths relative to `edh-self-service-center/`. Stack: FastAPI + SQLAlchemy (Lakebase Postgres deployed, SQLite local). `get_db` comes from `app.db.session` (re-exported by `app.api.deps`); `get_current_user` from `app.api.deps`. Users have `user.has_role("Platform Admin")` / `"Governance Admin"` and `user.email`.
0. Router registration (`backend/app/api/v1/__init__.py`)
```python
from app.api.v1 import ..., governance, rbac, rbac_grid
router.include_router(rbac.router, prefix="/rbac", tags=["rbac"])
router.include_router(rbac_grid.router, prefix="/rbac-grid", tags=["rbac-grid"])
```
Mounted under `/api/v1` => `/api/v1/rbac/...` and `/api/v1/rbac-grid/...`.
1. Endpoint table
Method
Path (under /api/v1)
Auth
Request
Response
GET
/rbac/drift
any authenticated
-
`{records:[RbacDriftRecord], total, scanned_at}` (in-memory last scan; empty if none)
GET
/rbac/drift/summary
any authed
-
`{total, extra, missing, mismatch, last_scan}`
POST
/rbac/drift/scan
any authed
-
`{scan_id}`; 409 if scan in progress; runs `rbac_detect_drift` tool as BackgroundTask
DELETE
/rbac/test-data/purge
Platform Admin (403)
`?confirm=YES` (else 400)
`{deleted_mutation_records, deleted_replica_rows, message}`
GET
/rbac-grid/data
view (PA or GA)
`sheet?`, `environment?`, `page>=1`, `page_size 1..5000 (500)`
`List[GridRowOut]` ordered by id; calls `_auto_sync_if_empty`
GET
/rbac-grid/permissions
any authed
-
`{role, can_view, can_edit, can_sync, editable_columns, refresh_seconds}`
POST
/rbac-grid/commit
edit (PA)
`{changes:[{id,row_version,column,new_value?}]}`
`{committed, commit_id, new_versions:[{id,row_version}]}`; 400 empty, 403 column, 409 conflict, 422 validation
GET
/rbac-grid/audit
view
`since?` ISO, `page`, `page_size<=1000 (100)`
list of `grid_edit` audit rows
POST
/rbac-grid/add-row
edit
`AddRowRequest`
`{id,row_version,message}`; 409 dup, 422
POST
/rbac-grid/add-rows
edit
`{rows:[AddRowRequest]}` (<= maxBatchSize 50)
`{inserted, commit_id, ids, message}` atomic
GET
/rbac-grid/refresh
view
`since` required (400)
rows with `updated_at >= since`, max 1000
GET
/rbac-grid/sheets
view
-
`[{sheet_name,count}]` (auto-sync if empty)
GET
/rbac-grid/sync-status
view
-
`{replica_rows}`
POST
/rbac-grid/reconcile
edit
-
sync summary dict (re-seed from workbook); 500 on failure
GET
/rbac-grid/my-requests
any authed (own requests only)
`status?`, `limit<=200 (50)`, `offset`
`{total, bundles:[{bundle_id, request_count, status, environments, created_at, updated_at}]}`
GET
/rbac-grid/pending-approvals
any authed (NO role check)
`limit<=200`
`{bundles:[{bundle_id, request_count, requester, environments, created_at, updated_at}]}`
GET
/rbac-grid/bundle/{bundle_id}
any authed (NO ownership check)
-
bundle detail + per-status summary + requests; 404
POST
/rbac-grid/bundle/{bundle_id}/approve
Platform Admin
-
`{bundle_id, status: "exported"\
"approved", count, export:{env:{...}}}`
POST
/rbac-grid/bundle/{bundle_id}/reject
Platform Admin
`?reason=`
`{bundle_id,status:"rejected",count}`
POST
/rbac-grid/bundle/{bundle_id}/export
any authed (no role check)
-
`{bundle_id, results}`; 404 if error
GET
/rbac-grid/debug/export/{bundle_id}
NO AUTH
-
runs `_run_export`, returns diagnostics incl. traceback
GET
/rbac-grid/exports
NO AUTH
-
`{env:{path, files:[{name,size,modified}]}}` (local dirs only)
GET
/rbac-grid/exports/{env}/{filename}
NO AUTH
-
CSV download (`os.path.join(export_dir, filename)`; no traversal sanitising)
POST
/requests/check-duplicate
authed
`RequestCreate`
duplicate report or `{duplicate:false,...}`
POST
/requests
existing
`RequestCreate`
NEW: 409 + duplicate report JSON for `rbac_uc_grant`/`rbac_uc_revoke`
GET
/cache-debug (`/api/v1/cache-debug`, main.py)
none
-
static asset diagnostics
Gotchas: three unauthenticated endpoints (`debug/export`, `exports`, `exports/{env}/{filename}`); `/export` has no role check; `/pending-approvals` and `/bundle/{id}` readable by any authed user.
2. Permission model (`backend/app/services/rbac_grid_permissions.py`)
```python
@dataclass
class GridPermissions:
role: str # "platform_admin" | "governance_admin" | "none"
can_view: bool = False; can_edit: bool = False; can_sync: bool = False
editable_columns: List[str] = field(default_factory=list)
_ALL_VALUE_COLUMNS = ["environment","securable_type","catalog","schema_name","object_name",
"owner","principal_type","principal_name","privilege","workspace_name"]
```
Platform Admin: view+edit+sync; editable cols from env `RBAC_GRID_EDITABLE_COLS_ADMIN` (`""` = all columns, else comma list). Read via `os.environ`, not the Settings object.
Governance Admin: view only, no editable columns.
Else `role="none"`, no access.
`assert_can_edit_column(perms, column)` raises ValueError if `not can_edit` or column not in editable list; endpoint maps to 403.
`_require_view` -> 403 "You do not have access to the RBAC grid."; `_require_edit` -> 403 "Role '<role>' cannot edit the RBAC grid.".
Bundle approve/reject: direct `user.has_role("Platform Admin")`; 403 "Only users with Platform Admin role can approve|reject RBAC bundles."
`RBAC_GRID_VIEWER_GROUPS`, `RBAC_GRID_EDITOR_GROUPS`, `RBAC_GRID_EDITABLE_COLS_EDITOR` are declared in Settings but NOT used by the engine (leftover from a group-based design; app roles used instead).
Backend authoritative; UI is display-only gating.
3. DB tables
Schema: `Base.metadata.schema = DB_SCHEMA` env (default `selfservice`) unless env `TESTING` set (`backend/app/db/base.py`), so every query is schema-qualified (needed for Lakebase PgBouncer which strips `-csearch_path`). Tables auto-created by `Base.metadata.create_all`; models imported in `backend/app/db/__init__.py`:
```python
from app.db.rbac_mutation import RbacMutationRecord
from app.db.rbac_replica import RbacReplicaRow
```
3.1 `rbac_replica` (`RbacReplicaRow`, `backend/app/db/rbac_replica.py`)
Table name = env `RBAC_REPLICA_TABLE` (read from `os.environ` at import) else `rbac_replica`.
```
id INTEGER PK autoincrement
sheet_name VARCHAR NOT NULL -- e.g. UC_Securables_RBAC_Dev
environment VARCHAR NOT NULL -- dev|tst|stg|prd
securable_type, catalog, schema_name, object_name, owner, principal_type, principal_name, privilege, workspace_name VARCHAR NULL
row_version INTEGER NOT NULL default 1 -- optimistic lock
row_hash VARCHAR(16) NOT NULL default '' -- content hash
request_id, source_type(grid|agent|sync), request_type(grant|revoke|modify), record_status, validation_status, approval_status, bundle_id, export_status VARCHAR NULL
ready_for_terraform BOOLEAN default false
applied_at TIMESTAMP NULL
is_deleted BOOLEAN default false
idempotency_key, intent_hash VARCHAR NULL
updated_at TIMESTAMP server_default now() onupdate now()
updated_by VARCHAR NULL -- 'sync' for workbook-seeded rows, user email for grid
created_at TIMESTAMP server_default now()
INDEX ix_rbac_rep_sheet_env (sheet_name, environment)
INDEX ix_rbac_rep_principal (principal_name)
INDEX ix_rbac_rep_catalog (catalog, schema_name, object_name)
```
Row hash (dedupe) verbatim:
```python
def compute_row_hash(sheet_name, environment, securable_type, catalog, schema_name,
object_name, owner, principal_type, principal_name, privilege, workspace_name) -> str:
payload = "|".join(str(v or "") for v in (sheet_name, environment, securable_type, catalog,
schema_name, object_name, owner, principal_type, principal_name, privilege, workspace_name))
return hashlib.sha256(payload.encode()).hexdigest()[:16]
```
No unique constraint on (sheet_name,row_hash); dedupe is app-level only. Lifecycle columns (record_status, approval_status, export_status, ready_for_terraform) are only updated by `_run_export` for rows whose `request_id` matches; the grid never sets them. Columns `source_type, request_type, validation_status, bundle_id, applied_at, is_deleted, idempotency_key, intent_hash` are not written by anything in this section (presumably by the agent tools - see other parts).
3.2 `rbac_mutation_records` (`RbacMutationRecord`, `backend/app/db/rbac_mutation.py`)
Table name = env `RBAC_MUTATION_TABLE` else `rbac_mutation_records`.
```
id VARCHAR PK (uuid4)
request_id VARCHAR NOT NULL (indexed) -- commit_id for grid ops ("grid-<12hex>", "grid-add-..", "grid-batch-..") or requests.id
action VARCHAR NOT NULL -- grant|revoke|modify|grid_edit|grid_add|grid_add_batch
environment, securable_type, securable_name, principal_type, principal_name, privilege, target_sheet VARCHAR NOT NULL
catalog, schema_name, object_name, owner VARCHAR NULL
status VARCHAR NOT NULL default 'applied' -- applied | committed | failed | exported
change_detail JSON NULL
requester_email VARCHAR NULL; justification TEXT NULL
bundle_id, output_path, path_type(local|volume|workspace), file_name, checksum, target_environment VARCHAR NULL
created_at TIMESTAMP server_default now(); committed_at TIMESTAMP NULL
INDEX ix_rbac_mut_request_id (request_id); INDEX ix_rbac_mut_env_created (environment, created_at)
```
Used as audit trail AND by duplicate-check Layer 1.
3.3 `requests` table / models
`RequestModel.bundle_id = Column(String, nullable=True, index=True)` ("RBAC bundle grouping - multiple requests share a bundle_id for batch approval"). `RequestStatus` enum (`backend/app/models/request.py`) gained `PENDING_APPROVAL="pending_approval"`, `APPROVED="approved"`, `EXPORTED="exported"`, `DENIED="denied"`; `StateInfo` gained `resultSummary: Optional[Dict[str, Any]] = None` ({"summary":..., "details":{...}} - "so the UI can show WHAT each step produced").
Request type strings: `rbac_uc_grant` (also `rbac_uc_revoke` in approve/export/dup-check). `state_context` keys consumed: `environment, request_type, securable_type, securable_name, catalog, schema_name, object_name, workspace_name, owner, principal_type, principal_name, privilege`; written by this code: `approved_by, approved_at, rejected_by, rejected_at, rejection_reason, export_path, export_checksum, exported_at, path_type, denial_reason, blocked_privileges, denied_privileges, requires_security_escalation, escalated_privileges`.
3.4 `before_insert` ORM hook on RequestModel (`backend/app/db/request.py`) - policy enforcement
`@sa_event.listens_for(RequestModel, "before_insert") def _auto_bundle_rbac_request(mapper, connection, target)`; only for `target.type == "rbac_uc_grant"`. Every denial sets `status="denied"`, `current_state="denied"`, `ctx["denial_reason"]=...`, `target.state_context = ctx` (reassign so JSON change is detected), then `return`.
No `bundle_id`: `f"RBAC-{utc now:%Y-%m-%dT%H}"` (hourly bundle). No `status`: `pending_approval` (+current_state).
`environment` column empty and `ctx["environment"]`: `target.environment = ctx["environment"].strip().lower()` (safety net so bundle drill-down, export partitioning, sheet routing work).
Enum validation via `validate_enum_value` (lazy import from `app.api.v1.rbac_grid`) for ctx keys environment, securable_type, principal_type, privilege -> `"Enum validation failed: {err}"`.
`validate_field_pattern` for catalog, schema_name, object_name (= `ctx.securable_name or ctx.object_name`), owner, principal_name, workspace_name -> `"Pattern validation: ..."`; then `validate_cross_field_consistency` -> `"Cross-field consistency: ..."`.
`validate_cross_fields(securable_type, privilege)` -> `"Cross-field validation: ..."`.
`agenticPolicies` from `rbac_config.yaml`:
P1 `deniedRequestTypes` (config: revoke, modify) -> "Request type '<t>' is not allowed via agentic flow. Use the admin grid for <t> operations."
P2 `denyPrincipalTypeInEnv` (service_principal / "service principal" in tst, stg, prd; compare after `_`->space normalising) -> rule `reason`.
P3 `hardBlockedPrivileges` (ALL_PRIVILEGES, MANAGE, ADMIN, IS_OWNER, CREATE_CATALOG, CREATE_EXTERNAL_LOCATION, CREATE_STORAGE_CREDENTIAL, CREATE_SERVICE_CREDENTIAL, CREATE_FOREIGN_CATALOG, CREATE_MANAGED_STORAGE, CREATE_CLEAN_ROOM, SET_SHARE_PERMISSION, EXTERNAL_USE_LOCATION, EXTERNAL_USE_SCHEMA); privilege split on `;`, uppercased; reason "Privilege(s) [...] are BLOCKED via agentic flow. These require direct admin grid action with security approval."; sets `blocked_privileges`.
P4 `writeOnlyEnvironments` (dev): if env not in it and (privilege atoms intersect `writeDeniedPermissions` or req_type in {modify,revoke}) -> denied "Write/edit denied in '<env>' via agentic flow. Agentic writes are restricted to: [...]"; sets `denied_privileges`.
P5 `escalationRequiredPrivileges` (MODIFY, DELETE, WRITE_VOLUME, CREATE_TABLE/VIEW/FUNCTION/SCHEMA/CONNECTION/SHARE/RECIPIENT/PROVIDER, CAN_MANAGE, CREATE_MODEL, CREATE_SERVICE): NOT denied; sets `ctx["requires_security_escalation"]=True`, `escalated_privileges`.
`ImportError` -> pass; any other Exception -> warning logged, continue to fallback.
Hard-coded FALLBACK (safety net if config load fails): if env in (stage, stg, test, tst, prod, prd) and (privilege atoms intersect hard-coded `write_perms` set [MODIFY, DELETE, ALL_PRIVILEGES, MANAGE, WRITE_VOLUME, many CREATE_, ADMIN, CAN_MANAGE, IS_OWNER] or req_type in {modify,revoke}) -> denied "Write/edit denied in '<env>' (read-only environment). Only dev allows write operations."
Design note: the hook imports validators from `rbac_grid` lazily (circular-import avoidance); a reimplementation should put validators in a service module.
4. Config-driven validation (`rbac/config/rbac_config.yaml`, loaded by `_load_rbac_config()` in rbac_grid.py)
Top-level keys: `approval, environments, enumerations, writeDeniedPermissions, writeDeniedRequestTypes, validation, crossFieldValidation, agenticPolicies, fieldPatterns, crossFieldConsistency, denyPatterns, knownPrefixes`.
Loader (cached in module global `_rbac_config`, restart to reload): path env `RBAC_CONFIG_PATH` default `rbac/config/rbac_config.yaml`; candidates = that path (CWD-relative), `Path(__file__).resolve().parents[4]/rbac/config/rbac_config.yaml`, `parents[3]/...`; raises `FileNotFoundError`.
`environments.<env>`: `access` (read_write/read_only), `aliases`, `exportPath` (dev `/tmp/rbac/exports/dev`; commented `volumePath`). `validation`: `caseSensitive{environment,principalType,objectType,permission: true}`, `requiredColumns [environment, principal_type, privilege]`, `maxBatchSize 50`, `allowedEditColumns [10 value cols]`.
Validators (all in `rbac_grid.py`; shared by grid commit/add AND request before_insert hook):
`validate_enum_value(column, value)`: empty value + column in `requiredColumns` -> "`<col>` is required and cannot be empty."; `_ENUM_COLUMN_MAP = {environment->environment, securable_type->objectType, principal_type->principalType, privilege->permission}`; case-sensitive unless `validation.caseSensitive[enum_key]` false; `permission` values are split on `;` and each atom must be an exact enumeration member. Messages: "rejected privilege(s) [...]. Must be EXACT match from: ..." / "`<col>` '<v>' is not allowed. Must be exact match from: ...".
`validate_cross_fields(securable_type, privilege)`: from `crossFieldValidation.{workspaceSecurableTypes, workspacePrivileges, ucSecurableTypes}`; WS type -> every atom must be in `workspacePrivileges`; UC type -> no atom may be a WS privilege.
`validate_allowed_column(column)`: not in `allowedEditColumns` -> "Column '<c>' is not editable." (403; injection defense).
`validate_field_pattern(column, value, securable_type, environment)`: (a) `denyPatterns` (regex, case-insensitive `re.match`) apply to all columns NOT in `_ENUM_COLUMN_MAP` (comment: enum values tst/stg/prd match consonant-cluster heuristics); (b) `fieldPatterns.<column>` rules: optional `scope` (list of securable types), `envAware` (replace `{env}` in regex with lowercase environment), `atomDelimiter` (split value; every atom must match regex or an `exceptions` regex), `exceptions`. Passes if ANY applicable rule fully matches; else returns first rule's `description`. If config missing returns None (graceful degradation).
`validate_cross_field_consistency(row)`: `crossFieldConsistency` rules with `field`, optional `scope`, `rule`: `envSuffixMatch` (field value's trailing `_(dev|tst|stg|prd)` must equal row environment; `exemptPatterns` skip) and `envSuffixConsistency` (`field` vs `crossField` suffixes must agree). Skipped if field or env empty.
`_extract_env_suffix(value)` = `re.search(r'_(dev|tst|stg|prd)$', value.lower())`. Unused helpers: `_char_entropy`, `_is_write_request`.
Pydantic validators on `AddRowRequest` run first (422): `_RE_IDENTIFIER` for catalog/schema (2-255), `_RE_OBJECT` object_name (2-512), `_RE_PRINCIPAL` (email or group name >=5 chars) for owner/principal_name (min 5, max 255/512), `_RE_WS_NAME` workspace_name (min 3). Dropdown fields (environment, securable_type, principal_type, privilege) only stripped; environment required.
5. Grid read/edit/insert logic (`rbac_grid.py`)
5.1 `_auto_sync_if_empty(db)` (called by `/data`, `/sheets`)
```python
if get_replica_count(db) > 0: return
lock_acquired = db.execute(sa_text("SELECT pg_try_advisory_lock(hashtext('rbac_replica_sync'))")).scalar()
if not lock_acquired: logger.info(...); return
try:
if get_replica_count(db) > 0: return # double-checked locking
try: path = WorkbookStorage.resolve_path()
except Exception:
storage = WorkbookStorage.from_settings(); path = storage.acquire()
sync_workbook_to_replica(db, str(path))
except Exception as exc:
logger.error(...); try: db.rollback() ... # never propagates: grid serves empty rather than crash
finally:
try: db.execute(sa_text("SELECT pg_advisory_unlock(hashtext('rbac_replica_sync'))")) except: pass
```
GOTCHA: `pg_try_advisory_lock` is Postgres-only and executed outside the try block; on local SQLite an empty-replica `/data` would error. Guard by dialect when reimplementing. `WorkbookStorage` is in `app.tools.self_service.rbac_workbook_storage`.
5.2 `POST /commit` (cell edits, optimistic locking, all-or-nothing)
For each change in order:
`validate_allowed_column` -> 403; `assert_can_edit_column` -> 403.
`validate_enum_value(column,new_value)` -> 422.
Optimistic lock verbatim:
```python
row = db.query(RbacReplicaRow).filter(RbacReplicaRow.id == change.id,
RbacReplicaRow.row_version == change.row_version).first()
if row is None:
db.rollback()
raise HTTPException(409, f"Conflict: row {change.id} was modified by another user. Refresh and retry.")
```
(also 409 if id doesn't exist). Read-then-write in one transaction; no `SELECT ... FOR UPDATE` and no `UPDATE ... WHERE row_version=`, so true concurrent commits can still race under READ COMMITTED.
`validate_field_pattern` with row context (environment/securable_type use proposed value if that is the column being edited).
`validate_cross_field_consistency` on a post-change snapshot of [environment, catalog, workspace_name, object_name, securable_type, schema_name, principal_name, owner] -> 422.
`old_value = getattr(row, col)`; `setattr(row, col, new_value)` (no empty->None normalisation); `validate_cross_fields(row.securable_type, row.privilege)` -> `db.rollback()` + 422 "Row <id>: ...".
`row.row_version += 1; updated_at=now(utc); updated_by=user.email; row_hash=compute_row_hash(...)` (all 11 fields).
Add `RbacMutationRecord(action="grid_edit", status="committed", request_id=commit_id, target_sheet=row.sheet_name, change_detail={"column","old_value"(str or None),"new_value","commit_id"}, requester_email, committed_at)`; `securable_name = ".".join(non-empty catalog,schema,object) or object_name or ""`.
Single `db.commit()` at end. `commit_id = f"grid-{uuid4().hex[:12]}"`. Other validation HTTPExceptions mid-loop don't explicitly rollback, but nothing is committed (session discarded). Multiple changes to the same row in one request: after the first change the in-session `row_version` is incremented, so a second change carrying the original version gets 409. No duplicate-hash check on commit - an edit can create a duplicate of another row.
5.3 `POST /add-row`
Order: `_require_edit`; enum validation for `environment, securable_type, principal_type, privilege` (empty not allowed); `validate_cross_fields`; `validate_field_pattern` for catalog, schema_name, object_name, owner, principal_name, workspace_name (only if non-empty); `validate_cross_field_consistency`; `row_hash`; duplicate check (sheet_name + row_hash) -> 409 "A row with identical values already exists in <sheet> (id=N)."; insert (`row_version=1`, `updated_by=user.email`); `db.flush()` for id; audit `action="grid_add"`, `change_detail={"action":"add_row","commit_id","values": body.model_dump(exclude_none=True)}`; commit. `commit_id = grid-add-<12hex>`. No validation that `sheet_name` is a known sheet or consistent with environment.
5.4 `POST /add-rows` (batch)
Max rows `cfg.validation.maxBatchSize` (default 50) -> 400. Phase 1 validates ALL rows (same checks; errors prefixed `Row <idx>:`), dedupe against DB (409) and within batch (`h in hashes`, 409). Phase 2 inserts all (flush per row) with per-row audit (`action="grid_add_batch"`, `change_detail.batch_index`), one `db.commit()`. `commit_id = grid-batch-<12hex>`.
5.5 Other
`/audit` filters `action == "grid_edit"` ONLY (add/batch audits not returned), `created_at >= since`, newest first.
`/refresh`: polling; frontend polls every `refresh_seconds` (env `RBAC_GRID_REFRESH_SECONDS`, default 30, read via `os.environ` in `/permissions`).
`/reconcile`: edit-gated; calls `sync_workbook_to_replica`. A POST `/sync` endpoint was deliberately removed ("convergence via agent + grid commit paths").
No delete-row endpoint; `is_deleted` unused by the API.
6. Workbook -> replica sync (`backend/app/services/rbac_sync.py`)
Workbook is a one-time seed ("read-only after seeding"); later mutations go to the replica. Sheet sets:
UC (9 cols: Environment, Securable Type, Catalog, SchemaName, ObjectName, Owner, PrincipalType, PrincipalName, AccessPrivilege): `UC_Securables_RBAC_{Dev,Tst,Stg,Prd}`
Metastore (8 cols, no ObjectName; `object_name=None`): `UC_metastore_RBAC`
WS (7 cols: Environment, PrincipalType, PrincipalName, WorkspaceName, SecurableType, SecurableName, SecurablePermission): `WS_Securables_RBAC_{Dev,Tst,Stg,Prd}`; mapping securable_type=col4, object_name=col5, privilege=col6, workspace_name=col3; catalog/schema/owner None.
Other sheets (Sheet1/scratch) skipped. Header row skipped; fully empty rows skipped; values `str(x).strip()`.
`sync_workbook_to_replica(db, workbook_path, , sheets=None)`: openpyxl `read_only=True, data_only=True`; computes `row_hash` per record; indexes existing rows by `f"{sheet_name}|{row_hash}"`; inserts missing (`row_version=1, updated_by="sync"`); counts unchanged; deletes rows absent from workbook EXCEPT user rows:
```python
if row.updated_by and row.updated_by != 'sync': # user-added/edited via grid -> keep
preserved += 1; continue
db.delete(row); deleted += 1
```
Commits inside. Returns `{total_workbook_rows, inserted, unchanged, deleted, preserved_user_rows, sheets_synced}`. Caveats: a grid-edited row has a different hash from its workbook origin, so reseed re-inserts the original workbook row (duplicate lineage); selective `sheets=` still runs the delete pass over ALL existing rows (not used by API); identical duplicate workbook rows both insert on first sync (existing_by_hash not updated in-loop). `get_replica_count(db)` = `db.query(RbacReplicaRow).count()`.
7. Request bundle lifecycle (lower half of `rbac_grid.py`)
Requests come ONLY from the agentic flow; the grid is admin direct-edit. Statuses: `pending_approval -> approved -> exported` (or `rejected`, `denied`, `export_failed`).
Bundle id: `_generate_bundle_id()` = `RBAC-YYYY-MM-DDThh` (UTC hour); same format as the insert hook, so all requests in the same UTC hour share a bundle.
`/my-requests`: `RequestModel` where `requester_email == user.email`, `type == "rbac_uc_grant"`, `bundle_id IS NOT NULL`, optional status; bundle `status` = `MIN(status)` (alphabetical); environments via second DISTINCT query.
`/pending-approvals`: `status == "pending_approval"`; requester = MIN(requester_email).
`/bundle/{id}`: type `rbac_uc_grant` only; bundle status = single status or `"mixed"`; summary `total/pending_approval/approved/denied(+rejected)/exported`; per request: id,title,type,status,current_state,environment,bundle_id,requester_email,state_context,state_summary,created_at,updated_at.
`/approve` (PA): rows type in (grant, revoke) AND `status == pending_approval` -> `status=approved`, `current_state=approved`, `updated_at`, ctx `approved_by`, `approved_at`; commit (500 on failure); THEN `_run_export` in a separate try so export failure never rolls back approval; response status `"exported"` if no `"error"` key in results else `"approved"`. No self-approval prevention.
`/reject`: only `rbac_uc_grant` pending rows (revoke not covered); sets `rejected`, ctx `rejected_by/at/rejection_reason`.
`_run_export(bundle_id, db, log)`: loads config (returns `{"error": ...}` on failure); selects type in (grant, revoke) with `status == "approved"` (else `{"error":"no approved requests to export"}`); groups by `r.environment or "dev"`; `export_dir = envs_cfg[env].exportPath or f"./runtime/rbac/exports/{env}"`; file name `f"{bundle_id.replace(':','-')}_{YYYYMMDDTHHMMSS}.csv"` (one consolidated CSV per env); columns:
```python
_CSV_COLUMNS = ["environment","request_type","object_type","catalog","schema_name","object_name",
"workspace_name","owner","principal_type","principal_name","permission"]
```
filled from `state_context` (request_type default "grant"; securable_type->object_type; privilege->permission). `checksum = sha256(csv)[:16]`. `_write_csv_to_path(full_path, csv, log)`: `/Volumes/...` -> `WorkspaceClient().files.upload(overwrite=True)` => "volume"; `/Workspace/...` -> `workspace.mkdirs(parent)` + `workspace.import_(path, content=base64, format=ImportFormat.AUTO)` => "workspace"; else local `os.makedirs` + `write_text` => "local" (EPHEMERAL on Databricks Apps). Per request: `status/current_state="exported"`, ctx `export_path, export_checksum, exported_at, path_type`; linked replica rows (`RbacReplicaRow.request_id == r.id`) set `record_status="exported", approval_status="approved", export_status="exported", ready_for_terraform=True`; add `RbacMutationRecord(action=ctx.request_type or "grant", status="exported", target_sheet=f"export_{env}", bundle_id, output_path, path_type, file_name, checksum, target_environment, committed_at)`. On env failure: requests -> `export_failed`, `results[env]={"status":"failed","error"}`. Final `db.commit()` (rollback and `{"error": ...}` on failure). Per-env success result: `{"status":"success","file","path","path_type","records"}`. Re-running `/export` is safe (only `approved` rows picked). Partial env failure still yields top-level approve status "exported" (only `error` key checked). Replica rows linked via `request_id` are not created here (expected from agent tools).
`/exports` list and download only read LOCAL directories (not Volumes/Workspace).
8. Duplicate / idempotency check (`backend/app/api/v1/requests.py`)
`_RBAC_MUTATION_TYPES = {"rbac_uc_grant","rbac_uc_revoke"}`. `_check_rbac_duplicate(metadata, db)`; inputs from `request_data.metadata`: `action` (default grant), `principal_name`, `securable_name`, `privilege` (uppercased), `environment` (lowercased; inherited from `request_data.environment.value` if missing), `securable_type` (lower), `principal_type`. Returns None if principal/securable/privilege/environment empty. Layers all always run:
Audit: `RbacMutationRecord` with `action in {"grant","grid_add","grid_edit"}`, same principal_name/securable_name/privilege/environment, `status in {"applied","committed","exported"}`, newest first.
(workbook read-back layer removed: workbook read-only seed)
In-flight: `RequestModel` type in mutation types AND `status NOT IN {"completed","rejected","failed","denied","export_failed","exported"}` (so users can resubmit after denial/export failure); matched in Python on `state_context` principal_name, securable_name, privilege (upper), environment (lower).
Replica: sheet from `RbacWorkbookSerializer.determine_target_sheet(_FakeRecord(env, SecurableType(stype)))` (`app.tools.self_service.rbac_workbook_serializer`, `rbac_models.SecurableType`); filter sheet_name, principal_name, privilege, environment; for UC securables split `securable_name` on "." -> catalog/schema_name/object_name; for securable_type in (workspace, workspace entitlement, folder, cluster, sql warehouse) match `object_name == last segment`.
Verdicts: `confirmed_duplicate` (audit+replica), `discrepancy` (audit only; "audit record is authoritative"), `replica_only`, `in_flight_only`, `duplicate_detected`. Response: `{duplicate:true, verdict, action, principal_name, principal_type, securable_type, securable_name, privilege, environment, message, evidence:[...], checks_performed:{lakebase_audit|in_flight|replica: hit|miss|error}, layer_errors?}`. A failing layer is logged and treated as miss (fail-open). `POST /requests/check-duplicate` returns report or `{duplicate:false, message:"No existing grant found - safe to submit.", principal_name, securable_name, privilege, environment}`; non-RBAC types return `{duplicate:false,...}`; `POST /requests` returns `JSONResponse(409, content=dup)` before creating. `GET /requests` list now `order_by(RequestModel.created_at.desc())` before offset/limit. Note: a revoke is checked against "grant exists" evidence, so a revoke of an existing grant is reported as duplicate (blocked) - behavior as written.
9. Drift API (`backend/app/api/v1/rbac.py`)
State is module-level in-memory (`_latest_drift`, `_scan_in_progress`): per-process, lost on restart, wrong with multiple workers. Scan: `from app.tools.self_service.rbac_detect_drift import rbac_detect_drift; result = await rbac_detect_drift()`; expected result `{"drift_records":[{securable_type, securable_name, principal_type, principal_name, privilege, drift_type: extra|missing|mismatch, environment, workbook_value, live_value}], "scanned_at"}`. On exception stores `{"status":"error","message":..., "drift_records":[]}`; `finally` resets flag. `scan_id` is just a uuid, not tied to state. Purge uses `next(get_db())`, deletes ALL rows of `RbacMutationRecord` and `RbacReplicaRow` (`synchronize_session=False`), rollbacks on error, logs email; purpose: reset Lakebase test state so duplicate check doesn't block.
10. Settings added (`backend/app/core/config.py`, `Settings`; pydantic-settings with `extra="ignore"` so vars must be declared)
```python
RBAC_WORKBOOK_PATH: str = ""
RBAC_STORAGE_BACKEND: str = "filesystem"
RBAC_DISCOVERY_CATALOG: str = ""
RBAC_MUTATION_TABLE: str = "rbac_mutation_records"
RBAC_REPLICA_TABLE: str = "rbac_replica"
RBAC_GRID_REFRESH_SECONDS: int = 30
RBAC_GRID_VIEWER_GROUPS: str = ""
RBAC_GRID_EDITOR_GROUPS: str = ""
RBAC_GRID_EDITABLE_COLS_EDITOR: str = "principal_name,privilege"
RBAC_GRID_EDITABLE_COLS_ADMIN: str = ""
```
Actual consumers read `os.environ` directly (table names at import time, `RBAC_GRID_EDITABLE_COLS_ADMIN`, `RBAC_GRID_REFRESH_SECONDS`, `RBAC_CONFIG_PATH`, `DB_SCHEMA`), so these must be real environment variables (e.g. `databricks.yml`) set before import; Settings values from `.env` alone do not populate `os.environ` unless dotenv does.
`default_config.py`: adds a Command Center home-screen section `{"title":"RBAC & Permissions","icon":"Shield","cards":[...]}`: "Grant UC Access" (prompt "I need to grant UC access to a data asset."), "Revoke Access" ("I need to revoke access from a principal."), "Workspace Assignment" ("I need to assign someone to a workspace."), "Group Membership" ("I need to add someone to an identity group."), "Ownership Transfer" ("I need to transfer ownership of a data asset."), and "Drift Report" with `"route": "/rbac/drift"` (no prompt). Descriptions e.g. "Request Unity Catalog privileges (SELECT, USE_CATALOG, etc.) on a securable."
11. DB session & migrations
`backend/app/db/session.py`:
`_is_databricks_environment()` extracted; now also true if `PGHOST` set.
`_resolve_lakebase_project(sdk=None)`: GET `/api/2.0/postgres/projects`; match `name.endswith(settings.DATABASE_INSTANCE_NAME)` else first project (with warning). `_fetch_lakebase_credential(sdk, project)`: POST `/api/2.0/postgres/credentials` body `{"endpoint": f"projects/{project}/branches/production/endpoints/primary"}` -> `token`. Both reused by `get_lakebase_token` and `get_database_url`.
DB name discovery fix: use `databases[0]["status"]["postgres_database"]` (real PG name uses underscores, e.g. `databricks_postgres`) falling back to resource-id from the name path (hyphenated).
search_path hardening for PgBouncer-pooled Lakebase: engine `connect` listener now runs `CREATE SCHEMA IF NOT EXISTS "<schema>"; SET search_path TO "<schema>", public;` + commit; new pool `checkout` listener re-issues `SET search_path` + commit (pool ROLLBACK can reset session SET); new `_set_search_path(session)` (skips sqlite; uses `get_db_schema()`) called in `get_db()` and `get_lakebase_session()` before first query. Together with `Base.metadata.schema` this is a triple layer.
`backend/app/db/migrate.py`: `run_startup_migrations` refactored so each step is wrapped via `_safe_step(label, fn, args, kwargs)` (log warning, continue) because a single InsufficientPrivilege (e.g. on workflow_versions) previously skipped all later steps. New `_fix_table_ownership(engine)` runs first (Postgres only; if current_user is superuser, `ALTER TABLE selfservice.<tbl> OWNER TO "<current_user>"` for tables in schema `selfservice` not already owned by it; errors logged at debug). Step list otherwise unchanged (skills->workflows renames, workflows.disabled, workflow_versions.kind/note, context_documents.retrieval_count/last_retrieved_at, data_assets.certification_rule_results, requests.digest_emitted_at/state_summary, indexes, enforcement_audit.workspace, tool_registry renames/columns, report_subscriptions.timezone, hot-path indexes, approvals.instructions/due_at, prune orphans). NOTE: there is NO migration for `requests.bundle_id` and none for the RBAC tables. `create_all` creates the two new RBAC tables, but on an existing `requests` table `bundle_id` must be added (`ALTER TABLE requests ADD COLUMN bundle_id VARCHAR` + index `ix_requests_bundle_id`) or every `requests` query fails. A reimplementation should add `_safe_step("add requests.bundle_id", _add_column, engine, "requests", "bundle_id", "VARCHAR")` and `_add_index(engine, "ix_requests_bundle_id", "requests", ["bundle_id"])`. Likewise the `workflow_versions.kind/note` tolerance in section 12 exists because migrations may lag.
12. workflows / workflow_service changes (schema-lag tolerance)
`workflows.py list_workflows`: `WorkflowTestService.health_map` and `WorkflowService.last_published_map` each wrapped in try/except -> `{}` with a warning.
`WorkflowService.list_versions`: try full query; on Exception `db.rollback()` and fall back to `with_entities(id, workflow_id, workflow_key, version, published_at, published_by)` ordered by version desc (when `kind`/`note` columns absent).
`last_published_map`: on exception rollback and fall back to query without `kind == "publish"` filter, building `{workflow_id: published_at.isoformat()}` (first/newest per workflow).
Catalog seeding: for existing `source == "seed"` workflows with empty `allowed_tools` and `spec.get("allowed_tools")`, backfill `existing.allowed_tools = spec["allowed_tools"]`; new seeded workflow created with `allowed_tools=spec.get("allowed_tools")` (RBAC workflow catalog specs carry their tool allowlist).
13. main.py, run.py, mcp_server.py, model client
`main.py` `spa_fallback_handler` (404 handler) rewritten: never intercept `/api/`, `/.`, `/_`, `/health`, `/logz`, `/favicon.ico` (JSON 404); JSON 404 if no STATIC_DIR; serve real static files; paths with a file extension that miss -> JSON 404 (not index.html); extension-less paths -> `index.html` with headers `Cache-Control: no-cache, no-store, must-revalidate`, `Pragma: no-cache`, `Expires: 0`. Root `/` now 302-redirects to `/?_cb=<epoch>` when no `_cb` query param (CDN cache-bust), then serves index.html with the no-cache headers (needs `RedirectResponse` imported). Added unauthenticated `GET /api/v1/cache-debug` (static dir, `s2` asset names, JS/CSS refs in index.html, `has_style_tag`). No RBAC seeding in main.py; tables come from `create_all`.
`backend/run.py`: Databricks Apps crash diagnostics: flushed prints; `_log_crash(stage,msg)` records RSS/uptime and uploads to a HARD-CODED workspace path `/Workspace/Users/<developer-email>/RBAC_Automation/_crash_log.txt` via `WorkspaceClient().workspace.upload(..., overwrite=True, format=ImportFormat.AUTO)`; `sys.excepthook`, SIGTERM/SIGINT handlers (log stack then `sys.exit(128+n)`); 5s heartbeat thread printing `[hb] <uptime>s RSS=<MB> thr=<n>`; imports `resource` (UNIX-only; fails on Windows); imports `app.main` in try/except (logs "import"), runs `uvicorn.run(app, host="0.0.0.0", port=8000)` in try/except/finally (logs "exit"). Debug scaffolding with personal path - parameterise or omit.
`mcp_server.py`: `_extract_obo_identity` also decodes the OBO JWT payload (unverified; base64url with padding) to set `identity["roles"] = claims.roles or claims.groups or []` and `identity["entitlements"]` (best effort; debug log on failure) so OPA role-gated rules work for MCP callers (previously roles always `[]`). `_governed_shim` now uses a unique `tool_call_id = f"mcp:{tool.name}:{uuid.uuid4().hex[:12]}"` (old static `mcp:<name>` caused idempotency/audit to replay cached results across different calls; important for RBAC mutation tools).
`model_serving/client.py`: `ModelServingClient.__init__` no longer raises `ValueError` when no token is acquired; it logs a warning instead so AgentRunner construction doesn't 500; the first call surfaces 401 as a friendly error.
14. camelCase handling
No snake/camel conversion layer was added in the backend for these files. All `/rbac-grid/` and `/rbac/` responses and request bodies are snake_case (`sheet_name`, `row_version`, `new_value`, `bundle_id`, `request_count`, `editable_columns`, `refresh_seconds`, ...); the frontend must consume them as snake_case. camelCase only appears in pre-existing `StateInfo` (`gateType`, `resultSummary`) and in YAML config keys (`maxBatchSize`, `exportPath`, `writeDeniedPermissions`, `allowedEditColumns`, `objectType`, `principalType`, `permission`, ...), mapped to snake_case column names via `_ENUM_COLUMN_MAP`.
15. Safety mechanisms to preserve
Backend re-checks permission on every mutation; UI gating is cosmetic.
Optimistic concurrency via `row_version` + 409; batch add all-or-nothing in one transaction; per-sheet content-hash dedupe.
Column whitelist + strict enum + field regex + cross-field + env-suffix validators shared between grid and agentic flow (single `rbac_config.yaml`).
Insert hook auto-denies agentic requests that violate policy (write in non-dev, SPN in non-dev, hard-blocked privileges, revoke/modify), plus hard-coded fallback if config load fails.
Pre-submission 3-layer duplicate check (audit, in-flight, replica), fail-open per layer, 409 with evidence.
Approval commit independent of export; per-env CSV with checksum, audit rows, replica flags (`ready_for_terraform`).
Postgres advisory lock + double-check for first-load auto-seed; sync preserves user-edited rows.
Audit row for every grid edit/add in `rbac_mutation_records` (but `/audit` lists only `grid_edit`).
Startup resilience: per-step migrations, schema-qualified metadata, search_path triple hardening, tolerant list queries, ModelServingClient non-fatal auth.
Open items for the reimplementer: unauthenticated export/debug endpoints and filename path traversal; missing `bundle_id` migration; advisory lock on SQLite; in-memory drift state; unused `RBAC_GRID_VIEWER/EDITOR_GROUPS`; no delete-row endpoint; reject covers only grant while approve covers grant+revoke; hard-coded personal path in `run.py`.
05 - Frontend and Tests (branch feature/EDTFNDTN-4251, diff vs 3c832e69)
0. Summary table
Frontend
File
New/Mod
Routed / reachable?
Purpose
Backend endpoints
src/pages/RbacGrid.tsx (1058 lines)
NEW
`/rbac-grid`, sidebar "RBAC Admin" (Platform Admin, Governance Admin)
Editable Excel-like grid replica of the RBAC workbook (Lakebase tables)
GET `/api/v1/rbac-grid/{permissions,sheets,data}`, POST `/rbac-grid/{commit,add-row}`
src/pages/RbacBundles.tsx
NEW
`/bundles`, `/bundles/:bundleId` (no sidebar entry)
List and detail of RBAC request bundles; approve, reject, export CSV
via api.ts bundle helpers (`/rbac-grid/my-requests`, `/bundle/{id}[/approve,/reject,/export]`)
src/pages/Approvals.tsx
MOD (+204)
existing `/approvals`
Adds "RBAC Bundles Pending Approval" section with expand, approve, reject
`/rbac-grid/pending-approvals`, bundle detail/approve/reject
src/pages/Requests.tsx
MOD (+463)
existing `/requests`, `/requests/:id`
Adds RBAC bundle section, synthetic RBAC step timeline, search box, "All" filter, direct-fetch of a request not in store, resultSummary rendering
`/rbac-grid/my-requests`, bundle detail, `getRequest`
src/pages/RbacRequest.tsx
NEW but DEAD
import commented out in App.tsx; `/rbac/request` redirects to `/`
Quick-action cards that prefill chat prompts (consolidated into the Ask Anything agent)
none (ChatView)
src/pages/RbacDriftReport.tsx
NEW but DEAD
import commented out; `/rbac/drift` redirects to `/`
Drift table with filter, sort, scan
`/rbac/drift`, `/rbac/drift/summary`, `/rbac/drift/scan`
src/components/RbacStatusCard.tsx
NEW but UNUSED
not imported anywhere
6-stage pipeline progress widget for `rbac_` requests, polls `getRequest`
`GET request`
src/components/RequestGraphView.tsx
MOD (1 line)
existing
passes `nodeDetails={graph.node_details}`
n/a
src/components/admin/WorkflowGraphPreview.tsx
MOD
existing
New `nodeDetails` prop, `detail` line on node, node width 180 -> 220
n/a
src/components/admin/WorkflowEditor.tsx
MOD
existing
`conditional_gate` and `merge_gate` stage kinds reuse GateForm
n/a
src/components/layout/Sidebar.tsx
MOD
n/a
Adds `rbac_admin` nav item
n/a
src/App.tsx
MOD
n/a
Routes + redirects
n/a
src/index.css
MOD
n/a
`.grid-header-label` text-transform override
n/a
src/services/api.ts
MOD (+253)
n/a
`checkDuplicate`, `DuplicateRequestError`, 409 handling, drift API, bundle API, new stage types, `RequestGraph.node_details`
see section 1
src/types/index.ts
MOD
n/a
New RequestStatus values, `State.resultSummary`
n/a
Tests (all under backend/tests)
Suite
Cases
What it verifies
Mocks
test_rbac_discovery_reader.py (568 lines)
~25
Estate YAML config resolution, snapshot freshness, grant queries against Delta tables via SQL warehouse
`WorkspaceClient` (autospec), `settings`, tmp estate YAML
test_rbac_drift_detection.py
5
`rbac_detect_drift`: extra/missing/in-sync/filter/API-error
`rbac_get_effective_access`, `rbac_read_estate_config`, `settings`, real openpyxl workbook
test_rbac_opa_policies.py
17
Only builds OPA input dicts and asserts on the inputs; never evaluates Rego
none
test_rbac_prerequisite_chain.py
9
`rbac_resolve_prerequisites` parent chains and dedup
`settings`, `RbacWorkbookParser`, tmp registry YAML
test_rbac_privilege_validation.py
14
`rbac_validate_privilege` normalization, allowlist, deprecation
`settings.RBAC_REGISTRY_PATH`, tmp registry YAML
test_rbac_workbook_mutation.py
10
`rbac_mutate_workbook` grant/revoke/dry-run/policy/estate/validation/packed cells
estate config, `rbac_validate_privilege`, `_evaluate_opa_policy`, `settings`
test_rbac_workbook_parser.py
21
`RbacWorkbookParser` UC and WS sheets, packed-cell expansion, roundtrip
none (real openpyxl workbook in tmp_path)
test_rbac_workflow_e2e.py (843 lines)
~100 parametrized
Static contract tests of workflow JSON, instruction files, tool modules, rego files, golden transcripts (not real execution)
`GitHubProvider`, `settings` fixtures (mostly unused by the tests)
integration/test_rbac_integration.py
10
LIVE Databricks smoke tests
none (real APIs); skipped unless `TEST_MODE=real`
1. Frontend
1.1 src/services/api.ts additions
All use `API_BASE_URL` and `getHeaders()` unless stated.
Duplicate check (pre-submission):
```ts
export interface DuplicateCheckResult {
duplicate: boolean; message: string; action?: string;
principal_name?; principal_type?; securable_type?; securable_name?; privilege?; environment?;
evidence?: Array<{ source: 'lakebase_audit' | 'workbook' | 'pending_request'; [key: string]: any }>;
}
export async function checkDuplicate(type: RequestType, title: string, environment?: Environment, metadata?: Record<string, any>)
// POST /requests/check-duplicate body {type,title,environment,metadata}
// on !ok: throws Error(err.detail || `Duplicate check failed: ...`)
export class DuplicateRequestError extends Error { readonly data: DuplicateCheckResult; name='DuplicateRequestError' }
```
`createRequest` now handles conflict:
```ts
// Handle 409 Conflict — duplicate grant already materialized
if (response.status === 409) {
const dup: DuplicateCheckResult = await response.json();
throw new DuplicateRequestError(dup);
}
```
Gotcha: the 409 body is the DuplicateCheckResult itself (NOT wrapped in `detail`). `checkDuplicate` is added to the exported `api` object. No page in this section's files calls `checkDuplicate` or catches `DuplicateRequestError` (likely used by the chat/agent UI elsewhere; verify with grep when reimplementing).
Drift API:
`getRbacDrift()` GET `/rbac/drift` -> `{records: RbacDriftRecord[], total}`
`getRbacDriftSummary()` GET `/rbac/drift/summary` -> `{total, extra, missing, mismatch, last_scan|null, scan_in_progress}`
`triggerRbacDriftScan()` POST `/rbac/drift/scan` -> `{scan_id}`
`RbacDriftRecord`: `{id, drift_type:'extra'|'missing'|'mismatch', securable_type, securable_name, principal_name, privilege, environment, workbook_value?, live_value?, detected_at}`.
These fail with plain `Error("Failed to ...: <statusText>")` (no detail parsing).
Bundle API (all under `/rbac-grid/`, id passed through `encodeURIComponent`):
fn
HTTP
Notes
`getRbacBundles()`
GET `/rbac-grid/my-requests`
Backend returns `{ total, bundles: [...] }`; fn returns `data.bundles \
\
[]`
`getPendingRbacBundles()`
GET `/rbac-grid/pending-approvals`
same wrapper, returns `data.bundles \
\
[]`
`getRbacBundleDetail(id)`
GET `/rbac-grid/bundle/{id}`
returns `RbacBundleDetail` directly
`approveRbacBundle(id)`
POST `.../approve`
-> `{status,count}`
`rejectRbacBundle(id, reason?)`
POST `.../reject` body `{reason}`
-> `{status,count}`
`exportRbacBundle(id)`
POST `.../export`
-> `{bundle_id, results: Record<env, {status:'success'\
..., records, file, error}>}`
Types:
```ts
RbacBundleSummary { bundle_id, request_count, status, environments: string[], created_at, updated_at }
RbacPendingBundle { bundle_id, request_count, requester, environments, created_at, updated_at }
RbacBundleRequest { id, title, type, status, environment, bundle_id, state_context: Record<string,any>, requester_email, created_at, updated_at }
RbacBundleDetail { bundle_id, requests: RbacBundleRequest[], summary:{total,pending_approval,approved,denied,exported} }
```
`state_context` keys used by UI: `privilege, securable_type, securable_name, principal_type, principal_name, environment, requires_security_escalation, escalated_privileges[], denial_reason`.
All 6 bundle fns + 3 drift fns + `checkDuplicate` are added to the `api` export object. Errors are `Error("Failed to <verb> bundle: <statusText>")` (backend `detail` is NOT surfaced).
Graph/workflow types:
`RequestGraph.node_details?: Record<string,{resolved_args?: Record<string,unknown>; description?: string|null}>`
New `WorkflowConditionalGateStage {kind:'conditional_gate', name, $when?, auto_approve?, inner_gate?:{type?,waiting_status?,approvers_from?}, _comment?}` (comment: fires only when `$when` is true, otherwise auto-approved/skipped; real gate config lives in `inner_gate`).
New `WorkflowMergeGateStage {kind:'merge_gate', name, waiting_status?, target_branch?, reject_on_rebase?, validate_intent_hash?, _comment?}` (blocks until a PR targeting the branch is merged).
`WorkflowStage` union now includes both.
1.2 src/types/index.ts
`RequestStatus` adds: `'pending_approval' | 'approved' | 'exported' | 'denied' | 'export_failed'`.
`State` (state-machine linear state) adds `resultSummary?: {summary: string; details: Record<string,any>}` ("Structured result summary for completed steps").
1.3 App.tsx, Sidebar, CSS
```tsx
<Route path="/rbac-grid" element={<RbacGrid />} />
<Route path="/bundles" element={<RbacBundles />} />
<Route path="/bundles/:bundleId" element={<RbacBundles />} />
<Route path="/rbac" element={<Navigate to="/" replace />} />
<Route path="/rbac/request" element={<Navigate to="/" replace />} />
<Route path="/rbac/drift" element={<Navigate to="/" replace />} />
```
Comment in source: "RBAC-specific pages removed — consolidated into the Ask Anything agent." `RbacRequest` and `RbacDriftReport` imports are commented out (files remain on disk, orphaned).
Sidebar item (lucide `Table2`):
```ts
{ id: 'rbac_admin', title: 'RBAC Admin', path: '/rbac-grid', group: 'Control Tower',
allowedPersonas: ['Platform Admin', 'Governance Admin'] }
```
There is NO sidebar item for `/bundles`; it is reached by direct URL, or the Requests/Approvals bundle sections (which expand inline rather than navigating).
index.css (top of file):
```css
.grid-header-label, table th .grid-header-label, thead th .grid-header-label { text-transform: none !important; }
```
1.4 RbacGrid.tsx (route `/rbac-grid`)
Purpose: "editable Lakebase replica of the RBAC workbook". Platform Admin edits; Governance Admin read-only. All styling is inline `style={{}}` objects (not Tailwind), with per-sheet colour theme.
API calls (raw `fetch`, hard-coded `/api/v1/rbac-grid/...`, `credentials: 'include'` - it does NOT use `API_BASE_URL`/`getHeaders`; errors surface as `throw new Error(await resp.text())`, i.e. raw response body in the red banner):
On mount `GET /api/v1/rbac-grid/permissions` -> `Permissions {role, can_view, can_edit, can_sync, editable_columns: string[], refresh_seconds}`. role values: `platform_admin`, `governance_admin` (badge text/colour).
`GET /api/v1/rbac-grid/sheets` -> `[{sheet_name, count}]`; first sheet auto-selected if none selected (`loadSheets` depends on `selectedSheet`).
`GET /api/v1/rbac-grid/data?sheet=<enc>&page_size=5000` -> `GridRow[]` (flat array, not wrapped). Fields: `id, sheet_name, environment, securable_type, catalog, schema_name, object_name, owner, principal_type, principal_name, privilege, workspace_name, row_version, updated_at, updated_by`.
`POST /api/v1/rbac-grid/commit` body `{changes:[{id,row_version,column,new_value}]}` -> `{committed: n}`.
`POST /api/v1/rbac-grid/add-row` body = `addRowData` (flat map of column key -> string plus `sheet_name`) -> `{message}`.
Guards: `perms` null -> "Loading permissions…"; `!perms.can_view` -> "Access Denied ... restricted to Platform Admin and Governance Admin roles." Read-only banner when `!can_edit`.
Per-sheet column sets (header label differs from DB key; labels are CamelCase to mirror Excel):
Default (UC sheets): Environment(environment), SecurableType, Catalog, SchemaName(schema_name), ObjectName(object_name), Owner, PrincipalType, PrincipalName, AccessPrivilege(privilege).
`UC_metastore_RBAC` (exact name): same minus ObjectName.
Sheets starting `WS_`: Environment, PrincipalType, PrincipalName, WorkspaceName(workspace_name), SecurableType, SecurableName(object_name), SecurablePermission(privilege).
Selection logic: `name.startsWith('WS_')` -> WS; `=== 'UC_metastore_RBAC'` -> metastore; else UC. Theme colours: WS green, metastore amber, UC blue.
CamelCase header gotcha: headers are rendered as `<td role="columnheader">` inside `<thead>` (not `<th>`) and a `useEffect` with no deps runs after every render:
```ts
theadRef.current.querySelectorAll('td,th').forEach((el) => {
(el as HTMLElement).style.setProperty('text-transform', 'none', 'important');
});
```
plus the CSS rule above. Purpose: some global CSS uppercases table headers; this keeps `SecurableType` etc. un-uppercased. (A reimplementation could just avoid `th` or use the CSS rule.) Note the same `theadRef` is also attached to the diff-modal's `<thead>` (the ref is reused; last mounted wins, effectively harmless).
State and editing model:
`stagedEdits: Map<string, StagedEdit>` keyed `"${rowId}:${column}"`, value `{id,row_version,column,new_value,old_value}`.
Editable cells are uncontrolled `<input defaultValue>` with `key={`${row.id}-${row.row_version}-${col.key}`}` (re-mount after reload when row_version bumps); edit staged on `onBlur`: `stageEdit(row, col.key, e.target.value || null)` (empty string becomes `null`). If new value === original, the staged entry is deleted (un-edit).
Cell display value: `getCellValue` returns staged `new_value` if staged. Edited cells highlighted `#fef3c7` with amber inset border.
Editability: `perms.can_edit && perms.editable_columns.includes(col)`; non-editable columns show a Lock icon in the header when the user can_edit.
No optimistic update: commit sends all staged changes, then on success: message `Committed N change(s).`, `setStagedEdits(new Map())`, close modal, `await loadData()`, clear status after 3 s (5 s for error). Errors show the raw `resp.text()`. Conflict handling is purely server-side via `row_version` optimistic locking - the client sends `row_version` per change and shows whatever error text the server returns (no special 409 UI, no per-row conflict resolution, staged edits are retained on error).
"Review Changes" modal lists Row `#id`, Column, Old (red strikethrough), New (green); buttons "Discard All" (clears staged, closes, reloads) and "Commit N change(s)".
Auto-refresh: `setInterval(loadData, (perms.refresh_seconds ?? 30)1000)` but skipped while `stagedEdits.size > 0` (do not clobber pending edits). Switching sheet tabs clears staged edits, search and filters (silently discards edits).
Note: `loadData` sets `loading=true` on each refresh; no spinner replacement of table, only the Refresh icon spins.
Filtering: global search box (case-insensitive substring over the visible columns of the sheet; input turns yellow when active). Per-column Excel-style filter dropdown (`ColumnFilterDropdown`): value list from `uniqueValuesPerColumn` (sorted `localeCompare`, blank shown as `(blank)`), search-in-values, Select All, Apply/Cancel/Clear; filter stored as `Record<col, Set<string>>`; if all values selected on Apply the filter is cleared (`onApply(column, null)`). Null/undefined cell values are matched as `''`. Footer shows `"X of Y rows (filtered)"`. "Clear All Filters (n)" button.
Insert Row modal (Platform Admin only, `can_edit`): opens with `{sheet_name: selectedSheet}`; renders the sheet's columns. Validation rules (`COLUMN_RULES`):
```ts
environment: { required: true, strictDropdown: true },
securable_type: { strictDropdown: true },
catalog/schema_name: { minLen: 2, maxLen: 255, pattern: /^[a-zA-Z][a-zA-Z0-9_]([._-][a-zA-Z0-9_]+)$/ },
object_name: { minLen: 2, maxLen: 512, pattern: /^[a-zA-Z][a-zA-Z0-9_]([._-/\s][a-zA-Z0-9_]+)$/ },
owner: { minLen: 5, maxLen: 255, pattern: email-or-group /^[a-zA-Z0-9][a-zA-Z0-9_.-]@[a-zA-Z0-9_.-]+$|^[a-zA-Z][a-zA-Z0-9_.@-]{4,}$/ },
principal_type: { required: true, strictDropdown: true },
principal_name: { required: true, minLen: 5, maxLen: 512, same email-or-group pattern },
privilege: { required: true, strictDropdown: true },
workspace_name: { minLen: 3, maxLen: 255, pattern: /^[a-zA-Z][a-zA-Z0-9_]([._-\s][a-zA-Z0-9_]+)$/ },
```
`strictDropdown` columns (environment, securable_type, principal_type, privilege) use `StrictDropdown` (searchable select; no free typing); allowed values are the unique values already present in the loaded sheet's data (blank excluded) - so a brand new environment/privilege cannot be added from the UI. `validateField`: required -> blank skip -> strict-dropdown membership ("Must select a value from the dropdown list") -> minLen/maxLen -> pattern (pattern skipped for strictDropdown). Live validation on change; errors shown only for touched fields; Insert button disabled when any error exists or `!addRowData.environment`. On success shows server `message`, reloads, auto-closes after 2 s.
Quirk: on the initial open no fields are validated, so Required errors for untouched fields only appear on submit (submit re-validates everything and marks failing fields touched). `maxLength={rule?.maxLen || 512}`.
Source ends with `// cache-bust v2` comment (they had stale-bundle issues).
1.5 RbacBundles.tsx (routes `/bundles`, `/bundles/:bundleId`)
Two sub-views in one page, `selectedBundle` state seeded from `useParams().bundleId` and synced via effect; selecting navigates to `/bundles/${encodeURIComponent(id)}`, Back navigates to `/bundles`. Header copy: "Each bundle groups requests submitted in the same hour." (i.e. bundle_id is time-bucketed by the backend.)
BundleList: `getRbacBundles()` once on mount (no polling), manual refresh button; search over `bundle_id` and `status`; sorted by `created_at` desc client-side; table columns Bundle ID, Requests (count), Environments (colour badges dev=emerald, tst=amber, stg=orange, prd=red, uppercased), Status badge, Created (`formatDistanceToNow`). Empty state: "Submit an RBAC request through the Agent chat and it will appear here as a bundle."
BundleDetail: `getRbacBundleDetail`, auto-poll every 8 s (`setInterval(load, 8000)`; note `load` sets `loading=true` but the spinner only shows when `!detail`). Header summary counts string: `N requests · X pending · Y approved · Z denied · W exported`.
Buttons are state-derived: `hasPending = summary.pending_approval > 0` shows "Approve All" and "Reject"; `hasApproved = summary.approved > 0` shows "Export CSVs".
Approve: `window.confirm('Approve all pending requests in this bundle?')` -> `approveRbacBundle` -> reload.
Reject: toggles textarea; empty reason -> `alert('Please provide a rejection reason.')`; then `rejectRbacBundle(id, note)`.
Export: no confirm; result stored in `exportResult` and rendered as per-env cards: success `"${res.records} record(s) → ${res.file}"`, failure `Failed: ${res.error}`; green/red by `res.status === 'success'`. Then reload.
All three share `actionLoading: 'approve'|'reject'|'export'|null` to disable buttons.
Table columns: Environment (`req.environment || ctx.environment`), Privilege (mono), Securable (type + name from `state_context`), Principal (type + name), Status, Flags (purple "Escalation" chip when `ctx.requires_security_escalation`, tooltip `Escalated: <escalated_privileges>`; `denial_reason` truncated to 40 chars with tooltip), Created.
Status styles: pending_approval yellow, approved green, denied red, exported blue, export_failed red, pending gray. Status text rendered with `_` -> space.
1.6 Approvals.tsx (diff)
New state: `rbacPendingBundles`, `expandedRbacBundle`, `rbacBundleDetail`, `rbacBundleLoading`, `rbacActionLoading` (holds the bundleId), `rbacRejectReason`, `rbacRejectBundleId`.
`loadRbacBundles` (`getPendingRbacBundles`) is added into `fetchAllData` (`Promise.all`) so it refreshes with all the page's other loads; errors only `console.error`.
Accordion: click row -> `toggleRbacBundle` (collapse if same; else fetch `getRbacBundleDetail`). Guard against stale detail: renders detail only when `rbacBundleDetail.bundle_id === bundle.bundle_id`.
Each request line: `ENV — principal_name`, `securable_type: securable_name — privilege`, status chip.
Approve: no confirm; `approveRbacBundle` -> `loadRbacBundles()` -> collapse. Reject: first click reveals textarea (`rbacRejectBundleId`), Confirm Reject requires non-empty reason (`alert('Please provide a rejection reason.')`), then `rejectRbacBundle`, reload, clear state. Errors use `alert('Failed to approve: ' + message)`.
Empty-page card condition now also requires `rbacPendingBundles.length === 0`.
Section header: `RBAC Bundles Pending Approval (n)`; whole section hidden when none. Note the list from the API is already filtered to bundles the caller can approve (server-side).
1.7 Requests.tsx (diff)
Filter default changed from `'pending'` to `'all'`; new `'all'` tab. Status buckets now include RBAC statuses:
pending: NOT in `['completed','exported','rejected','denied','failed','export_failed']`
completed: `['completed','exported']`
failed: `['rejected','denied','failed','export_failed']`
Search box filters by id, title, `type.replace(/_/g,' ')`.
RBAC bundles section (above the table): `getRbacBundles()` on mount; bundle filter mapping: pending -> `['pending_approval','approved']`; completed -> `['exported','completed']`; failed -> `['rejected','denied','failed','export_failed']`; search matches `bundle_id`, `status`, environments. Section hidden if filtered list empty. Accordion expand loads `getRbacBundleDetail` (uses single `bundleDetail` state; collapse resets). Each child request is expected to deep link to `/requests/:id` (comment: "RBAC requests opened via deep-link from a bundle drill-down").
Direct fetch: if `selectedRequestId` is not in the store list (and store not loading, nothing fetched yet) it calls `getRequest(id)` into `directFetchedRequest`; cleared when `selectedRequestId` becomes falsy. `selectedRequest = storeMatch || directFetchedRequest`. Not-found screen shown only when `requestId && !selectedRequest && !isLoading && !directFetchLoading`. Empty state is shown only if no requests AND no bundles. Loading screen only if `isLoading && requests.length===0 && rbacBundles.length===0`.
Synthetic RBAC timeline - RBAC mutation requests bypass the state machine:
```ts
const isRbacType = request.type === 'rbac_uc_grant' || request.type === 'rbac_uc_revoke';
// steps: submitted (always completed; fact request_created {principal, securable, privilege, environment})
// pending_approval "Awaiting Platform Admin Approval" (type 'platform_admin')
// active if st==='pending_approval'; completed if in [approved, exported, completed]; 'rejected' if st==='denied'; else pending
// exported "Approved & Exported to RBAC Workbook"
// completed if [exported, completed]; active if st==='approved'; else pending
// fact export_completed {export_path: meta.export_path, environment} when exported/completed and meta.export_path
// if st === 'denied' -> steps = rbacSteps.slice(0, 2)
```
The normal "linear states" branch is skipped for these (`!isRbacType && request.stateMachine.states...`), and `resultSummary` is propagated from states (`resultSummary: state.resultSummary`, `undefined` for synthetic ones). Only the two `rbac_uc_` types get this; other `rbac_` workflow types use the normal state machine.
New fact renderers (by `fact.type`): `privilege_validated` (shows `data.normalized_token || data.privilege || 'validated'`), `plan_computed` ("Dry run plan ready"), `grant_applied` ("Applied to workbook"), `changes_validated` ("Validation complete"), `workbook_committed` ("Committed"), `export_completed` ("Exported to <export_path>"), `request_created` (`priv on securable -> principal (env)`), `prerequisites_resolved` (`N prereq(s)` or `resolved`).
"Step Result" box for completed or failed steps with `step.resultSummary`: shows `summary` then `details` flattened: skips null/undefined/empty arrays; nested plain objects render as key/value sub-rows (skipping null/undefined/'' values; hides object if all empty); arrays and scalars rendered as separate branches.
1.8 RbacRequest.tsx (dead)
Chat intake page: 9 quick-action cards (`grant, revoke, workspace, entitlement, group, ownership, role, bulk, drift`) each with a canned prompt (e.g. grant: "I need to grant Unity Catalog access to a principal. Help me set up the grant."; drift: "Run a drift detection to compare the RBAC workbook state against live Databricks state."). Uses `<ChatView ref mode="unified" storageKey="chatview_messages_rbac" welcomeNode placeholder samplePrompts>`; click calls `chatRef.current.submitQuery(prompt)` via `ChatViewHandle`. "View my requests ->" navigates to `/requests`. Example prompts hint at agent capabilities (grant SELECT, revoke MODIFY from service principal, drift for production).
1.9 RbacDriftReport.tsx (dead)
Loads `api.getRbacDrift()` + `api.getRbacDriftSummary()` in parallel (`records ?? []`). Scan: `triggerRbacDriftScan()` then `setInterval(3000)` polling summary until `!scan_in_progress`, then reload. Gotcha: the poll interval is a local variable never cleared on unmount, and a poll exception is unhandled (scanning would hang if summary errors). Filters: drift type (all/extra/missing/mismatch), environment (derived from records), search text (securable_name etc., case-insensitive), sortable columns (default `detected_at` desc). Summary tiles use `summary?.extra ?? count from records`. Shows "Last scan: <locale string>". Note data shape (`drift_type: extra|missing|mismatch`, workbook_value/live_value) differs from the `rbac_detect_drift` tool output (`extra_grants`/`missing_grants` arrays, `drift_type: "extra_grant"`), so the backend `/rbac/drift` endpoint must translate.
1.10 RbacStatusCard.tsx (unused)
Horizontal pipeline of stages `validate_request, manager_approval, data_owner_approval, opa_policy_check, excel_mutate, verify_effective_access` (labels Validate/Manager/Owner/Policy/Mutate/Verify). Stage status from `request.stateMachine.completedStates` / `activeStates`; failed if request failed/rejected and `currentState === stageId`. Progress % = completed/6. Polls `api.getRequest(request.id)` every `pollMs` (default 5000) until terminal (`completed|rejected|failed`); poll errors silently swallowed. Only treats `type.startsWith('rbac_')` as RBAC; footer shows `metadata.securable_name/privilege/principal_name`. These stage ids do NOT match the actual workflow stage names in the JSON specs (which are e.g. `validate_request`, `resolve_prerequisites`, manager/data_owner gates, `apply_grant`, `commit_workbook`), so it is stale.
1.11 Workflow graph components
`RequestGraphView`: passes `nodeDetails={graph.node_details}`.
`WorkflowGraphPreview`: new optional prop `nodeDetails?: Record<id,{resolved_args?, description?}>`; `PreviewNodeData.detail = nodeDetails?.[n.id]?.description ?? undefined`; renders `<div className="text-[9px] text-indigo-600 truncate ..." title={detail}>`; node width 180px -> 220px. Example description (from type comment): "GRANT SELECT on catalog.schema for group". `resolved_args` is typed but not rendered.
`WorkflowEditor`: stage kinds `gate`, `conditional_gate`, `merge_gate` all route to `GateForm` (cast `as WorkflowGateStage`); other kinds cast `as WorkflowStepStage`. (The GateForm does not expose the new kinds' specific fields.)
2. Backend tests (spec of expected behavior)
Run: `cd backend && source venv/bin/activate && pytest tests/test_rbac_.py -v`. Tests import modules lazily inside test bodies, and several patch `app.core.config.settings` (which is NOT the same object the tool module imported if the module did `from app.core.config import settings`; only some tests patch module-local `settings`). Tool modules live in `app/tools/self_service/`.
2.1 test_rbac_discovery_reader.py (`app.tools.self_service.rbac_discovery_reader`)
Fixtures: `patch_estate(tmp_path)` returns a writer for a tmp `estate.yaml` (settings patched in each test via `patch("app.tools.self_service.rbac_discovery_reader.settings")`); `mock_warehouse` patches `rbac_discovery_reader.WorkspaceClient` with autospec and sets `instance.warehouses.list.return_value = [SimpleNamespace(id="wh-123", enable_serverless_compute=True, state=SimpleNamespace(value="RUNNING"))]`. SQL responses built by `_make_sql_response(columns, rows)` -> `SimpleNamespace(manifest.schema.columns[name], result.data_array)`; empty = `data_array=None`.
Estate YAML shape used: `estate:{cloud, account_id:auto, provider_version, privilege_model_version, identity_federation, workspace_entitlement_mode, idp_type, provisioning_mode, git_provider, terraform_runner, verification_workspace, default_timezone}`, `discovery:{catalog, schema, max_staleness_minutes}`, `policy:{system_groups, admin_group_patterns, role_group_prefix, bulk_threshold}`, `workbook:{file_path, sheets}`.
Cases:
Config resolution: returns `catalog`, `schema`, `max_staleness_minutes==240`, and backtick-quoted table names:
`config["grants_table"] == "`enterprise_dev`.`audit`.rbac_discovered_grants"` and `snapshots_table == "`enterprise_dev`.`audit`.rbac_discovery_snapshots"`. Missing `discovery` section or `catalog: null` -> `ValueError` matching `discovery.catalog.not set`; missing estate file -> `ValueError` "Estate config not found".
Freshness: result keys `is_fresh`, `last_snapshot_id` (e.g. `run_20260830_100000`), `warning`, `age_minutes`. Fresh -> `is_fresh True`, `warning None`, `age_minutes < 240`. Stale -> `is_fresh False`, warning contains "stale" or "minutes old". No snapshots -> `is_fresh False`, `last_snapshot_id None`, warning "no successful discovery snapshots".
`get_current_grants` with filters: `{success True, total, source:"delta", grants:[{securable_full_name,...}]}`; empty -> success, total 0; SQL exception -> `success False` and `"Connection refused" in result["error"]`.
`get_grants_for_securable`: shape `{success, source:"delta", total, grants[{principal, privilege, inherited_from_type, inherited_from_name}]}`.
`check_grant_exists`: `{success, grant_exists, matching_count}`; bulk securables: only matching one counted (`matching_count == 1 # only t1 matched`).
`get_grant_history`: `{success, total, active, removed, source:"delta"}`.
Warehouse resolution: env var takes precedence (`_get_warehouse_id() == "env-wh-999"`), fallback to settings (`"settings-wh-222"`).
`_extract_rows`: rows to dicts by column name; empty response and `data_array=None` -> `[]`.
2.2 test_rbac_drift_detection.py (`rbac_detect_drift`, async)
Fixture workbook: sheet `UC_Table_View` header `["Environment","Object Type","Object Name","Principal Type","Principal","Privilege","Owner"]` rows dev: table_a/team_alpha/SELECT, table_b/team_alpha/SELECT, table_c/team_beta/MODIFY (all `cat.schema.<t>`). Patches: `app.core.config.settings` (RBAC_WORKBOOK_PATH), `rbac_detect_drift.rbac_read_estate_config` -> `{"valid": True, "estate": {"cloud":"azure"}}`, `rbac_detect_drift.rbac_get_effective_access` (async side_effect `(securable_type, securable_name, kwargs) -> {"success": True, "grants":[{"principal","privilege","inherited_from_name"}]}`).
Extra: live has additional `rogue_team SELECT` on table_a -> `summary.extra_grants_count == 1`, `extra_grants[0].principal == "rogue_team"`, `extra_grants[0].drift_type == "extra_grant"` (called `environment="dev", include_extra_grants=True, include_missing_grants=False`).
Missing: live lacks table_b -> `summary.missing_grants_count == 1`, `missing_grants[0].securable_name == "cat.schema.table_b"`.
In sync: `summary.has_drift False`, both counts 0.
Filter: `securable_name="cat.schema.table_a"` -> `summary.securables_checked == 1`, `filters_applied.securable_name` echoed.
API failure (`side_effect = Exception("API timeout")`): `success True` and `len(errors) > 0` (errors collected, not raised).
2.3 test_rbac_opa_policies.py
Important: these tests never call OPA/Rego. Each test builds an input dict with helper builders and asserts the dict's own fields (they document the input contract, "mock mode"). Input contracts:
`rbac_grants.rego` (package `rbac.grants`): `{request:{privilege, securable_type, securable_name, principal_type, principal_name, environment, is_administrative, inherits_to_children, has_expiry}, estate:{cloud, provider_version}}`.
`rbac_blast_radius.rego`: `{request:{privilege, securable_type, securable_name, inherits_to_children, environment, securable_names[]}}`.
`rbac_entitlements.rego`: `{request:{privilege, securable_type:"workspace", securable_name:"ws-12345", principal_name, environment}, estate:{workspace_entitlement_modes:{<securable_name>: mode}}}`.
Scenarios named (the intended policy outcomes): hard_deny MANAGE on catalog (administrative), hard_deny ALL_PRIVILEGES, allow table SELECT, warn on no expiry (`has_expiry False`), extra_approval for schema-level SELECT (inherits); blast radius: schema SELECT widening, catalog non-USE_CATALOG, bulk 15 securables over threshold (policy `bulk_threshold: 10` in estate), `environment == "all"` cross-workspace, table level no blast radius; entitlements: hard_deny system group `users` on workspace, hard_deny `admins`, hard_deny admin entitlement to non-admin group, extra_approval `CAN_MANAGE`, warn `workspace_access` in legacy mode, warn `cluster_create` in `prd`, allow normal entitlement, hard_deny group membership into system group `admins`. Policy tiers are: `hard_deny`, `extra_approval`, `warn`, `allow`.
2.4 test_rbac_prerequisite_chain.py (`rbac_resolve_prerequisites`)
Fixtures: tmp registry dir with `securables.yaml` (SAMPLE_SECURABLES_YAML with per-privilege `prerequisites: [...]`, e.g. `WRITE_FILES: prerequisites: [READ_FILES]`), `settings` patched at `app.tools.self_service.rbac_resolve_prerequisites.settings`, `RbacWorkbookParser` patched there (so "already existing" checks use a mock parser). Assertions on a set of privilege tokens (`priv_tokens`):
SELECT on table -> contains `USE_CATALOG`, `USE_SCHEMA`; MODIFY same.
Parent names: catalog prereq `securable_name == "enterprise"`, schema prereq `== "enterprise.gold"` (from FQN `catalog.schema.table`).
WRITE_VOLUME -> `READ_VOLUME`, `USE_SCHEMA`, `USE_CATALOG`; READ_VOLUME -> only parent chain (`READ_VOLUME` not in prereqs).
WRITE_FILES on external location -> `READ_FILES`.
USE_CATALOG on catalog -> `len(prereqs) == 0`.
CREATE_TABLE on schema -> `USE_SCHEMA` and `USE_CATALOG`.
Dedup: `assert len(keys) == len(set(keys)), f"Duplicate prerequisites found: {keys}"`.
(Header docstring also promises "already-existing prerequisites marked correctly"; I did not see a dedicated test for it.)
2.5 test_rbac_privilege_validation.py (`rbac_validate_privilege`, sync)
Fixtures: tmp registry with `privileges.yaml` (per securable type allowlists: table [SELECT, MODIFY, ALL_PRIVILEGES, APPLY_TAG]; schema [USE_SCHEMA, CREATE_TABLE, CREATE_VOLUME, CREATE_FUNCTION, ALL_PRIVILEGES]; catalog [USE_CATALOG, CREATE_SCHEMA, ALL_PRIVILEGES]; volume [READ_VOLUME, WRITE_VOLUME]; external_location [READ_FILES, WRITE_FILES, CREATE_EXTERNAL_TABLE]) and `providers.yaml` (`provider_version "1.52.0"`, `normalization: {READ: SELECT, WRITE: MODIFY, ALL: ALL_PRIVILEGES, USAGE: USE_SCHEMA}`, `deprecated_tokens: {USAGE: null, READ: SELECT, WRITE: MODIFY}`); patch `rbac_validate_privilege.settings.RBAC_REGISTRY_PATH`.
Signature: `rbac_validate_privilege(privilege=, securable_type=, include_prerequisites=bool)`.
Valid: `valid True`, `normalized_privilege == "SELECT"` (also lowercase input).
Invalid: nonsense token, privilege wrong for securable type, `WRITE_VOLUME` on table -> `valid False`.
Deprecated: `USAGE` on schema -> `deprecated True`; `READ` on table -> `normalized_privilege == "SELECT"` and `deprecated True`; `WRITE` -> `MODIFY`.
Prerequisites: with flag -> `valid True`, `prerequisites` is a list; without -> `[] or key absent`.
Gotcha: this test asserts the result key `normalized_privilege`, whereas the mutation tests mock `rbac_validate_privilege` returning `normalized_token` (and the frontend fact reads `normalized_token`). Both key names should exist or the real implementation must satisfy whichever consumer is used; verify against the actual tool.
2.6 test_rbac_workbook_mutation.py (`rbac_mutate_workbook`, sync)
Fixture workbook sheets (header `Environment, Object Type, Object Name, Principal Type, Principal, Privilege, Owner`): `UC_Catalog` (enterprise_dev/data_engineers/USE_CATALOG), `UC_Schema` (enterprise_dev.gold analysts USE_SCHEMA; packed enterprise_dev.silver etl_team `"USE_SCHEMA; CREATE_TABLE"`), `UC_Table_View` (enterprise_dev.gold.fact_sales analysts SELECT; packed principals enterprise_prd.gold.dim_customer `"analysts; ml_team"` SELECT). Packed delimiter is `"; "`.
Patches (module-local): `rbac_mutate_workbook.rbac_read_estate_config` (enabled: `{"valid":True,"mutation_enabled":True,"estate":{cloud,provider_version}}`; disabled: `{"valid":True,"mutation_enabled":False,"validation_errors":[...]}`), `rbac_mutate_workbook.rbac_validate_privilege` (returns `{valid, normalized_token, is_administrative, inherits_to_children, prerequisites}`), `rbac_mutate_workbook._evaluate_opa_policy` (allow: `{"tier":"allow","messages":[]}`; deny: `{"tier":"hard_deny","messages":["MANAGE on catalog is blocked by policy."]}`), and `app.core.config.settings.RBAC_WORKBOOK_PATH`.
Call signature: `rbac_mutate_workbook(action="grant"|"revoke", privilege, securable_type, securable_name, principal_name, principal_type, environment, owner?, justification?, auto_prerequisites, dry_run, request_id?)`.
Add simple grant -> `success True`.
Dry run -> `success True`, `dry_run True`, and file mtime unchanged (`new_mtime == original_mtime`).
Revoke existing -> success.
hard_deny -> `success False` and `"hard_deny" in error.lower() or "BLOCKED" in error`.
Estate disabled -> `success False`, error contains `"DISABLED"` or "mutation".
Invalid `principal_type` / `securable_type` -> `success False` and the error text contains the field name (`principal_type` / `securable_type`).
Packed safety: revoking `ml_team` from `"analysts; ml_team"` -> success (comment: analysts must retain SELECT; not actually asserted); adding `CREATE_VOLUME` to etl_team's packed row -> success (comment: should pack into same row; not asserted). So the spec of intended behavior is in comments only.
2.7 test_rbac_workbook_parser.py (`RbacWorkbookParser`, `parse_workbook`)
Imports models from `app.tools.self_service.rbac_models` (`PrincipalType`, `RbacCanonicalRecord`, `SecurableType`, ...). Fixture workbook sheets: `UC_Catalog` (2), `UC_Schema` (1 + packed 3 privileges), `UC_Table_View` (1 + 6 from packed principals x privileges + 1 view/user), `UC_Volume_ExtLoc` (packed READ_VOLUME; WRITE_VOLUME), `Metastore` (env `all`, CREATE_CATALOG), `WS_RBAC_Dev` (header `Environment, Workspace ID, Principal Type, Principal, Entitlement, Value, Owner`), `WS_RBAC_Prd`, `Sample` (must be skipped).
Expected behaviors:
Simple record: environment `dev`, `securable_type == SecurableType.CATALOG`, `securable_name`, `principal_type == PrincipalType.GROUP`, `privilege "USE_CATALOG"`, `owner`, `is_packed_origin is False`.
Packed privileges: `USE_SCHEMA; CREATE_TABLE; CREATE_VOLUME` -> 3 packed records (4 incl. the plain row), set equality of privileges.
Packed principals AND privileges: `analysts; data_scientists; ml_engineers` x `SELECT; MODIFY` = 6 packed records (cartesian product); sheet gives 8 total.
All UC sheets: 17 records; WS: 6 records (5 from packed entitlements incl. `workspace_access`, plus 1 prd; `entitlement_or_acl == "workspace_access"`); environments `dev`, `prd` inferred.
Securable types resolved: CATALOG, SCHEMA, TABLE, VIEW, VOLUME, METASTORE.
Privileges uppercase and stripped; `identity_key` unique across records.
Errors: missing workbook -> `WorkbookParseError` matching "not found"; missing sheet -> 0 records and `parser.parse_errors` contains "not found"; empty rows skipped.
`parse_workbook` summary: `total_uc_records==17`, `total_ws_records==6`, `total_errors==0`, `packed_uc_records>0`, `unique_principals>0`, `unique_securables>0`; `Sample` sheet never appears as `source_sheet`.
Roundtrip: parse -> serialize -> parse: `original_keys.issubset(new_keys)` and the newly added record's `identity_key` present.
2.8 test_rbac_workflow_e2e.py (static contract tests; despite the docstring, no workflow runtime is executed)
Reads `backend/app/workflows/graphs/catalog/<key>.json`; 9 workflows: `rbac_uc_grant, rbac_uc_revoke, rbac_ws_assignment, rbac_ws_entitlement, rbac_group_membership, rbac_ownership_transfer, rbac_role_assumption, rbac_drift_detection, rbac_bulk_access`.
Fixtures: `fixture_workbook`, `estate_config` (YAML with `account_id: test-account-123`, `mutation_enabled: True`, `role_assumption_enabled: True`, `workspace_entitlement_modes` for ws-12345/ws-67890), `mock_github` (patches `app.tools.self_service.rbac_commit_workbook.GitHubProvider` async context manager with `get_branch_ref -> {"sha":"abc123"}`, `create_branch`, `create_or_update_file -> {"sha":"def456"}`, `create_pull_request -> {"number":42,"html_url":".../pull/42"}`, `request_reviewers`, `enable_auto_merge`), `mock_settings` (RBAC_WORKBOOK_PATH, RBAC_ESTATE_PATH, RBAC_REGISTRY_PATH, RBAC_WORKBOOK_REPO "org/rbac-workbook", RBAC_WORKBOOK_REPO_PATH "workbooks/rbac.xlsx", GITHUB_TOKEN, GITHUB_ORG, GITHUB_WEB_BASE_URL). Config keys here document settings names.
Classes:
Spec loading: JSON has `key == filename`, non-empty `stages`, non-empty `complete_fact`; every non-drift workflow's last stage is `name=="commit_workbook"`, `tool=="rbac_commit_workbook"`; every stage has `kind` and `name`; `step` needs `tool` and `args` (except `commit_workbook`); `gate` needs `type`.
Variable binding: reference resolver for `{"$var": "x"}` (context lookup, missing -> None) and `{"$coalesce": [...]}` (first non-None, recursive). Every `$var` name must satisfy `var.replace(".", "").replace("_", "").isalnum()`.
Execution/structure (hard-coded stage orders):
`rbac_uc_grant`: stages[0] `validate_request`/`rbac_validate_privilege`; [1] `resolve_prerequisites`/`rbac_resolve_prerequisites`; [2] gate type `manager`; [3] gate type `data_owner`; [4] `apply_grant`/`rbac_mutate_workbook` with `approvals == ["manager","data_owner"]`; [5] `commit_workbook`/`rbac_commit_workbook`.
`rbac_uc_revoke`: first stage `check_existing_grant`/`rbac_read_workbook`.
`rbac_ownership_transfer`: includes stage names `check_pipeline_restriction` and `platform_admin_approval`.
`rbac_role_assumption`: first `check_feature_gate`/`rbac_read_estate_config`.
`rbac_drift_detection`: zero gates; last tool != `rbac_commit_workbook`.
`rbac_bulk_access`: first `expand_package`/`rbac_list_securables`.
`rbac_group_membership`: includes `resolve_principal` and `validate_group`.
`rbac_ws_entitlement`: first `check_entitlement_mode`/`rbac_get_workspace_entitlement_mode`.
Approval gates (ordered gate `type` lists): uc_grant [manager, data_owner]; uc_revoke [manager, data_owner]; ws_assignment [manager]; ws_entitlement [manager, workspace_admin]; group_membership [manager, group_owner]; ownership_transfer [data_owner, platform_admin]; role_assumption [manager, role_owner]; drift_detection []; bulk_access [manager, data_owner]. Every `rbac_mutate_workbook` step must declare non-empty `approvals`.
(Note: these assert a gate list built from `kind == "gate"`; newer `conditional_gate`/`merge_gate` kinds added to the frontend types would not be counted, so if workflow specs were changed to those kinds, this test would break.)
Agent instructions: `backend/app/agents/instructions/<key>.md` must exist, contain `Goal` or `## Goal` or `# RBAC`; non-drift ones mention "justification" (case-insensitive).
Tool registration: 13 modules must exist under `app/tools/self_service/`: `rbac_validate_privilege, rbac_resolve_prerequisites, rbac_mutate_workbook, rbac_commit_workbook, rbac_read_workbook, rbac_read_estate_config, rbac_resolve_principal, rbac_get_workspace_entitlement_mode, rbac_check_existing_grants, rbac_list_securables, rbac_get_effective_access, rbac_detect_drift, rbac_compensate_workbook`; and every `step` tool referenced in any workflow must have an `rbac_.py` module.
OPA files: `backend/policies/rbac_grants.rego` (`package rbac.grants`), `rbac_blast_radius.rego` (`package rbac.blast_radius`), `rbac_entitlements.rego` (`package rbac.entitlements`).
Estate/registry files exist: `<repo>/rbac/estate.yaml` and `<repo>/rbac/registry/{privileges,securables,providers}.yaml` (path computed as `Path(__file__).parent.parent.parent / "rbac"`, i.e. repo root `rbac/`).
Golden transcripts error scenarios from `backend/app/workflows/golden_transcripts.json` (dynamic parametrization `workflow::scenario`; empty list if file missing): each scenario must have `expected_status` in `{rejected, failed, partial, awaiting_confirmation, awaiting_extra_approval}` and at least one of `message_contains | error_contains | warning_contains`. Scenarios with `blocked_by` starting `rbac_grants.rego` must use format `"file:rule"`, with the file under `backend/policies/` and the rule name appearing in the file text. Scenarios with a `gates` count must exceed the happy-path `transcripts[workflow]["gates"]`. `rejected` scenarios require a `rbac_mutate_workbook` stage with at least one `step` before it. `failed` scenarios with `input` must have a non-empty input dict. `partial`/`awaiting_confirmation` require the workflow to have gates or stages with `condition`.
2.9 integration/test_rbac_integration.py (LIVE)
Whole module skipped unless `os.environ["TEST_MODE"] == "real"` (docstring says `pytest --mode=real` but the skip checks the env var `TEST_MODE`, so the `--mode` flag alone would not enable it unless conftest sets it). Env: `RBAC_TEST_CATALOG` (default `integration_test_catalog`), `RBAC_TEST_SCHEMA` (`test_schema`), `RBAC_TEST_TABLE` (`test_table`), `RBAC_TEST_GROUP` (`rbac-integration-test-group`), `RBAC_TEST_WORKSPACE_ID`, `RBAC_TEST_USER`; needs `DATABRICKS_HOST`/token and a real `rbac/estate.yaml` (`estate_config` fixture asserts `mutation_enabled`). `workbook_path` fixture copies `settings.RBAC_WORKBOOK_PATH` to tmp (skips if absent).
Tests:
`rbac_resolve_principal(principal_name, principal_type, reject_system_groups=)` (async): existing group -> `success True, principal_type "group", exists True, "source" in result`; nonexistent user -> `success False` or `exists False`; `admins` with `reject_system_groups=True` -> `is_system_group True` or `success False`.
`rbac_get_effective_access(securable_type, securable_name, principal_name=)` (async): catalog -> `success` and `"permissions" in result or "grants" in result`; table filtered by principal -> success; nonexistent -> `success False` with `"error"`.
`rbac_get_workspace_entitlement_mode(workspace_id)` -> `success`, `mode in ("legacy","explicit")` (skipped without workspace id).
`rbac_detect_drift(securable_name=, securable_type="catalog")` -> success, `summary`, and `extra_grants` or `drift_items`; `workspace_id="ws-99999999999"` -> `"error" in result or partial is True`.
Mutation end-to-end: `rbac_mutate_workbook(action="grant", ..., owner="integration-test", justification=..., request_id="integration-test-001", auto_prerequisites=True, dry_run=False)` called synchronously here, `success True` and `records_added >= 1`; then `await rbac_compensate_workbook(original_action="grant", ..., original_request_id=, reason=, dry_run=False)` -> `success True`. Dry-run test hashes workbook SHA-256 before/after and asserts identical, `result["dry_run"] True`.
Inconsistency to note: unit tests call `rbac_mutate_workbook` synchronously and `rbac_detect_drift`/`rbac_get_effective_access`/`rbac_resolve_principal`/`rbac_compensate_workbook` with `await`. `rbac_validate_privilege`, `rbac_resolve_prerequisites` (tests call sync). The reimplementation must keep these sync/async signatures.
3. Cross-cutting gotchas
The grid page bypasses `api.ts` (raw fetch, hard-coded `/api/v1`), which would break if `API_BASE_URL` differs; bundle/drift pages use `api.ts`.
Backend list endpoints return wrapper objects (`{total,bundles}`, `{records,total}`) except grid `data`/`sheets`, which return bare arrays.
Bundle ids are hour-bucketed groups of requests; bundle statuses seen in UI: `pending_approval, approved, denied, rejected, exported, completed, export_failed, failed, pending`. Request-level uses `denied` while the bundle reject API is `/reject` (UI treats `denied` and `rejected` both as failure).
`rbac_uc_grant`/`rbac_uc_revoke` request types bypass the state machine in the UI timeline; flow is submitted -> pending_approval (Platform Admin) -> approved -> exported (workbook CSV export, `metadata.export_path`).
Dead code: RbacRequest, RbacDriftReport, RbacStatusCard are not wired up; the `/rbac` routes redirect to `/` (chat).
OPA unit tests do not exercise Rego; only the e2e suite verifies the .rego files exist and declare packages and that golden-transcript `blocked_by: file.rego:rule` references resolve textually.
Tests have several comment-only expectations (packed-cell preservation) with no assertion.

