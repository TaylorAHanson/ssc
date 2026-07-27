"""N2K-aware LMWS endpoint *probe* client (diagnostics only).

Why this exists: ``listMembersAdd`` (the endpoint behind the production
``add_group_membership`` path in :mod:`app.providers.lmws.native`) validates that
the target list is **not** N2K and refuses otherwise, which is why an add to a
need-to-know list comes back as::

    Requester is not authorized to view or modify N2K list '<list>'

LMWS exposes separate endpoints for those lists on the REST base
(``.../lmws-rest/publicAPIrest``): ``n2kListMembershipAdd``,
``n2kAdminListMembershipAdd``, and ``listAnyMembershipAdd`` — the last of which
routes internally based on whether the list is N2K / Saviynt-backed. This module
is a **probe harness** for establishing empirically which of them the service
account can actually use.

What the LMWS documentation settles, and what it leaves to this harness:

* There is **no on-behalf-of parameter** on any membership-add method —
  authorization is always evaluated against the Basic-Auth identity. So the
  service account is both authenticator and authorization subject, and every
  target list needs it entitled directly.
* Two independent permission layers gate a call, and they need opposite fixes:
  the **ACL** check (is the account in ``lmws.rest`` / ``listmanager-n2k-admins``)
  runs first at the gateway, then the **supervisor** check (is the account a
  supervisor of *this* list) runs in business logic. The docs don't pin down the
  exact error text for each, so :func:`_classify_error` separates them by
  message content — that distinction is the main thing a probe run buys you.
* Whether ``listAnyMembershipAdd`` also needs ``listmanager-n2k-admins`` for an
  N2K target is explicitly *not* documented. Answering that is the point of the
  endpoint matrix.

Deliberately different from :class:`LmwsNativeClient`, which it subclasses for
credential + HTTP plumbing:

* **Never raises on a gateway rejection.** A body-level ``errorInfos``, a 401, or
  an unreachable host are all *findings*, not failures, so every call returns a
  structured envelope (see :meth:`LmwsN2kProbeClient.probe`). The production path
  keeps its raise-on-error behaviour; probing needs the whole response to
  compare endpoints side by side.
* **Member encoding is a knob** (:data:`MEMBER_STYLES`) because the docs show
  ``listMembers=[user1,user2]`` while the existing production call sends bare
  CSV — worth testing rather than guessing.
* **Base URL is overridable** so the same probe can run against the dev / tst /
  stg / prod gateways without touching Admin -> Settings.

Nothing here is wired into a workflow or state machine; it is reached only via
the two admin-gated probe tools in
``app.tools.self_service.lmws_n2k_probe`` and the
``backend/scripts/probe_lmws_n2k.py`` CLI.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Union

import httpx

from app.core.config import settings
from app.providers.lmws.client import _csv
from app.providers.lmws.native import LmwsNativeClient

logger = logging.getLogger(__name__)


#: Which configured base URL an endpoint lives under. Keys are the LMWS endpoint
#: names exactly as they appear in the gateway path.
BASES = {
    "authn": "LMWS_AUTHN_URL",
    "rest": "LMWS_REST_URL",
    "cache": "LMWS_CACHE_URL",
    "fws": "LMWS_FWS_URL",
}

#: Endpoints that only read. Safe to call repeatedly while probing.
READ_ENDPOINTS: Dict[str, str] = {
    # N2K list metadata — includes ``justQuestFormName`` when the list requires a
    # JQS justification form (in which case ``justification`` on an add must be a
    # form response id, not free text).
    "n2kListMetadataGet": "rest",
    # Status of a request id / reqKey returned by an N2K add (Saviynt workflow).
    "requestStatus": "rest",
    # Existing production reads, included so a probe run can establish a baseline
    # (e.g. confirm listRetrieve fails on the same list that an add succeeds on).
    "listRetrieve": "authn",
    "memberRetrieve": "cache",
}

#: Membership-add endpoints. These MUTATE (and may file a Saviynt approval
#: request), so callers must opt in explicitly — see the ``dry_run`` handling in
#: :meth:`LmwsN2kProbeClient.probe_membership_add`.
ADD_ENDPOINTS: Dict[str, str] = {
    # Routes internally for N2K / non-N2K / Saviynt / non-Saviynt. Per the docs
    # its ACL requirement is just `lmws.rest` (not `listmanager-n2k-admins`),
    # which makes it the most likely to work with the current service account.
    "listAnyMembershipAdd": "rest",
    # N2K-specific add; returns a requestId/reqKey to track via `requestStatus`.
    "n2kListMembershipAdd": "rest",
    # For lists that are themselves N2K *admin* lists.
    "n2kAdminListMembershipAdd": "rest",
    # The current production endpoint — hardcoded to reject N2K lists. Kept here
    # so a probe can reproduce the known failure alongside the alternatives.
    "listMembersAdd": "authn",
}

#: How to encode the ``listMembers`` parameter.
#:   ``auto``      -> ``u1`` for one member, ``[u1,u2]`` for several. Matches the
#:                    documented examples exactly and is the default.
#:   ``bracketed`` -> ``[u1]`` / ``[u1,u2]`` — always bracketed.
#:   ``csv``       -> ``u1,u2``    (what the production listMembersAdd path sends)
#:   ``repeated``  -> ``listMembers=u1&listMembers=u2``
MEMBER_STYLES = ("auto", "bracketed", "csv", "repeated")


def encode_members(
    members: Union[str, List[str], None], style: str = "auto"
) -> Union[str, List[str]]:
    """Encode ``members`` for the ``listMembers`` query parameter.

    The docs show a bare username for a single member and literal brackets with
    no spaces for several (``listMembers=[user1,user2]``), which is what ``auto``
    reproduces. Returns a ``list`` for the ``repeated`` style (httpx expands it
    into repeated query params) and a ``str`` otherwise.
    """
    if style not in MEMBER_STYLES:
        raise ValueError(f"Unknown member style {style!r}; expected one of {MEMBER_STYLES}.")
    csv = _csv(members)
    if style == "csv":
        return csv
    if style == "repeated":
        return [m for m in csv.split(",") if m]
    if style == "auto" and "," not in csv:
        return csv
    return f"[{csv}]"


#: Gateway text that identifies a missing ACL (the authenticating account is not
#: in ``lmws.rest`` / ``listmanager-n2k-admins``) as opposed to a list-level
#: permission problem. The two need completely different remediation, so the
#: probe reports them as distinct outcomes.
_ACL_MARKERS = ("not a member of", "acl", "environment specific")

#: Gateway text for "you reached the right endpoint but aren't a supervisor of
#: this list" — the expected failure until ``edhapisvc`` is added as a
#: list-supervisor on the target N2K list.
_SUPERVISOR_MARKERS = (
    "not authorized to view or modify",
    "supervisor",
    "not authorized to modify",
)

#: Gateway text for calling an N2K-only endpoint on a non-N2K list (or the
#: reverse, e.g. listMembersAdd against an N2K list).
_LIST_TYPE_MARKERS = ("is not n2k", "should be n2k", "not an n2k")


class LmwsN2kProbeClient(LmwsNativeClient):
    """Probe LMWS endpoints and report what the gateway said, without raising."""

    # ------------------------------------------------------------------
    # URL resolution
    # ------------------------------------------------------------------

    @staticmethod
    def base_url_for(endpoint: str, override: Optional[str] = None) -> str:
        """Resolve the base URL for ``endpoint`` (``override`` wins).

        Returns ``""`` when the relevant setting is blank, so the caller can
        report "not configured" as a finding rather than raising.
        """
        if override:
            return override.rstrip("/")
        which = READ_ENDPOINTS.get(endpoint) or ADD_ENDPOINTS.get(endpoint) or "rest"
        return str(getattr(settings, BASES[which], "") or "").rstrip("/")

    # ------------------------------------------------------------------
    # Core probe
    # ------------------------------------------------------------------

    async def probe(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        method: str = "GET",
        base_url: Optional[str] = None,
        dry_run: bool = False,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Call one LMWS endpoint and classify the outcome.

        Always returns an envelope; network errors, HTTP errors, and body-level
        ``errorInfos`` rejections are reported rather than raised so a caller can
        compare several endpoints in one run.

        Envelope keys: ``endpoint``, ``url``, ``params``, ``sent``, ``outcome``,
        ``ok``, ``http_status``, ``latency_ms``, ``errors``, ``request_ids``,
        ``body``, ``detail``. ``sent`` is False only when nothing left the app
        (dry run, missing base URL, missing credentials).
        """
        base = self.base_url_for(endpoint, base_url)
        url = f"{base}/{endpoint}" if base else ""
        sent_params = dict(params or {})
        result: Dict[str, Any] = {
            "endpoint": endpoint,
            "method": method.upper(),
            "url": url,
            "params": sent_params,
            "service_account": self.username or None,
            "verify_tls": self.verify_tls,
            "sent": False,
            "ok": False,
            "outcome": "error",
            "http_status": None,
            "latency_ms": None,
            "errors": [],
            "request_ids": [],
            "body": None,
            "detail": "",
        }

        if not base:
            which = READ_ENDPOINTS.get(endpoint) or ADD_ENDPOINTS.get(endpoint) or "rest"
            result["outcome"] = "not_configured"
            result["detail"] = (
                f"No base URL configured for '{endpoint}' — set {BASES[which]} in "
                f"Admin -> Settings (Group Management) or pass an explicit base_url."
            )
            return result

        if dry_run:
            result["outcome"] = "dry_run"
            result["detail"] = (
                "Dry run — nothing was sent. This is the exact request that would "
                "be made. Re-run with dry_run=false to call the gateway."
            )
            return result

        if not self.username or not self.password:
            result["outcome"] = "no_credentials"
            result["detail"] = (
                "LMWS service-account credentials could not be resolved (secret scope "
                f"'{settings.LMWS_SECRET_SCOPE}', key '{settings.LMWS_PASSWORD_SECRET_KEY}'). "
                "Nothing was sent."
            )
            return result

        timeout = timeout_seconds or self.timeout
        start = time.monotonic()
        # Set before the call: `sent` means "this request left the app", so only
        # dry_run / not_configured / no_credentials report False. An unreachable
        # host still counts as attempted.
        result["sent"] = True
        try:
            async with httpx.AsyncClient(verify=self.verify_tls, timeout=timeout) as client:
                resp = await client.request(
                    method.upper(),
                    url,
                    params=sent_params,
                    auth=(self.username, self.password),
                )
            result["latency_ms"] = round((time.monotonic() - start) * 1000)
            result["http_status"] = resp.status_code
            body: Any
            try:
                body = resp.json()
            except ValueError:
                body = (resp.text or "")[:2000]
            result["body"] = body

            if resp.status_code in (401, 403):
                result["errors"] = _error_messages(body) or [str(body)[:500]]
                result["outcome"] = "acl_missing"
                result["detail"] = (
                    f"HTTP {resp.status_code} — reached the gateway but the service "
                    "account was rejected before the endpoint's own logic ran. That is "
                    "the ACL layer: the account is not in the required ACL group "
                    "('lmws.rest' for the REST methods, 'listmanager-n2k-admins' for "
                    "the n2k* methods). Fix by requesting the ACL, not by changing "
                    "list permissions."
                )
                return result
            if resp.status_code >= 400:
                result["outcome"] = "http_error"
                result["detail"] = f"HTTP {resp.status_code} from the gateway."
                return result

            errors = _error_messages(body)
            if errors:
                result["errors"] = errors
                result["outcome"], result["detail"] = _classify_error(errors)
                return result

            result["ok"] = True
            result["outcome"] = "success"
            result["request_ids"] = _request_ids(body)
            tracking = (
                f" Track with requestStatus using: {', '.join(result['request_ids'])}."
                if result["request_ids"]
                else ""
            )
            result["detail"] = f"HTTP {resp.status_code} — the endpoint accepted the call.{tracking}"
            return result
        except httpx.TimeoutException as e:
            result["latency_ms"] = round((time.monotonic() - start) * 1000)
            result["outcome"] = "timeout"
            result["detail"] = f"No response within {timeout:.0f}s: {e}"
            return result
        except httpx.RequestError as e:
            result["latency_ms"] = round((time.monotonic() - start) * 1000)
            result["outcome"] = "unreachable"
            result["detail"] = (
                f"Could not reach the gateway: {e}. If this works from a notebook but "
                "not here, it is an egress/network-path difference, not an LMWS "
                "authorization problem."
            )
            return result
        except Exception as e:  # noqa: BLE001 - a probe must never propagate
            result["latency_ms"] = round((time.monotonic() - start) * 1000)
            result["outcome"] = "error"
            result["detail"] = f"Unexpected error: {e}"
            logger.warning("LMWS probe of %s raised unexpectedly", endpoint, exc_info=True)
            return result

    # ------------------------------------------------------------------
    # Convenience wrappers
    # ------------------------------------------------------------------

    async def probe_read(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Probe a read-only endpoint (see :data:`READ_ENDPOINTS`)."""
        if endpoint not in READ_ENDPOINTS:
            return {
                "endpoint": endpoint,
                "outcome": "rejected",
                "ok": False,
                "sent": False,
                "detail": (
                    f"'{endpoint}' is not a known read-only LMWS endpoint. Allowed: "
                    f"{', '.join(sorted(READ_ENDPOINTS))}."
                ),
            }
        return await self.probe(
            endpoint, params, base_url=base_url, timeout_seconds=timeout_seconds
        )

    async def probe_config(self, *, refresh: bool = False) -> Dict[str, Any]:
        """Report LMWS configuration health without calling the gateway.

        Every LMWS failure surfaces to the user as one opaque sentence, and the
        causes need different owners: an unreadable secret scope is a Databricks
        grant, a missing key is an IAM request, a blank base URL is a bundle/env
        change. This separates them in a single call, before any network probe.

        ``refresh`` drops this one secret from ``workspaces._secret_cache`` and
        re-resolves it. That cache stores misses as well as hits, so after
        granting the app's service principal READ on the scope the process would
        otherwise keep serving the cached failure until it restarts. Scoped to
        the LMWS key alone, so no other credential's caching behaviour changes.

        Never returns the password — only whether it resolved and its length.
        """
        scope = settings.LMWS_SECRET_SCOPE
        key = settings.LMWS_PASSWORD_SECRET_KEY

        if refresh:
            from app.core import workspaces

            evicted = workspaces._secret_cache.pop((scope, key), "__absent__")
            self.password = self._resolve_password()
            logger.info(
                "LMWS config probe refreshed secret %s/%s (was %s, now %s)",
                scope, key,
                "cached-miss" if evicted is None else
                "absent" if evicted == "__absent__" else "cached-hit",
                "resolved" if self.password else "still unresolved",
            )
        urls = {
            name: str(getattr(settings, attr, "") or "")
            for name, attr in BASES.items()
        }
        missing_urls = [BASES[n] for n, v in urls.items() if not v]

        result: Dict[str, Any] = {
            "native_enabled": bool(getattr(settings, "LMWS_NATIVE", True)),
            "service_account": self.username or None,
            "secret_scope": scope,
            "secret_key": key,
            "password_resolved": bool(self.password),
            "password_length": len(self.password) if self.password else 0,
            "password_source": (
                "LMWS_SERVICE_PASSWORD override" if settings.LMWS_SERVICE_PASSWORD
                else f"secret scope {scope}/{key}"
            ),
            "base_urls": urls,
            "missing_base_urls": missing_urls,
            "verify_tls": self.verify_tls,
            "refreshed": refresh,
            "ready": bool(self.username and self.password and urls.get("rest")),
        }

        if not self.password:
            result["scope_diagnosis"] = await asyncio.to_thread(_diagnose_scope, scope, key)

        problems = []
        if not self.username:
            problems.append("LMWS_SERVICE_USERNAME is blank.")
        if not self.password:
            problems.append(
                f"The password did not resolve from {result['password_source']} — "
                + str(result.get("scope_diagnosis", {}).get("detail", ""))
            )
            if not refresh:
                problems.append(
                    "Note: failed secret reads are cached for the life of the process, "
                    "so if you just granted access, re-run this with refresh=true "
                    "rather than waiting for a restart."
                )
        # Only the REST base blocks N2K work — every n2k*/listAny* endpoint lives
        # there. A blank authn/cache/fws URL just narrows which comparison probes
        # can run, so it is a note rather than a problem.
        if not urls.get("rest"):
            problems.append(
                "LMWS_REST_URL is blank, which blocks every N2K endpoint. It is set "
                "per deployment target in databricks.yml (note the LMWS_*_URL env "
                "entries are currently only wired into the dev target) or in "
                "Admin -> Settings under Group Management."
            )
        optional_missing = [u for u in missing_urls if u != "LMWS_REST_URL"]
        result["problems"] = problems
        result["notes"] = (
            [
                "Also blank (not required for N2K, but these probes will report "
                "not_configured): " + ", ".join(optional_missing)
                + ". LMWS_AUTHN_URL is needed only to reproduce the failing "
                "listMembersAdd baseline."
            ]
            if optional_missing
            else []
        )
        result["detail"] = (
            "LMWS looks configured; the gateway itself has not been contacted."
            if not problems
            else " ".join(problems)
        )
        return result

    async def probe_list_metadata(
        self,
        list_name: str,
        *,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Read a list's N2K metadata and interpret it.

        Two answers matter before attempting any add, and both are buried in the
        nested ``namespaceMetadata.metadata.metadataInfos`` array:

        * ``is_n2k`` — the endpoint only succeeds for N2K lists ("The requested
          list should be N2K List"), so a SUCCESS *is* the N2K signal.
        * ``jqs_form`` — when ``justQuestFormName`` is set, ``justification`` on
          an add must be a JQS answer id (``[[answerId]]``) rather than free
          text, and there is no REST API to obtain one. That makes the list
          unusable for unattended automation, so it is worth knowing first.
        """
        result = await self.probe_read(
            "n2kListMetadataGet", {"listName": list_name},
            base_url=base_url, timeout_seconds=timeout_seconds,
        )
        metadata = _metadata_infos(result.get("body"))
        jqs_form = metadata.get("justQuestFormName") or None
        result["list_name"] = list_name
        result["metadata"] = metadata
        result["is_n2k"] = bool(result.get("ok"))
        result["jqs_form"] = jqs_form
        result["requires_jqs"] = bool(jqs_form)
        if result.get("ok"):
            if jqs_form:
                result["detail"] = (
                    f"'{list_name}' is an N2K list and requires JQS form '{jqs_form}'. "
                    "The justification on an add must be a JQS answer id in the form "
                    "[[answerId]], which is only obtainable through the browser-based "
                    "JQS form — free-text justification will be rejected."
                )
            else:
                result["detail"] = (
                    f"'{list_name}' is an N2K list and does NOT require a JQS form, so "
                    "a free-text justification is acceptable."
                )
        elif result.get("outcome") == "wrong_list_type":
            result["is_n2k"] = False
            result["detail"] = (
                f"'{list_name}' is not an N2K list — the ordinary listMembersAdd path "
                "should work for it."
            )
        return result

    async def probe_request_status(
        self,
        *,
        requestid: Optional[str] = None,
        reqkey: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Look up a request returned by an N2K add.

        Both parameters are optional but at least one is required; ``reqkey``
        takes precedence at the gateway when both are supplied. Note the
        lowercase spellings — the response field is ``requestid``, not the
        ``requestId`` an add returns.
        """
        params: Dict[str, Any] = {}
        if reqkey:
            params["reqkey"] = reqkey
        if requestid:
            params["requestid"] = requestid
        if not params:
            return {
                "endpoint": "requestStatus",
                "outcome": "rejected",
                "ok": False,
                "sent": False,
                "detail": "Provide requestid or reqkey (at least one is required).",
            }
        result = await self.probe_read(
            "requestStatus", params, base_url=base_url, timeout_seconds=timeout_seconds
        )
        body = result.get("body")
        if result.get("ok") and isinstance(body, dict):
            result["request_state"] = {
                k: body.get(k)
                for k in ("listName", "requestid", "reqkey", "status", "state",
                          "requestor", "requestee", "requestdate", "approvers", "comments")
                if body.get(k) not in (None, "")
            }
            status = str(body.get("status") or "")
            approvers = body.get("approvers")
            if status and not approvers:
                result["detail"] = f"Status '{status}' with no pending approvers."
            elif status:
                result["detail"] = f"Status '{status}'; pending approvers: {approvers}."
        return result

    async def probe_membership_add(
        self,
        endpoint: str,
        list_name: str,
        members: Union[str, List[str]],
        *,
        justification: Optional[str] = None,
        member_style: str = "auto",
        base_url: Optional[str] = None,
        dry_run: bool = True,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Probe one of the membership-add endpoints (see :data:`ADD_ENDPOINTS`).

        ``dry_run`` defaults to ``True`` — these endpoints mutate group membership
        and may file an approval request, so the caller opts in to actually
        sending. When the list uses a JQS justification form, ``justification``
        must be the form response id; check ``n2kListMetadataGet`` first.
        """
        if endpoint not in ADD_ENDPOINTS:
            return {
                "endpoint": endpoint,
                "outcome": "rejected",
                "ok": False,
                "sent": False,
                "detail": (
                    f"'{endpoint}' is not a known LMWS membership-add endpoint. Allowed: "
                    f"{', '.join(sorted(ADD_ENDPOINTS))}."
                ),
            }
        try:
            encoded = encode_members(members, member_style)
        except ValueError as e:
            return {
                "endpoint": endpoint,
                "outcome": "rejected",
                "ok": False,
                "sent": False,
                "detail": str(e),
            }

        params: Dict[str, Any] = {
            "listName": list_name,
            "listMembers": encoded,
            "justification": justification or settings.LMWS_DEFAULT_JUSTIFICATION,
        }
        out = await self.probe(
            endpoint,
            params,
            base_url=base_url,
            dry_run=dry_run,
            timeout_seconds=timeout_seconds,
        )
        out["member_style"] = member_style
        return out

    async def probe_add_matrix(
        self,
        list_name: str,
        members: Union[str, List[str]],
        *,
        justification: Optional[str] = None,
        endpoints: Optional[List[str]] = None,
        member_style: str = "auto",
        base_url: Optional[str] = None,
        dry_run: bool = True,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Probe every membership-add endpoint against one list and summarize.

        The point of the matrix: with a single call you learn which endpoints the
        service account can use for a given N2K list, instead of discovering it
        one failed request at a time.
        """
        targets = endpoints or list(ADD_ENDPOINTS)
        results = []
        for endpoint in targets:
            results.append(
                await self.probe_membership_add(
                    endpoint,
                    list_name,
                    members,
                    justification=justification,
                    member_style=member_style,
                    base_url=base_url,
                    dry_run=dry_run,
                    timeout_seconds=timeout_seconds,
                )
            )
        worked = [r["endpoint"] for r in results if r.get("ok")]
        if dry_run:
            summary = f"Dry run — {len(results)} request(s) built, none sent."
        elif worked:
            summary = (
                f"{len(worked)} of {len(results)} endpoint(s) accepted the call: "
                f"{', '.join(worked)}."
            )
        else:
            summary = f"None of the {len(results)} endpoint(s) accepted the call."
        return {
            "list_name": list_name,
            "members": _csv(members).split(",") if members else [],
            "member_style": member_style,
            "dry_run": dry_run,
            "endpoints_probed": targets,
            "endpoints_succeeded": worked,
            "results": results,
            "summary": summary,
        }


# ----------------------------------------------------------------------
# Body parsing helpers
# ----------------------------------------------------------------------

def _error_messages(body: Any) -> List[str]:
    """Pull failure messages out of a response body.

    Covers both shapes the gateway uses on an HTTP 200: an ``errorInfos`` /
    ``errorInfo`` array (what ``LmwsNativeClient._check_body`` keys off) and a
    top-level ``Result`` of ``EXCEPTION`` / ``FAILED`` / ``ERROR``, which is how
    a business-logic rejection such as a failed supervisor check surfaces.
    Collects the messages instead of raising.
    """
    if not isinstance(body, dict):
        return []

    messages: List[str] = []
    errs = body.get("errorInfos") or body.get("errorInfo")
    if isinstance(errs, dict):
        errs = [errs]
    if isinstance(errs, list):
        for e in errs:
            messages.append(str(e.get("message") or e) if isinstance(e, dict) else str(e))
    elif errs:
        messages.append(str(errs))

    status = str(body.get("Result", body.get("result", ""))).upper()
    if status in {"EXCEPTION", "FAILED", "ERROR"} and not messages:
        detail = body.get("message") or body.get("Message") or body.get("description")
        messages.append(str(detail) if detail else f"Result={status} with no message.")
    return messages


def _classify_error(messages: List[str]) -> tuple:
    """Map gateway error text to ``(outcome, detail)``.

    The whole point of a probe run is telling apart the two failure modes that
    look identical in the raw response but need different fixes: a missing ACL
    group on the service account versus missing list-supervisor status on the
    specific target list.
    """
    joined = " ".join(messages).lower()
    text = "; ".join(messages)

    if any(m in joined for m in _LIST_TYPE_MARKERS):
        return "wrong_list_type", (
            f"The endpoint rejected the list as the wrong type: {text}. The n2k* "
            "methods require an N2K list and listMembersAdd requires a non-N2K one, "
            "so this says which family the list belongs to."
        )
    # Check the ACL markers before the supervisor ones: an ACL rejection can also
    # mention authorization, but "not a member of ... ACL" is the specific signal.
    if "not a member of" in joined and "acl" in joined:
        return "acl_missing", (
            f"The service account is missing the required ACL group: {text}. Request "
            "the ACL (lmws.rest for the general REST methods); this is not a "
            "list-level permission problem."
        )
    if any(m in joined for m in _SUPERVISOR_MARKERS):
        return "supervisor_required", (
            f"The endpoint was reachable and the ACL passed, but the service account "
            f"is not a supervisor of this list: {text}. Fix is per-list — the list "
            "owner or an existing supervisor adds the service account (there is no "
            "API to add a supervisor to an N2K list)."
        )
    return "api_error", (
        f"HTTP 200 but the body reported a failure: {text}"
    )


def _diagnose_scope(scope: str, key: str) -> Dict[str, Any]:
    """Tell apart "no READ on the scope" from "the key isn't there".

    ``_read_secret`` is deliberately fail-soft and returns ``None`` for both, but
    they need different fixes — a Databricks permission grant versus asking the
    credential owner to add the key. Listing the scope's secrets distinguishes
    them: listing returns key names only, never values.
    """
    out: Dict[str, Any] = {"scope": scope, "key": key, "scope_readable": False,
                           "key_present": None, "detail": ""}
    try:
        from databricks.sdk import WorkspaceClient

        names = [s.key for s in WorkspaceClient().secrets.list_secrets(scope=scope)]
        out["scope_readable"] = True
        out["key_present"] = key in names
        out["keys_in_scope"] = sorted(n for n in names if n)
        if key in names:
            out["detail"] = (
                f"Scope '{scope}' is readable and key '{key}' exists, but the value came "
                "back empty — the secret is likely set to an empty string."
            )
        else:
            out["detail"] = (
                f"Scope '{scope}' is readable but has no key named '{key}'. Available "
                f"keys: {', '.join(out['keys_in_scope']) or '(none)'}. Either add the "
                "key or point LMWS_PASSWORD_SECRET_KEY at the right one in "
                "Admin -> Settings."
            )
    except Exception as e:  # noqa: BLE001 - diagnosis must never fail the caller
        out["detail"] = (
            f"Could not list secrets in scope '{scope}': {e}. The app's service "
            "principal most likely lacks READ on that scope (or the scope does not "
            "exist in this workspace). Grant the app SP READ on the scope."
        )
        logger.warning("LMWS scope diagnosis failed for %s: %s", scope, e)
    return out


def _metadata_infos(body: Any) -> Dict[str, str]:
    """Flatten ``n2kListMetadataGet``'s nested key/label/value array into a dict.

    The response nests the interesting fields several levels down::

        {"namespaceMetadata": {"metadata": {"metadataInfos": [
            {"key": "justQuestFormName", "label": "...", "value": "iamqa.bigform.hasexp"},
            ...
        ]}}}
    """
    if not isinstance(body, dict):
        return {}
    infos = (
        body.get("namespaceMetadata", {})
        .get("metadata", {})
        .get("metadataInfos", [])
        if isinstance(body.get("namespaceMetadata"), dict)
        else []
    )
    out: Dict[str, str] = {}
    if isinstance(infos, dict):
        infos = [infos]
    if not isinstance(infos, list):
        return out
    for info in infos:
        if not isinstance(info, dict):
            continue
        key = info.get("key")
        if key:
            out[str(key)] = str(info.get("value") or "")
    return out


def _request_ids(body: Any) -> List[str]:
    """Collect request identifiers an N2K add returns for ``requestStatus`` polling.

    The documented success shape nests them under ``workflowInfos``; top-level
    variants are also checked since the field naming differs across endpoints
    (``requestId`` / ``requestid`` / ``reqKey``).
    """
    if not isinstance(body, dict):
        return []
    keys = ("requestId", "requestid", "reqKey", "reqkey")
    found: List[str] = []

    def _collect(d: Any) -> None:
        if not isinstance(d, dict):
            return
        for k in keys:
            v = d.get(k)
            if v not in (None, "") and str(v) not in found:
                found.append(str(v))

    _collect(body)
    infos = body.get("workflowInfos") or []
    if isinstance(infos, dict):
        infos = [infos]
    if isinstance(infos, list):
        for info in infos:
            _collect(info)
    return found
