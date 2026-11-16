"""CORS origins: the configured list, plus the extra origins ./dev.sh adds when it moves the frontend's port."""
from app.core.config import Settings


def _settings(**env) -> Settings:
    return Settings(**env)


def test_extra_origins_are_added_to_the_configured_ones():
    s = _settings(CORS_ORIGINS="http://localhost:5173", CORS_EXTRA_ORIGINS="http://localhost:5174, http://127.0.0.1:5174")
    assert s.cors_origins_list == ["http://localhost:5173", "http://localhost:5174", "http://127.0.0.1:5174"]


def test_an_extra_that_is_already_allowed_is_not_repeated():
    s = _settings(CORS_ORIGINS="http://localhost:5173", CORS_EXTRA_ORIGINS="http://localhost:5173")
    assert s.cors_origins_list == ["http://localhost:5173"]


def test_with_no_extras_the_configured_origins_are_unchanged():
    s = _settings(CORS_ORIGINS='["http://localhost:5173","http://localhost:3000"]', CORS_EXTRA_ORIGINS="")
    assert s.cors_origins_list == ["http://localhost:5173", "http://localhost:3000"]
