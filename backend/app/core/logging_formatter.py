import logging
import json
from datetime import datetime
from contextvars import ContextVar
from typing import Optional
from app.core.config import settings

# Context variables to hold request-scoped information
current_user_email: ContextVar[Optional[str]] = ContextVar("current_user_email", default=None)
current_request_id: ContextVar[Optional[str]] = ContextVar("current_request_id", default=None)
current_endpoint: ContextVar[Optional[str]] = ContextVar("current_endpoint", default=None)
current_method: ContextVar[Optional[str]] = ContextVar("current_method", default=None)
current_client_ip: ContextVar[Optional[str]] = ContextVar("current_client_ip", default=None)
current_user_agent: ContextVar[Optional[str]] = ContextVar("current_user_agent", default=None)
current_correlation_id: ContextVar[Optional[str]] = ContextVar("current_correlation_id", default=None)

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

        # Add exception info if any
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            log_record["exception"] = record.exc_text

        # Add stack info if any
        if record.stack_info:
            log_record["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(log_record)

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
