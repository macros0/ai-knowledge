from __future__ import annotations

import httpx
import pytest

from scripts.wait_for_qdrant import QdrantReadinessError, wait_for_qdrant


class _Response:
    status_code = 200


def test_wait_for_qdrant_retries_transient_failure_without_mutating_service():
    attempts = 0

    def request(url, *, headers, timeout, verify):
        nonlocal attempts
        assert url == "http://qdrant:6333/collections"
        assert headers == {"api-key": "test-key"}
        assert timeout == 2
        assert verify == "/etc/customer-ca.pem"
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("not ready")
        return _Response()

    wait_for_qdrant(
        "http://qdrant:6333",
        timeout_seconds=5,
        interval_seconds=0,
        api_key="test-key",
        ca_bundle="/etc/customer-ca.pem",
        request=request,
        monotonic=lambda: 0,
        sleep=lambda _seconds: None,
    )
    assert attempts == 2


def test_wait_for_qdrant_has_bounded_failure():
    with pytest.raises(QdrantReadinessError, match="not ready"):
        wait_for_qdrant(
            "http://qdrant:6333",
            timeout_seconds=0,
            request=lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.ConnectError("not ready")),
        )
