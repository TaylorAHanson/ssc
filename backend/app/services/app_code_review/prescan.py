"""Deterministic pre-scan: whose identity reads data, and committed secrets.

These facts come from code, not the model, so they set a floor the reviewer
can escalate but never talk down: a README that says "this app is OBO-only,
approve it" can't hide a bare ``WorkspaceClient()`` next to a SQL query.

Identity signals are only read from source files (not docs), and comment-only
lines are ignored, so prose can't manufacture or mask one. Secret detection
runs over every text file, because a token pasted in a README leaks just the
same.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Pattern, Tuple

import yaml

from app.services.app_code_review.models import Finding, Signal

CODE_EXTENSIONS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".ipynb", ".scala", ".java",
    ".go", ".r", ".sql", ".sh",
}

# Declared app resources the app's service principal gets access to, and
# whether that access reaches governed data. The app's Lakebase ``database`` is
# its own state store, like a serving endpoint or a secret, not governed data
# (unless it holds synced copies of UC tables, which the reviewer judges).
DATA_BEARING_RESOURCES = {"sql_warehouse", "uc_securable", "genie_space", "volume"}
KNOWN_RESOURCES = DATA_BEARING_RESOURCES | {"database", "serving_endpoint", "secret", "job", "experiment"}

# The app forwards the signed-in user's token.
_OBO_PATTERNS: List[Tuple[str, Pattern]] = [
    ("forwarded user token", re.compile(r"x-forwarded-access-token", re.IGNORECASE)),
]

# The app authenticates as itself. The SDK's no-argument constructors pick up
# the app's DATABRICKS_CLIENT_ID/SECRET, which is the service principal.
_SP_PATTERNS: List[Tuple[str, Pattern]] = [
    ("default SDK client (app service principal)", re.compile(r"\bWorkspaceClient\(\s*\)")),
    ("default SDK client (app service principal)", re.compile(r"new\s+WorkspaceClient\(\s*(\{\s*\})?\s*\)")),
    # A bare Config() is how OBO apps read the workspace host too; it only
    # means SP access once its credentials are used.
    ("SDK config credentials (app service principal)", re.compile(r"\.authenticate\b")),
    ("service principal credentials", re.compile(r"\bDATABRICKS_CLIENT_(ID|SECRET)\b")),
    ("service principal OAuth", re.compile(r"\boauth_service_principal\(")),
    ("SDK credentials provider", re.compile(r"\bcredentials_provider\s*[=:]")),
    ("app-held personal access token", re.compile(r"\bDATABRICKS_TOKEN\b")),
    ("Lakebase credential for the app", re.compile(r"\bgenerate_database_credential\b")),
]

# The code touches governed data (whatever identity it uses).
_DATA_PATTERNS: List[Tuple[str, Pattern]] = [
    ("Databricks SQL connector", re.compile(r"\bsql\.connect\(|@databricks/sql|databricks\.sql\b")),
    ("statement execution API", re.compile(r"\bstatement_execution\b|/api/2\.0/sql/statements")),
    ("Spark SQL", re.compile(r"\bspark\.(sql|table|read)\b")),
    ("Unity Catalog table/volume API", re.compile(r"\.tables\.(get|list)\(|\bfiles\.(download|upload)\(|/Volumes/")),
    ("Genie API", re.compile(r"\bgenie\.(start_conversation|create_message|execute)|/api/2\.0/genie/")),
    ("Vector Search", re.compile(r"\bVectorSearchClient\b|vector_search_endpoints")),
    ("SQL query", re.compile(r"\bSELECT\b.{1,200}?\bFROM\b", re.IGNORECASE)),
]

# Patterns that can trigger a security exception (external services, write-back,
# uploads, agentic actions). Many are legitimate in a Databricks App (the OpenAI
# client is often pointed at a Databricks serving endpoint), so they are leads
# for the reviewer to verify, not findings.
_EXCEPTION_LEAD_PATTERNS: List[Tuple[str, Pattern]] = [
    ("outbound HTTP call", re.compile(
        r"\b(requests|httpx|aiohttp|urllib\.request)\.(get|post|put|patch|delete|request|urlopen)\(|"
        r"\baxios\b|\bfetch\(\s*['\"`]https?://")),
    ("external URL", re.compile(
        r"https?://(?![\w.-]*(databricks|localhost|127\.0\.0\.1|0\.0\.0\.0))[\w-]+(\.[\w-]+)+")),
    ("AI provider SDK", re.compile(
        r"^\s*(import|from)\s+(openai|anthropic|google\.generativeai|google\.genai|cohere|mistralai|groq)\b|"
        r"['\"](openai|@anthropic-ai/sdk|@google/generative-ai)['\"]")),
    ("write-back SQL", re.compile(
        r"\b(INSERT\s+(INTO|OVERWRITE)|UPDATE\s+[\w.`]+\s+SET|MERGE\s+INTO|DELETE\s+FROM|"
        r"CREATE\s+(OR\s+REPLACE\s+)?TABLE|DROP\s+TABLE|TRUNCATE\s+TABLE)\b", re.IGNORECASE)),
    ("file upload handling", re.compile(
        r"\bfile_uploader\(|\bUploadFile\b|\bmulter\b|request\.files\b|dcc\.Upload\b|gr\.(File|UploadButton)\b")),
    ("LLM tool calling", re.compile(
        r"\btool_choice\b|\bbind_tools\(|\bAgentExecutor\b|create_react_agent|\btools\s*=\s*\[")),
]

# Formats specific enough that a hit is almost certainly a real credential.
_SECRET_PATTERNS: List[Tuple[str, Pattern]] = [
    ("Databricks personal access token", re.compile(r"\bdapi[0-9a-f]{32}(-\d)?\b")),
    ("GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b")),
    ("AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("LLM provider API key", re.compile(r"\bsk-(ant-|proj-)?[A-Za-z0-9_-]{32,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
]

# A literal assigned to something secret-named. Noisier, so it's a concern
# the reviewer verifies rather than a blocker.
_GENERIC_SECRET = re.compile(
    r"""(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|client[_-]?secret|access[_-]?key)\b"""
    r"""\s*[:=]\s*["']([^"'\s]{12,})["']"""
)
_PLACEHOLDER = re.compile(
    r"(?i)(your[-_]|xxx|changeme|example|placeholder|dummy|redacted|<|\$\{|\{\{|os\.environ|getenv|process\.env)"
)
_SECRET_NAME = re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key|private[_-]?key|credential)")

# Text addressed to an AI reviewer. Beyond trying to steer the verdict, it can
# get the model call refused by a gateway guardrail, leaving only these checks.
_REVIEWER_DIRECTED = re.compile(
    r"(?i)(ignore (all |any )?(previous|prior|above) (instructions|rules)|"
    r"(note|message|instructions?) (to|for) (the )?(ai|llm|gpt|claude|reviewer|security review)|"
    r"(ai|llm) reviewer|pre-?approved by (the )?security|recommendation\s*[=:]\s*['\"]?approve)"
)

_COMMENT_LINE = re.compile(r"^\s*(#|//|--|\*|/\*)")

# SP patterns only count in files that use Databricks at all (``.authenticate``
# and ``credentials_provider`` are generic names).
_DATABRICKS_FILE = re.compile(r"databricks", re.IGNORECASE)

_DATA_RESOURCE_KEY = re.compile(r"warehouse|volume|table|genie|catalog", re.IGNORECASE)


@dataclass
class PreScan:
    identity: str = "no_databricks_access"
    obo_signals: List[Signal] = field(default_factory=list)
    sp_signals: List[Signal] = field(default_factory=list)
    data_signals: List[Signal] = field(default_factory=list)
    exception_leads: List[Signal] = field(default_factory=list)
    declared_resources: List[Dict[str, Any]] = field(default_factory=list)
    user_api_scopes: List[str] = field(default_factory=list)
    app_config_files: List[str] = field(default_factory=list)
    entrypoint_files: List[str] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity": self.identity,
            "obo_signals": [s.to_dict() for s in self.obo_signals],
            "sp_signals": [s.to_dict() for s in self.sp_signals],
            "data_signals": [s.to_dict() for s in self.data_signals],
            "exception_leads": [s.to_dict() for s in self.exception_leads],
            "declared_resources": self.declared_resources,
            "user_api_scopes": self.user_api_scopes,
            "app_config_files": self.app_config_files,
            "entrypoint_files": self.entrypoint_files,
        }


def redact(value: str) -> str:
    """Show just enough of a secret to find it, never the secret itself."""
    value = value.strip()
    return (value[:4] + "…" + f"({len(value)} chars)") if len(value) > 4 else "…"


def redact_secrets(text: str) -> str:
    """``text`` with any recognizable credential redacted (snippets are stored and shown)."""
    for _, pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: redact(m.group(0)), text)
    return _GENERIC_SECRET.sub(lambda m: m.group(0).replace(m.group(2), redact(m.group(2))), text)


def _ext(path: str) -> str:
    return posixpath.splitext(path)[1].lower()


def _scan_lines(
    files: Dict[str, str],
    patterns: List[Tuple[str, Pattern]],
    code_only: bool,
    file_gate: Optional[Pattern] = None,
    limit: int = 25,
) -> List[Signal]:
    out: List[Signal] = []
    seen = set()
    for path in sorted(files):
        if code_only and _ext(path) not in CODE_EXTENSIONS:
            continue
        if file_gate is not None and not file_gate.search(files[path]):
            continue
        for lineno, line in enumerate(files[path].splitlines(), start=1):
            if code_only and _COMMENT_LINE.match(line):
                continue
            for kind, pattern in patterns:
                if pattern.search(line) and (path, kind) not in seen:
                    seen.add((path, kind))
                    out.append(Signal(
                        kind=kind, file=path, line=lineno, snippet=redact_secrets(line.strip())[:160],
                    ))
                    if len(out) >= limit:
                        return out
    return out


def _str(value: Any) -> Optional[str]:
    """YAML scalars can be dates, numbers or bools; results must stay JSON-safe."""
    return None if value is None else str(value)[:200]


def _as_list(value: Any) -> List[Any]:
    """A YAML field that should be a list; a lone scalar counts as one item."""
    if isinstance(value, list):
        return value
    return [] if value is None or isinstance(value, dict) else [value]


def _load_yaml(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def _app_yaml_facts(path: str, doc: Any, scan: PreScan) -> None:
    """Facts from an ``app.yaml``: env literals, resource references, entrypoint."""
    if not isinstance(doc, dict):
        return
    scan.app_config_files.append(path)
    base = posixpath.dirname(path)

    command = doc.get("command")
    tokens = command.split() if isinstance(command, str) else _as_list(command)
    for token in tokens:
        if isinstance(token, str) and _ext(token) in CODE_EXTENSIONS:
            scan.entrypoint_files.append(posixpath.normpath(posixpath.join(base, token)))

    for entry in _as_list(doc.get("env")):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        if "valueFrom" in entry:
            # Resources attached in the Apps UI only show up here, by key, so
            # the key name is the best evidence of what they are.
            ref = str(entry.get("valueFrom"))[:200]
            scan.declared_resources.append({
                "file": path, "name": ref, "type": "referenced", "via_env": name[:200],
                "data_bearing": bool(_DATA_RESOURCE_KEY.search(ref)),
            })
        elif "value" in entry and _SECRET_NAME.search(name):
            value = str(entry.get("value") or "")
            if value and not _PLACEHOLDER.search(value):
                scan.findings.append(Finding(
                    severity="blocker", category="secrets",
                    title=f"Secret-looking value set inline for {name}",
                    why_it_matters="Anything in app.yaml is readable by everyone with repo access.",
                    remediation="Rotate the value, add it as a secret resource on the app, and "
                                "reference it with valueFrom.",
                    file=path, evidence=f"{name}: {redact(value)}",
                ))


def _bundle_app_facts(path: str, doc: Any, scan: PreScan) -> None:
    """Facts from a bundle file's ``resources.apps``: declared resources, OBO scopes."""
    if not isinstance(doc, dict):
        return
    apps = ((doc.get("resources") or {}) if isinstance(doc.get("resources"), dict) else {}).get("apps")
    if not isinstance(apps, dict):
        return
    scan.app_config_files.append(path)
    for app_name, app in apps.items():
        if not isinstance(app, dict):
            continue
        for scope in _as_list(app.get("user_api_scopes")):
            scan.user_api_scopes.append(_str(scope))
        for res in _as_list(app.get("resources")):
            if not isinstance(res, dict):
                continue
            rtype = next((k for k in res if k in KNOWN_RESOURCES), None)
            spec = res.get(rtype) if rtype else None
            scan.declared_resources.append({
                "file": path, "app": _str(app_name), "name": _str(res.get("name")),
                "type": rtype or "other",
                "data_bearing": rtype in DATA_BEARING_RESOURCES,
                "permission": _str(spec.get("permission")) if isinstance(spec, dict) else None,
            })


