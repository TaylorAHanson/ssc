"""Probe the N2K-aware LMWS membership endpoints from a shell.

Companion to the ``lmws_probe_read`` / ``lmws_probe_membership_add`` agent tools,
for when you want a fast loop without going through chat. Answers one question:
for a given (possibly N2K) list, which LMWS membership-add endpoint will the
service account actually accept — ``listAnyMembershipAdd``,
``n2kListMembershipAdd``, ``n2kAdminListMembershipAdd``, or the production
``listMembersAdd`` that refuses N2K lists outright.

**Where this can run:** the LMWS service-account password comes from a Databricks
secret scope and the gateway is reachable only from inside the corporate network
path, so a laptop checkout generally cannot use this — it reports
``no_credentials`` or ``unreachable``. Use the ``lmws_probe_read`` /
``lmws_probe_membership_add`` agent tools from the running app instead, which is
the supported path. This script is for an environment that already has both (the
app host, or a Databricks web terminal with the scope readable and
``LMWS_SERVICE_PASSWORD`` exported).

Run inside the backend venv (deps are not installed globally):

    cd backend && source venv/bin/activate

    # Is the list N2K, and does it need a JQS justification form?
    python -m scripts.probe_lmws_n2k metadata --list edh_dbx_enterprise_deng

    # Build every candidate add request without sending anything.
    python -m scripts.probe_lmws_n2k matrix --list edh_dbx_enterprise_deng --member taylhans

    # Actually call the gateway (REAL membership change; may file an approval).
    python -m scripts.probe_lmws_n2k matrix --list edh_dbx_enterprise_deng \
        --member taylhans --execute

    # One endpoint, alternate member encoding, against a non-prod gateway.
    python -m scripts.probe_lmws_n2k add --list some_list --member taylhans \
        --endpoint listAnyMembershipAdd --member-style csv --execute \
        --base-url https://dev.apigw-op.example.com/iam/v1/lmws-rest/publicAPIrest

    # Follow up on a requestId returned by an N2K add.
    python -m scripts.probe_lmws_n2k status --request-id 435918834

Credentials resolve exactly as the app resolves them: ``LMWS_SERVICE_USERNAME``
plus the password read from the LMWS secret scope via the app's service principal
(``LMWS_SERVICE_PASSWORD`` overrides for local dev). Nothing is sent unless
``--execute`` is passed for the mutating subcommands.
"""
import argparse
import asyncio
import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("probe_lmws_n2k")

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def _load_dotenv() -> None:
    """Load ``backend/.env`` so the script sees the same config the app does."""
    env_path = os.path.join(_BACKEND_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="command", required=True)

    def _common(sp):
        sp.add_argument("--base-url", default=None,
                        help="Override the configured base URL (target dev/tst/stg/prod).")
        sp.add_argument("--timeout", type=float, default=None,
                        help="Per-request timeout in seconds.")
        sp.add_argument("--json", action="store_true",
                        help="Emit the raw result envelope as JSON only.")

    sp = sub.add_parser("metadata", help="Read an N2K list's metadata (n2kListMetadataGet).")
    sp.add_argument("--list", dest="list_name", required=True, help="Target list name.")
    _common(sp)

    sp = sub.add_parser("status", help="Check a request returned by an N2K add (requestStatus).")
    sp.add_argument("--request-id", default=None, help="Numeric request id (sent as 'requestid').")
    sp.add_argument("--req-key", default=None,
                    help="Request key (sent as 'reqkey'; the gateway prefers it over requestid).")
    _common(sp)

    sp = sub.add_parser("read", help="Call any allowed read-only endpoint with free-form params.")
    sp.add_argument("--endpoint", required=True, help="Endpoint name, e.g. n2kListMetadataGet.")
    sp.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                    help="Query parameter; repeat for multiple.")
    _common(sp)

    sp = sub.add_parser("add", help="Probe ONE membership-add endpoint.")
    sp.add_argument("--list", dest="list_name", required=True, help="Target list name.")
    sp.add_argument("--member", action="append", required=True, dest="members",
                    help="Corporate username (CN); repeat for multiple.")
    sp.add_argument("--endpoint", required=True, help="Endpoint name, e.g. listAnyMembershipAdd.")
    sp.add_argument("--justification", default=None,
                    help="Justification text, or a JQS form response id when the list requires one.")
    sp.add_argument("--member-style", default="auto", choices=("auto", "bracketed", "csv", "repeated"),
                    help="How to encode listMembers (default: auto — bare for one member, [u1,u2] for several).")
    sp.add_argument("--execute", action="store_true",
                    help="Actually send it. REAL membership change; may file an approval request.")
    _common(sp)

    sp = sub.add_parser("matrix", help="Probe every membership-add endpoint and compare.")
    sp.add_argument("--list", dest="list_name", required=True, help="Target list name.")
    sp.add_argument("--member", action="append", required=True, dest="members",
                    help="Corporate username (CN); repeat for multiple.")
    sp.add_argument("--justification", default=None, help="Justification text or JQS form response id.")
    sp.add_argument("--member-style", default="auto", choices=("auto", "bracketed", "csv", "repeated"),
                    help="How to encode listMembers (default: auto — bare for one member, [u1,u2] for several).")
    sp.add_argument("--endpoint", action="append", default=None, dest="endpoints",
                    help="Limit the matrix to these endpoints; repeat for multiple.")
    sp.add_argument("--execute", action="store_true",
                    help="Actually send them. REAL membership changes; may file approval requests.")
    _common(sp)

    return p.parse_args(argv)


