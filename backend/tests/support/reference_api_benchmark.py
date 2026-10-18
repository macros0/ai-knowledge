"""Read-only API timing in a dedicated test_reference_* PostgreSQL database."""
import json
import os
from time import perf_counter
from urllib.parse import urlparse

url = os.environ["REFERENCE_TEST_POSTGRES_URL"]
if not urlparse(url).path.rsplit("/", 1)[-1].startswith("test_reference_"):
    raise RuntimeError("Explicit test database required")
os.environ["DATABASE_URL"] = url
os.environ["AUTH_PROVIDER"] = "disabled"
os.environ["QDRANT_URL"] = "http://127.0.0.1:9"
from app.config import get_settings
get_settings.cache_clear()
from app.db.session import configure_for_tests
configure_for_tests(url)
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)  # No lifespan/startup/backfill; exercise the real ASGI route only.
durations = []
for index in range(220):
    started = perf_counter()
    response = client.get("/api/tags")
    elapsed = (perf_counter() - started) * 1000
    assert response.status_code == 200
    assert len(response.json()["tags"]) == 50
    if index >= 20:
        durations.append(elapsed)
print(json.dumps({"scope": "ASGI_TestClient_tags_50", "samples": len(durations),
                  "p50_ms": sorted(durations)[99], "p95_ms": sorted(durations)[189]}))
