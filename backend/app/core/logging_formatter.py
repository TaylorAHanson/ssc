import logging
import json
import uuid
from contextlib import contextmanager
from datetime import datetime
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional
from app.core.config import settings

# Context variables to hold request-scoped information
current_user_email: ContextVar[Optional[str]] = ContextVar("current_user_email", default=None)
current_request_id: ContextVar[Optional[str]] = ContextVar("current_request_id", default=None)
current_endpoint: ContextVar[Optional[str]] = ContextVar("current_endpoint", default=None)
current_method: ContextVar[Optional[str]] = ContextVar("current_method", default=None)
current_client_ip: ContextVar[Optional[str]] = ContextVar("current_client_ip", default=None)
current_user_agent: ContextVar[Optional[str]] = ContextVar("current_user_agent", default=None)
current_correlation_id: ContextVar[Optional[str]] = ContextVar("current_correlation_id", default=None)
# A dict the middleware creates per request and later steps fill in (the caller's app
# roles). It is mutated rather than re-set: sync dependencies run in a worker thread on
# a copy of the context, so a value set there never reaches the middleware, but a change
# to this shared dict does.
current_request_info: ContextVar[Optional[Dict[str, Any]]] = ContextVar("current_request_info", default=None)


def note_request_identity(roles: List[str], role_override: Optional[str] = None) -> None:
    """Record the caller's app roles so every later line of this request carries them."""
    info = current_request_info.get()
    if info is None:
        return
    info["roles"] = sorted(roles or [])
    if role_override:
        info["role_override"] = role_override


@contextmanager
def job_log_context(name: str, run_id: Optional[str] = None) -> Iterator[str]:
    """Tag the lines a background job logs with its name and a run id, as a request is."""
    run = run_id or uuid.uuid4().hex[:8]
    tokens = (
        (current_request_id, current_request_id.set(f"{name}:{run}")),
        (current_endpoint, current_endpoint.set(f"job:{name}")),
        (current_method, current_method.set("JOB")),
    )
    try:
        yield run
    finally:
        for var, token in reversed(tokens):
            var.reset(token)

class AppLoggingFormatter(logging.Formatter):
    """
    Custom logging formatter that outputs JSON records with contextual information.
    """
    def format(self, record):
        # Format the basic message and exception info if present
        message = record.getMessage()
        
        # Determine the timestamp
        if self.usesTime():
            record.asctime = self.formatTime(record, self.datefmt)
        else:
            record.asctime = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]

        # Extract context variables
        email = current_user_email.get()
        endpoint = current_endpoint.get()
        req_id = current_request_id.get()
        method = current_method.get()
        ip = current_client_ip.get()
        agent = current_user_agent.get()
        corr_id = current_correlation_id.get()

        # Build JSON dictionary
        log_record = {
            "timestamp": record.asctime,
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "env": getattr(settings, "ENVIRONMENT", "unknown"),
            "version": getattr(settings, "VERSION", "unknown"),
            "pid": record.process,
            "file": f"{record.filename}:{record.lineno}",
            "req_id": req_id if req_id else "N/A",
            "correlation_id": corr_id if corr_id else "N/A",
            "user": email if email else "System",
            "ip": ip if ip else "N/A",
            "agent": agent if agent else "N/A",
            "method": method if method else "SYS",
            "path": endpoint if endpoint else "Background"
        }
        info = current_request_info.get()
        if info and "roles" in info:
            log_record["roles"] = info["roles"]
            if info.get("role_override"):
                log_record["role_override"] = info["role_override"]
        # Structured fields passed as ``extra={"data": {...}}``, so they can be filtered on
        # instead of parsed out of the message.
        data = getattr(record, "data", None)
        if isinstance(data, dict):
            log_record["data"] = data

        # Add exception info if any
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            log_record["exception"] = record.exc_text

        # Add stack info if any
        if record.stack_info:
            log_record["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(log_record, default=str)

def setup_logging(log_level_str="INFO"):
    """
    Configures the root logger to use the custom AppLoggingFormatter as JSON.
    """
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    formatter = AppLoggingFormatter()
    console_handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)
    
    # Force ALL existing loggers to strip their handlers and propagate to root
    for name, logger_obj in logging.root.manager.loggerDict.items():
        if isinstance(logger_obj, logging.Logger):
            for handler in logger_obj.handlers[:]:
                logger_obj.removeHandler(handler)
            logger_obj.propagate = True

    # httpx logs every request at INFO, and each policy evaluation is a request
    # to the local OPA server, so at INFO they bury the app's own log lines.
    # uvicorn.access duplicates the auth middleware's HTTP line, which also has the
    # duration and the caller's roles.
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(max(log_level, logging.WARNING))