def _kv_params(pairs) -> dict:
    out = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--param must be KEY=VALUE, got {pair!r}")
        key, value = pair.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _report(result: dict, as_json: bool) -> None:
    """Log a probe envelope (or matrix) in a scannable form."""
    if as_json:
        logger.info(json.dumps(result, indent=2, default=str))
        return

    if "results" in result:
        logger.info("List:    %s", result.get("list_name"))
        logger.info("Members: %s", ", ".join(result.get("members") or []))
        logger.info("Summary: %s", result.get("summary"))
        for item in result["results"]:
            logger.info("-" * 72)
            _report_one(item)
        return
    _report_one(result)


def _report_one(result: dict) -> None:
    logger.info("Endpoint: %s", result.get("endpoint"))
    logger.info("Outcome:  %s (ok=%s, sent=%s)", result.get("outcome"),
                result.get("ok"), result.get("sent"))
    if result.get("url"):
        logger.info("URL:      %s %s", result.get("method", "GET"), result["url"])
    if result.get("params"):
        logger.info("Params:   %s", json.dumps(result["params"], default=str))
    if result.get("http_status") is not None:
        logger.info("HTTP:     %s (%sms)", result["http_status"], result.get("latency_ms"))
    for msg in result.get("errors") or []:
        logger.info("Error:    %s", msg)
    if result.get("request_ids"):
        logger.info("Requests: %s", ", ".join(result["request_ids"]))
    if result.get("detail"):
        logger.info("Detail:   %s", result["detail"])
    if result.get("body") is not None:
        logger.info("Body:     %s", json.dumps(result["body"], default=str)[:1500])


async def _run(args) -> int:
    from app.providers.lmws.n2k import LmwsN2kProbeClient

    client = LmwsN2kProbeClient()
    if not client.username:
        logger.warning("LMWS_SERVICE_USERNAME is not set — probes will report no_credentials.")

    if args.command == "metadata":
        result = await client.probe_list_metadata(
            args.list_name, base_url=args.base_url, timeout_seconds=args.timeout,
        )
    elif args.command == "status":
        result = await client.probe_request_status(
            requestid=args.request_id, reqkey=args.req_key,
            base_url=args.base_url, timeout_seconds=args.timeout,
        )
    elif args.command == "read":
        result = await client.probe_read(
            args.endpoint, _kv_params(args.param),
            base_url=args.base_url, timeout_seconds=args.timeout,
        )
    elif args.command == "add":
        result = await client.probe_membership_add(
            args.endpoint, args.list_name, args.members,
            justification=args.justification,
            member_style=args.member_style,
            base_url=args.base_url,
            dry_run=not args.execute,
            timeout_seconds=args.timeout,
        )
    elif args.command == "matrix":
        result = await client.probe_add_matrix(
            args.list_name, args.members,
            justification=args.justification,
            endpoints=args.endpoints,
            member_style=args.member_style,
            base_url=args.base_url,
            dry_run=not args.execute,
            timeout_seconds=args.timeout,
        )
    else:  # argparse enforces the choices
        raise SystemExit(f"Unknown command {args.command!r}")

    _report(result, args.json)

    if "results" in result:
        return 0 if (result.get("endpoints_succeeded") or result.get("dry_run")) else 1
    return 0 if result.get("ok") or result.get("outcome") == "dry_run" else 1


def main(argv=None) -> int:
    _load_dotenv()
    args = _parse_args(argv)
    if getattr(args, "execute", False):
        logger.warning(
            "--execute set: this will make REAL membership changes on '%s'.",
            getattr(args, "list_name", "?"),
        )
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