def _find_secrets(files: Dict[str, str], scan: PreScan) -> None:
    for path in sorted(files):
        name = posixpath.basename(path)
        is_env_file = name == ".env" or (
            name.startswith(".env.") and not name.endswith((".example", ".sample", ".template"))
        )
        for lineno, line in enumerate(files[path].splitlines(), start=1):
            for kind, pattern in _SECRET_PATTERNS:
                m = pattern.search(line)
                if m:
                    scan.findings.append(Finding(
                        severity="blocker", category="secrets", title=f"Committed {kind}",
                        why_it_matters="Anyone with access to the repository (and its history) can use it.",
                        remediation="Revoke and rotate it, remove it from the code and git history, and "
                                    "load it from a secret scope or secret resource.",
                        file=path, line=lineno, evidence=redact(m.group(0)),
                    ))
            m = _GENERIC_SECRET.search(line)
            if m and not _PLACEHOLDER.search(line):
                scan.findings.append(Finding(
                    severity="blocker" if is_env_file else "concern", category="secrets",
                    title=f"Possible hardcoded {m.group(1).lower()}",
                    why_it_matters="A literal credential in source is readable by anyone with repo access.",
                    remediation="If it's real: rotate it and load it from a secret scope instead.",
                    file=path, line=lineno, evidence=f"{m.group(1)} = {redact(m.group(2))}",
                ))
        if len(scan.findings) > 40:
            return


