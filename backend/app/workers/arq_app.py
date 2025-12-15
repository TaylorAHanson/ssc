"""
ARQ application setup for async task processing.
"""
try:
    from arq.connections import RedisSettings
    ARQ_AVAILABLE = True
except ImportError:
    ARQ_AVAILABLE = False
    # Stub for when ARQ is not available
    class RedisSettings:
        def __init__(self, *args, **kwargs):
            pass

from app.core.config import settings

# Only import tasks if ARQ is available
if ARQ_AVAILABLE:
    try:
        from app.workers.tasks.state_transitions import (
            process_state_transition,
            provision_workspace,
            notify_failure
        )
        TASKS_AVAILABLE = True
    except ImportError:
        TASKS_AVAILABLE = False
else:
    TASKS_AVAILABLE = False


def get_redis_settings():
    """Get Redis settings for ARQ."""
    if not ARQ_AVAILABLE:
        raise NotImplementedError("ARQ/Redis not installed. Install 'arq' and 'redis' packages to enable async task processing.")
    return RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        database=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None
    )


# ARQ worker configuration (only if ARQ is available)
if ARQ_AVAILABLE and TASKS_AVAILABLE:
    class WorkerSettings:
        """ARQ worker settings."""
        functions = [
            process_state_transition,
            provision_workspace,
            notify_failure,
        ]
        redis_settings = get_redis_settings()
else:
    # Stub class when ARQ is not available
    class WorkerSettings:
        """ARQ worker settings (stub - ARQ not available)."""
        functions = []
        redis_settings = None