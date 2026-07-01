# Databricks notebook source
# LMWS / FWS-API Group Management Job
#
# Manages Qualcomm LMWS/FWS-API groups via parameterized job runs. This notebook
# is the "engine" behind `app.providers.lmws.client.LmwsProvider`: the app uploads
# and submits it as a one-time job (classic compute), passing the action and its
# arguments as notebook widgets, and reads the JSON result back via
# `dbutils.notebook.exit(...)`.
#
# ---------------------------------------------------------------------------
# Prerequisites (per the LMWS service docs):
#   * Databricks secret scope with the service-account PASSWORD (key
#     `edhapisvc`); the username is the service account itself.
#   * The four LMWS/FWS-API base URLs, passed in as widgets by the app
#     (LmwsProvider.build_parameters) from config/databricks.yml — never
#     hardcoded here.
#   * Classic compute cluster (any size — purely API calls, no Spark).
#   * Service account must be a list-supervisor of target lists for membership ops.
#
# NOTE: TLS verification is disabled (verify=False) to match the production
# client against the internal Qualcomm gateway CA. HTTP-level failures raise
# (via raise_for_status) and surface as a failed job; the app treats a non-JSON
# or {"Result": "FAILED"/"ERROR"} body as failure (see LmwsProvider.parse_output).

import json
import sys

import requests
import urllib3
from requests.auth import HTTPBasicAuth

# The gateway uses an internal CA and the client calls with verify=False;
# silence the resulting per-request InsecureRequestWarning noise.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Parameters (mirror the documented LMWS parameter table)
# ---------------------------------------------------------------------------
dbutils.widgets.text("action", "list_retrieve")          # operation to perform
dbutils.widgets.text("list_name", "")                     # target list name
dbutils.widgets.text("members", "")                       # comma-separated CNs
dbutils.widgets.text("justification", "Automated via Databricks job")
dbutils.widgets.text("owner", "")                         # owner CN (real person)
dbutils.widgets.text("supervisors", "")                   # comma-separated supervisor CNs (min 2 distinct)
dbutils.widgets.text("description", "")                   # group description
dbutils.widgets.text("clone_source", "qcc.dsf.eccn.reference")  # clone source for createSPGroup
dbutils.widgets.text("request_id", "")                    # request id from a prior createSPGroup
dbutils.widgets.text("spac_policies", "")                 # comma-separated SPAC policy names
dbutils.widgets.text("request_type", "ADD")               # ADD | REMOVE (process_spac_policy)
dbutils.widgets.text("cci_classification", "1")           # CCI classification (create_sp_group)
dbutils.widgets.text("qc_list_types", "qgroup,email")     # list types (list_create_new)
dbutils.widgets.text("requester", "")                     # requester CN (defaults to owner, then service acct)
dbutils.widgets.text("secret_scope", "lmws")              # Databricks secret scope for creds
# LMWS / FWS-API base URLs — passed in by the app (LmwsProvider.build_parameters),
# sourced from config/databricks.yml so they are never hardcoded in this notebook.
dbutils.widgets.text("authn_url", "")                     # LMWS authn base URL
dbutils.widgets.text("rest_url", "")                      # LMWS REST (publicAPIrest) base URL
dbutils.widgets.text("cache_url", "")                     # LMWS list cache info base URL
dbutils.widgets.text("fws_url", "")                       # FWS-API entitlement base URL

action = dbutils.widgets.get("action").strip()
list_name = dbutils.widgets.get("list_name").strip()
members = [m.strip() for m in dbutils.widgets.get("members").split(",") if m.strip()]
justification = dbutils.widgets.get("justification").strip()
owner = dbutils.widgets.get("owner").strip()
supervisors = [s.strip() for s in dbutils.widgets.get("supervisors").split(",") if s.strip()]
description = dbutils.widgets.get("description").strip()
clone_source = dbutils.widgets.get("clone_source").strip()
request_id = dbutils.widgets.get("request_id").strip()
spac_policies = [p.strip() for p in dbutils.widgets.get("spac_policies").split(",") if p.strip()]
request_type = dbutils.widgets.get("request_type").strip().upper() or "ADD"
cci_classification = dbutils.widgets.get("cci_classification").strip() or "1"
qc_list_types = [t.strip() for t in dbutils.widgets.get("qc_list_types").split(",") if t.strip()]
# Requester CN for FWS-API calls: explicit widget, else the list owner, else the
# service account (finalized below once SERVICE_USERNAME is known).
requester = dbutils.widgets.get("requester").strip() or owner
secret_scope = dbutils.widgets.get("secret_scope").strip() or "lmws"

