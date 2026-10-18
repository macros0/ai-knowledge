"""One real tag translation write path; only an explicitly disposable database."""
import json
import os
from time import perf_counter
from urllib.parse import urlparse

url = os.environ["REFERENCE_TEST_POSTGRES_URL"]
if not urlparse(url).path.rsplit("/", 1)[-1].startswith("test_reference_"):
    raise RuntimeError("Explicit test database required")
os.environ.update(DATABASE_URL=url, AUTH_PROVIDER="disabled", REFERENCE_EDIT_LOCKS_ENABLED="false",
                  QDRANT_URL="http://127.0.0.1:9")
from app.config import get_settings
get_settings.cache_clear()
from app.db.session import configure_for_tests
configure_for_tests(url)
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
durations = []
for index in range(120):
    # The read is outside the measured write and supplies the candidate's CAS.
    tag = client.get("/api/tags").json()["tags"][0]
    payload = {"locale": "en", "text": f"Measured translation {index}"}
    if "reference_revision" in tag:
        payload["expected_reference_revision"] = tag["reference_revision"]
    started = perf_counter()
    response = client.patch(f'/api/tags/{tag["id"]}/translations/en', json=payload)
    elapsed = (perf_counter() - started) * 1000
    assert response.status_code == 200, response.status_code
    if index >= 20:
        durations.append(elapsed)
print(json.dumps({"scope": "ASGI_TestClient_tag_translation_write_CAS_when_supported", "samples": len(durations),
                  "p50_ms": sorted(durations)[49], "p95_ms": sorted(durations)[94]}))