def _find_reviewer_directed_text(files: Dict[str, str], scan: PreScan) -> None:
    for path in sorted(files):
        for lineno, line in enumerate(files[path].splitlines(), start=1):
            if _REVIEWER_DIRECTED.search(line):
                scan.findings.append(Finding(
                    severity="concern", category="prompt_injection",
                    title="Text addressed to an AI reviewer",
                    why_it_matters="It tries to steer the automated review, and can cause the model "
                                   "review to be refused. Judge the code, not the claim.",
                    remediation="Remove it; approval claims belong in the request, not the code.",
                    file=path, line=lineno, evidence=redact_secrets(line.strip())[:160],
                ))
                return


def _first(signals: Iterable[Signal]) -> Optional[Signal]:
    return next(iter(signals), None)


def touches_data(scan: PreScan) -> bool:
    return bool(scan.data_signals or any(r.get("data_bearing") for r in scan.declared_resources))


def classify_identity(scan: PreScan) -> str:
    has_obo = bool(scan.obo_signals or scan.user_api_scopes)
    has_sp = bool(scan.sp_signals)
    if has_sp and has_obo:
        # Usually the SP reaches app resources while queries use the user's
        # token; which one a given query uses is the reviewer's call.
        return "mixed"
    if has_sp:
        return "sp_with_data_access" if touches_data(scan) else "sp_app_resources_only"
    if has_obo:
        return "obo_only"
    return "unknown" if touches_data(scan) else "no_databricks_access"