# Base URLs for the pasted FWS-API client. Names match the production client's
# module constants so the real logic can be dropped in unchanged.
BASE_LMWS_AUTHN = dbutils.widgets.get("authn_url").strip()
BASE_LMWS_REST = dbutils.widgets.get("rest_url").strip()
BASE_LMWS_CACHE = dbutils.widgets.get("cache_url").strip()
BASE_FWS = dbutils.widgets.get("fws_url").strip()

print(f"LMWS action={action} list_name={list_name} members={members} request_id={request_id}")

# ---------------------------------------------------------------------------
# Authentication — service-account creds
# ---------------------------------------------------------------------------
# Only the PASSWORD is stored in the Databricks secret scope (a single secret).
# The username is NOT stored as a secret, so it's hardcoded here. Update both
# constants if the LMWS/FWS-API service account changes (the secret key is named
# after the service account in the control-tower scope).
SERVICE_USERNAME = "edhapisvc"        # LMWS/FWS-API service-account username
PASSWORD_SECRET_KEY = "edhapisvc"     # key (in `secret_scope`) holding its password

try:
    SERVICE_PASSWORD = dbutils.secrets.get(scope=secret_scope, key=PASSWORD_SECRET_KEY)
except Exception as e:
    raise RuntimeError(
        f"Unable to read LMWS service-account password from secret scope "
        f"'{secret_scope}' (key: '{PASSWORD_SECRET_KEY}'): {e}"
    )

# Finalize the requester now that the service account is known.
requester = requester or SERVICE_USERNAME

# Normalized CSV forms for the API (the notebook parsed these into lists).
members_csv = ",".join(members)
supervisors_csv = ",".join(supervisors)


# ---------------------------------------------------------------------------
# FWS-API / LMWS HTTP client (basic-auth against the Qualcomm gateway)
# ---------------------------------------------------------------------------
_AUTH = HTTPBasicAuth(SERVICE_USERNAME, SERVICE_PASSWORD)


def _require_url(base: str, name: str) -> str:
    if not base:
        raise RuntimeError(
            f"LMWS base URL '{name}' is not configured. Set it via the app config "
            f"(databricks.yml LMWS_*_URL / var.lmws_*_url) so it is passed to this job."
        )
    return base


def _check_body(data, where: str):
    """Raise on a body-level LMWS/FWS-API error.

    The gateway returns HTTP 200 even for logical failures, signalling them via a
    non-empty ``errorInfos`` (or ``errorInfo``) array, e.g.
    ``{"errorInfos": [{"code": "90002", "message": "Requester is not authorized ..."}]}``.
    ``raise_for_status`` can't catch these, so surface them as a job failure.
    """
    if not isinstance(data, dict):
        return data
    errs = data.get("errorInfos") or data.get("errorInfo")
    if errs:
        if isinstance(errs, list):
            msgs = "; ".join(str(e.get("message") or e) for e in errs)
        else:
            msgs = str(errs)
        raise RuntimeError(f"LMWS API error from {where}: {msgs}")
    return data


def _get(base: str, name: str, path: str, params: dict) -> dict:
    resp = requests.get(f"{_require_url(base, name)}/{path}", params=params, auth=_AUTH, verify=False)
    resp.raise_for_status()
    return _check_body(resp.json(), f"{name}/{path}")


def _post(base: str, name: str, path: str, payload: dict) -> dict:
    resp = requests.post(f"{_require_url(base, name)}/{path}", json=payload, auth=_AUTH, verify=False)
    resp.raise_for_status()
    return _check_body(resp.json(), f"{name}/{path}")


# ---------------------------------------------------------------------------
# Action handlers — real FWS-API / LMWS calls
# ---------------------------------------------------------------------------
def _require(value, name):
    if not value:
        raise RuntimeError(f"'{name}' is required for action '{action}'")


def _action_list_retrieve() -> dict:
    _require(list_name, "list_name")
    resp = _get(BASE_LMWS_AUTHN, "authn_url", "listRetrieve", {"listName": list_name})
    return {
        "Result": "SUCCESS",
        "listOwner": resp.get("listOwner"),
        "listSupervisors": resp.get("listSupervisors", []),
        "listMembers": resp.get("listMembers", []),
        "raw": resp,
    }


def _action_member_retrieve() -> dict:
    _require(members, "members")
    resp = _get(BASE_LMWS_CACHE, "cache_url", "memberRetrieve", {"member": members[0]})
    return {"Result": "SUCCESS", "memberships": resp.get("memberships", resp), "raw": resp}


def _action_list_members_add() -> dict:
    _require(list_name, "list_name")
    _require(members, "members")
    resp = _get(BASE_LMWS_AUTHN, "authn_url", "listMembersAdd", {
        "listName": list_name, "listMembers": members_csv, "justification": justification,
    })
    return {"Result": "SUCCESS", "workflowInfos": resp.get("workflowInfos", []), "raw": resp}


