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
# SCAFFOLD NOTICE
# ---------------------------------------------------------------------------
# This file faithfully implements the documented LMWS *contract* — widget
# parsing, secret-scope auth, action dispatch, JSON output, RuntimeError on
# failure — but the actual FWS-API HTTP calls are PLACEHOLDERS (see
# `_fws_api_call` / the `_action_*` handlers). Drop in the real FWS-API client
# logic, or paste the existing production notebook over this file. The app-side
# wiring (provider, state machines, tools) does not change either way.
#
# Prerequisites (per the LMWS service docs):
#   * Databricks secret scope (default `lmws`) with keys `username` / `password`
#     holding the service-account credentials.
#   * Classic compute cluster (any size — purely API calls, no Spark).
#   * Service account must be a list-supervisor of target lists for membership ops.

import json
import sys

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
dbutils.widgets.text("secret_scope", "lmws")              # Databricks secret scope for creds

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
secret_scope = dbutils.widgets.get("secret_scope").strip() or "lmws"

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


# ---------------------------------------------------------------------------
# FWS-API client — PLACEHOLDER
# ---------------------------------------------------------------------------
def _fws_api_call(operation: str, payload: dict) -> dict:
    """Call the Qualcomm FWS-API.

    PLACEHOLDER: replace the body with the real FWS-API request/response logic
    (the existing production notebook's API client). It should authenticate with
    SERVICE_USERNAME / SERVICE_PASSWORD and return the parsed JSON response.
    """
    raise NotImplementedError(
        f"FWS-API call '{operation}' is not implemented in this scaffold. "
        "Paste the production LMWS notebook logic here."
    )


# ---------------------------------------------------------------------------
# Action handlers (core actions implemented against the contract;
# group/SPAC lifecycle actions are intentionally stubbed for now)
# ---------------------------------------------------------------------------
def _require(value, name):
    if not value:
        raise RuntimeError(f"'{name}' is required for action '{action}'")


def _action_list_retrieve() -> dict:
    _require(list_name, "list_name")
    resp = _fws_api_call("list_retrieve", {"list_name": list_name})
    return {
        "Result": "SUCCESS",
        "listOwner": resp.get("listOwner"),
        "listSupervisors": resp.get("listSupervisors", []),
        "listMembers": resp.get("listMembers", []),
    }


def _action_member_retrieve() -> dict:
    _require(members, "members")
    resp = _fws_api_call("member_retrieve", {"member": members[0]})
    return {"Result": "SUCCESS", "memberships": resp.get("memberships", [])}


def _action_list_members_add() -> dict:
    _require(list_name, "list_name")
    _require(members, "members")
    resp = _fws_api_call(
        "list_members_add",
        {"list_name": list_name, "members": members, "justification": justification},
    )
    return {"Result": "SUCCESS", "workflowInfos": resp.get("workflowInfos", [])}


def _action_list_members_remove() -> dict:
    _require(list_name, "list_name")
    _require(members, "members")
    _fws_api_call(
        "list_members_remove",
        {"list_name": list_name, "members": members, "justification": justification},
    )
    return {"Result": "SUCCESS", "removed": members}


def _action_list_members_update() -> dict:
    _require(list_name, "list_name")
    _require(members, "members")
    _fws_api_call(
        "list_members_update",
        {"list_name": list_name, "members": members, "justification": justification},
    )
    return {"Result": "SUCCESS", "members": members}


def _action_stubbed() -> dict:
    raise RuntimeError(
        f"Action '{action}' is not implemented yet. Supported actions: "
        "list_retrieve, member_retrieve, list_members_add, list_members_remove, "
        "list_members_update."
    )


HANDLERS = {
    "list_retrieve": _action_list_retrieve,
    "member_retrieve": _action_member_retrieve,
    "list_members_add": _action_list_members_add,
    "list_members_remove": _action_list_members_remove,
    "list_members_update": _action_list_members_update,
    # Group/SPAC lifecycle — stubbed
    "list_create_new": _action_stubbed,
    "create_sp_group": _action_stubbed,
    "process_spac_policy": _action_stubbed,
    "get_spac_policy": _action_stubbed,
    "request_confirmation": _action_stubbed,
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