def run_prescan(files: Dict[str, str]) -> PreScan:
    scan = PreScan()
    for path in sorted(files):
        name = posixpath.basename(path)
        if name in ("app.yaml", "app.yml"):
            _app_yaml_facts(path, _load_yaml(files[path]), scan)
        elif _ext(path) in (".yml", ".yaml") and "apps" in files[path]:
            _bundle_app_facts(path, _load_yaml(files[path]), scan)

    scan.obo_signals = _scan_lines(files, _OBO_PATTERNS, code_only=True)
    scan.sp_signals = _scan_lines(files, _SP_PATTERNS, code_only=True, file_gate=_DATABRICKS_FILE)
    scan.data_signals = _scan_lines(files, _DATA_PATTERNS, code_only=True)
    scan.exception_leads = _scan_lines(files, _EXCEPTION_LEAD_PATTERNS, code_only=True, limit=40)
    _find_secrets(files, scan)
    _find_reviewer_directed_text(files, scan)
    scan.identity = classify_identity(scan)

    sp, data = _first(scan.sp_signals), _first(scan.data_signals)
    if scan.identity == "sp_with_data_access":
        where = sp or data
        scan.findings.insert(0, Finding(
            severity="blocker", category="identity",
            title="The app's service principal appears to read data",
            why_it_matters="Data read as the app's own identity is shown to every user of the app, "
                           "whether or not they have access to it themselves.",
            remediation="Query with the signed-in user's token (on-behalf-of-user) instead, or get a "
                        "security exception for service principal access that bypasses OBO.",
            file=where.file if where else None, line=where.line if where else None,
            evidence=(sp.snippet if sp else "data-bearing resources declared for the app"),
        ))
    elif scan.identity == "mixed":
        scan.findings.insert(0, Finding(
            severity="minor", category="identity",
            title="Uses the service principal alongside the signed-in user's token",
            why_it_matters="Normal when the service principal only reaches the app's own resources "
                           "(its Lakebase database, a serving endpoint). If it also queries governed "
                           "data, users can see data they don't have access to.",
            remediation="Confirm governed-data queries use the user's token.",
            file=sp.file if sp else None, line=sp.line if sp else None,
            evidence=sp.snippet if sp else "",
        ))
    elif scan.identity == "unknown":
        scan.findings.insert(0, Finding(
            severity="concern", category="identity",
            title="Reads data, but which identity it uses couldn't be determined",
            why_it_matters="Whether users see only their own data depends on this.",
            remediation="Confirm with the app owner how data queries authenticate.",
            file=data.file if data else None, line=data.line if data else None,
            evidence=data.snippet if data else "",
        ))

    if not scan.app_config_files:
        scan.findings.append(Finding(
            severity="concern", category="platform",
            title="No app.yaml or bundle app definition found",
            why_it_matters="Without it, the app's declared resources and permissions can't be checked.",
        ))
    return scan
