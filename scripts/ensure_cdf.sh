#!/usr/bin/env bash
# Ensure the Lakebase Change Data Feed (Lakehouse Sync) exists for a bundle target.
#
# A bundle can't declare a CDF config, so this runs after `bundle deploy` /
# `bundle run`: it reads the target's resolved variables, and creates the feed
# that copies the app's Postgres schema into Unity Catalog `lb_<table>_history`
# tables if one isn't there yet. Safe to re-run. See docs/PLATFORM_SETUP.md §7.2.
#
# Usage: scripts/ensure_cdf.sh <target> [--dry-run]
#   PG_SCHEMA  Postgres schema to replicate (default: selfservice, the DB_SCHEMA setting)
#
# Skips (exit 0) when lakebase_cdf_tables is "none" or the target's cdf_catalog /
# cdf_schema are still REPLACE_WITH_* placeholders. A feed's destination can't be
# changed after creation, so an existing feed is never touched; if its destination
# differs from the target's variables the script warns and leaves it alone.
#
# Needs the Databricks CLI (with `databricks postgres create-cdf-config`), python3,
# and an identity with CAN MANAGE on the Lakebase project plus USE CATALOG,
# USE SCHEMA and CREATE TABLE on the destination.
set -euo pipefail

TARGET="${1:-}"
DRY_RUN=0
[ "${2:-}" = "--dry-run" ] && DRY_RUN=1
if [ -z "$TARGET" ]; then
  echo "usage: $0 <target> [--dry-run]" >&2
  exit 2
fi

PG_SCHEMA="${PG_SCHEMA:-selfservice}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() { echo "[ensure_cdf:$TARGET] $*"; }

# Resolve the target's variables and Lakebase project name from the bundle.
# (The Python lives in variables, not inside $(...): bash 3.2 on macOS can't parse
# a heredoc containing ")" within a command substitution.)
read -r -d '' RESOLVE_PY <<'PY' || true
import json, os, re, shlex

b = json.loads(os.environ["BUNDLE_JSON"])
v = {k: (x.get("value", x.get("default", "")) or "") for k, x in b.get("variables", {}).items()}
project = b["resources"]["database_instances"]["edh_ssc_db"]["name"]
# `validate` can leave ${var.x} / ${bundle.target} unresolved in resource names.
project = re.sub(r"\$\{var\.(\w+)\}", lambda m: str(v.get(m.group(1), m.group(0))), project)
project = project.replace("${bundle.target}", b.get("bundle", {}).get("target", ""))
if "${" in project:
    raise SystemExit("could not resolve Lakebase project name: " + project)
for key, val in {
    "CDF_TABLES": v.get("lakebase_cdf_tables", "none"),
    "CDF_CATALOG": v.get("cdf_catalog", ""),
    "CDF_SCHEMA": v.get("cdf_schema", ""),
    "DB_ID": v.get("lakebase_database_id", "databricks-postgres"),
    "PROJECT": project,
}.items():
    print("%s=%s" % (key, shlex.quote(str(val))))
PY

BUNDLE_JSON="$(databricks bundle validate -t "$TARGET" -o json)"
export BUNDLE_JSON
RESOLVED="$(python3 -c "$RESOLVE_PY")"
eval "$RESOLVED"

tables="$(echo "$CDF_TABLES" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
if [ -z "$tables" ] || [ "$tables" = "none" ]; then
  log "lakebase_cdf_tables is '$CDF_TABLES' - no tables opted in, skipping."
  exit 0
fi
case "$CDF_CATALOG$CDF_SCHEMA" in
  ""|*REPLACE_WITH*)
    log "cdf_catalog / cdf_schema are not set for this target - skipping."
    exit 0
    ;;
esac
if [ -z "$CDF_CATALOG" ] || [ -z "$CDF_SCHEMA" ]; then
  log "cdf_catalog and cdf_schema must both be set - skipping."
  exit 0
fi

PARENT="projects/${PROJECT}/branches/production/databases/${DB_ID}"
log "parent: $PARENT"
log "replicating Postgres schema '$PG_SCHEMA' -> ${CDF_CATALOG}.${CDF_SCHEMA}"

EXISTING="$(databricks postgres list-cdf-configs "$PARENT" -o json)"

# Find a config for this Postgres schema and print "<catalog>.<schema>" (empty if none).
# The response shape is Beta, so look for the keys anywhere in each item.
read -r -d '' FIND_PY <<'PY' || true
import json, os

data = json.loads(os.environ["EXISTING"] or "[]")
want = os.environ["PG_SCHEMA"]


def walk(node):
    if isinstance(node, dict):
        yield node
        for child in node.values():
            yield from walk(child)
    elif isinstance(node, list):
        for child in node:
            yield from walk(child)


def pick(item, key):
    for d in walk(item):
        if isinstance(d.get(key), str):
            return d[key]
    return ""


items = data if isinstance(data, list) else next(
    (v for v in data.values() if isinstance(v, list)), []
)
for item in items:
    if pick(item, "postgres_schema") == want:
        print(pick(item, "catalog") + "." + pick(item, "schema"))
        break
PY
export EXISTING PG_SCHEMA
FOUND="$(python3 -c "$FIND_PY")"

if [ -n "$FOUND" ]; then
  if [ "$FOUND" != "${CDF_CATALOG}.${CDF_SCHEMA}" ]; then
    log "WARNING: a feed for '$PG_SCHEMA' already writes to $FOUND, not ${CDF_CATALOG}.${CDF_SCHEMA}. A feed's destination can't be changed; delete it first (databricks postgres delete-cdf-config) to move it. Leaving it as is."
  else
    log "feed for '$PG_SCHEMA' already exists -> $FOUND. Nothing to do."
  fi
  exit 0
fi

if [ "$DRY_RUN" = 1 ]; then
  log "dry run: would create the feed."
  exit 0
fi

CONFIG_ID="$(echo "${PG_SCHEMA}" | tr '_' '-' | tr '[:upper:]' '[:lower:]')"
log "creating feed '$CONFIG_ID'..."
databricks postgres create-cdf-config "$PARENT" "$CDF_CATALOG" "$CDF_SCHEMA" "$PG_SCHEMA" \
  --cdf-config-id "$CONFIG_ID"
log "feed created. Tables with REPLICA IDENTITY FULL appear as lb_<table>_history once they have a row."
