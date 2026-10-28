"""Page views and errors reported by the browser, written to the server log.

Gives the server log the half of a session it can't see on its own: which screens a
person moved between (routing happens in the browser) and what broke on their side.
Each line carries the user and their app roles like any other request. Switched off
under Admin -> Settings -> Infrastructure (``logging.client_events``).
"""
import logging
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field, field_validator

from app.api.deps import get_current_user
from app.core.config import _yaml_config
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_EVENTS = 20
_LIMITS = {"path": 300, "message": 1000, "source": 50, "stack": 3000, "request_id": 64, "app_version": 40}


def client_events_enabled() -> bool:
    cfg = _yaml_config.get("logging") or {}
    return bool(cfg.get("client_events", True)) if isinstance(cfg, dict) else True


class ClientEvent(BaseModel):
    type: Literal["page_view", "error"]
    path: str = ""
    message: Optional[str] = None
    # Where an error was caught: "react", "window.onerror" or "unhandledrejection".
    source: Optional[str] = None
    stack: Optional[str] = None
    # The X-Request-ID of the failed call, when the error came from one.
    request_id: Optional[str] = None
    app_version: Optional[str] = None

    @field_validator("path", "message", "source", "stack", "request_id", "app_version", mode="before")
    @classmethod
    def _clip(cls, value: Any, info) -> Any:
        # Browser input is untrusted and unbounded: cut it to size rather than reject it,
        # so one oversized stack trace doesn't lose the rest of the batch.
        if value is None:
            return None
        text = str(value)
        if info.field_name == "path":
            text = text.split("?", 1)[0].split("#", 1)[0]
        return text[: _LIMITS[info.field_name]]


class ClientEventBatch(BaseModel):
    events: List[ClientEvent] = Field(default_factory=list)


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
def record_client_events(
    batch: ClientEventBatch,
    current_user: User = Depends(get_current_user),
) -> Response:
    if not client_events_enabled():
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    for ev in batch.events[:MAX_EVENTS]:
        data = {"client_path": ev.path, "app_version": ev.app_version}
        if ev.type == "page_view":
            logger.info("PAGE_VIEW %s", ev.path, extra={"data": data})
            continue
        data.update({"source": ev.source, "error": ev.message, "stack": ev.stack, "failed_request_id": ev.request_id})
        logger.warning(
            "CLIENT_ERROR (%s) on %s: %s", ev.source or "browser", ev.path, ev.message,
            extra={"data": data},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
