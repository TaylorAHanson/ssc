from fastapi.testclient import TestClient
import pytest
from app.main import app

client = TestClient(app)

def test_profiler_middleware_no_param():
    """Test that requests without profile=true return normal JSON."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert "status" in response.json()

def test_profiler_middleware_with_param():
    """Test that requests with profile=true return HTML profiling output."""
    response = client.get("/health?profile=true")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "pyinstrument" in response.text.lower()
    # Check for common pyinstrument HTML elements or keywords
    assert "program output" in response.text.lower() or "pyinstrument" in response.text.lower()