def _action_list_members_remove() -> dict:
    _require(list_name, "list_name")
    _require(members, "members")
    resp = _get(BASE_LMWS_AUTHN, "authn_url", "listMembersRemove", {
        "listName": list_name, "listMembers": members_csv, "justification": justification,
    })
    return {"Result": "SUCCESS", "removed": members, "raw": resp}


def _action_list_members_update() -> dict:
    _require(list_name, "list_name")
    _require(members, "members")
    resp = _get(BASE_LMWS_AUTHN, "authn_url", "listMembersUpdate", {
        "listName": list_name, "listMembers": members_csv, "justification": justification,
    })
    return {"Result": "SUCCESS", "members": members, "raw": resp}


def _action_list_create_new() -> dict:
    _require(list_name, "list_name")
    _require(owner, "owner")
    types = qc_list_types or ["qgroup", "email"]
    qc_json = json.dumps(
        {"qcListTypeInfos": {"qcListTypeInfo": [{"qcListType": t} for t in types]}}
    )
    resp = _get(BASE_LMWS_REST, "rest_url", "listCreateNew", {
        "listName": list_name,
        "description": description,
        "listOwner": owner,
        "listSupervisors": supervisors_csv,
        "qcListTypeInfos": qc_json,
    })
    return {"Result": "SUCCESS", "listName": list_name, "raw": resp}


def _action_create_sp_group() -> dict:
    _require(list_name, "list_name")
    _require(owner, "owner")
    resp = _post(BASE_FWS, "fws_url", "createSPGroup", {
        "actor": SERVICE_USERNAME,
        "listName": list_name,
        "requester": requester,
        "systemEndpoint": "Azure",
        "cloneListName": clone_source,
        "description": description,
        "owner": owner,
        "supervisors": supervisors_csv,
        "type": "SECURITY",
        "CCIClassification": cci_classification,
        "notificationCallBack": f"{requester}@qualcomm.com",
        "accessRequested": "on-prem-windowsbased",
    })
    return {
        "Result": "SUCCESS",
        "listName": list_name,
        "requestId": resp.get("requestId", resp.get("requestid")),
        "raw": resp,
    }


def _action_process_spac_policy() -> dict:
    _require(list_name, "list_name")
    _require(spac_policies, "spac_policies")
    policies = [{"policyType": "SPAC", "policyName": p} for p in spac_policies]
    key = "addPolicies" if request_type == "ADD" else "removePolicies"
    resp = _post(BASE_FWS, "fws_url", "processSpacPolicy", {
        "actor": SERVICE_USERNAME,
        "requester": requester,
        "systemEndpoint": "Azure",
        "listName": list_name,
        "requestType": request_type,
        key: policies,
    })
    return {"Result": "SUCCESS", "listName": list_name, "requestType": request_type, "raw": resp}


def _action_get_spac_policy() -> dict:
    _require(list_name, "list_name")
    resp = _post(BASE_FWS, "fws_url", "getSpacPolicy", {
        "actor": SERVICE_USERNAME,
        "requester": requester,
        "systemEndpoint": "Azure",
        "listName": list_name,
    })
    return {"Result": "SUCCESS", "listName": list_name, "policies": resp.get("policies", resp), "raw": resp}


def _action_request_confirmation() -> dict:
    _require(request_id, "request_id")
    resp = _post(BASE_FWS, "fws_url", "requestConfirmation", {
        "actor": SERVICE_USERNAME,
        "requester": requester,
        "systemEndpoint": "Azure",
        "requestid": request_id,
    })
    return {"Result": "SUCCESS", "requestId": request_id, "status": resp.get("status", resp), "raw": resp}


HANDLERS = {
    "list_retrieve": _action_list_retrieve,
    "member_retrieve": _action_member_retrieve,
    "list_members_add": _action_list_members_add,
    "list_members_remove": _action_list_members_remove,
    "list_members_update": _action_list_members_update,
    "list_create_new": _action_list_create_new,
    "create_sp_group": _action_create_sp_group,
    "process_spac_policy": _action_process_spac_policy,
    "get_spac_policy": _action_get_spac_policy,
    "request_confirmation": _action_request_confirmation,
}

# ---------------------------------------------------------------------------
# Dispatch & return
# ---------------------------------------------------------------------------
handler = HANDLERS.get(action)
if handler is None:
    raise RuntimeError(f"Unknown action: '{action}'. Valid actions: {sorted(HANDLERS)}")

result = handler()
print(f"LMWS action '{action}' result: {result}")

# Return the JSON document to the caller (LmwsProvider.parse_output reads this).
dbutils.notebook.exit(json.dumps(result))
