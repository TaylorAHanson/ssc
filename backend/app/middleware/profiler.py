from fastapi import Request
from fastapi.responses import HTMLResponse
from pyinstrument import Profiler
from starlette.middleware.base import BaseHTTPMiddleware
import logging

logger = logging.getLogger(__name__)

class PyinstrumentMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.query_params.get("profile") == "true":
            profiler = Profiler(interval=0.001)
            profiler.start()
            
            try:
                await call_next(request)
            finally:
                profiler.stop()
                return HTMLResponse(profiler.output_html())
        
        return await call_next(request)
