"""Тесты rate limiter массовой перегенерации (скользящее окно)."""
import pytest

from app.services.rate_limiter import RateLimitExceeded, RateLimiter


class TestRateLimiter:
    def test_allows_within_limits(self):
        rl = RateLimiter()
        for _ in range(3):
            rl.check_bulk_regenerate(
                "u1", 10, max_ops_per_hour=3, max_docs_per_hour=50
            )

    def test_exceeds_ops_per_hour(self):
        rl = RateLimiter()
        rl.check_bulk_regenerate("u1", 1, max_ops_per_hour=1, max_docs_per_hour=100)
        with pytest.raises(RateLimitExceeded):
            rl.check_bulk_regenerate("u1", 1, max_ops_per_hour=1, max_docs_per_hour=100)

    def test_exceeds_docs_per_hour(self):
        rl = RateLimiter()
        rl.check_bulk_regenerate("u1", 30, max_ops_per_hour=10, max_docs_per_hour=50)
        with pytest.raises(RateLimitExceeded):
            rl.check_bulk_regenerate("u1", 30, max_ops_per_hour=10, max_docs_per_hour=50)

    def test_per_user_isolation(self):
        rl = RateLimiter()
        rl.check_bulk_regenerate("u1", 1, max_ops_per_hour=1, max_docs_per_hour=10)
        # Тот же лимит, но другой пользователь — не блокируется.
        rl.check_bulk_regenerate("u2", 1, max_ops_per_hour=1, max_docs_per_hour=10)

    def test_retry_after_present(self):
        rl = RateLimiter()
        rl.check_bulk_regenerate("u1", 1, max_ops_per_hour=1, max_docs_per_hour=10)
        with pytest.raises(RateLimitExceeded) as excinfo:
            rl.check_bulk_regenerate("u1", 1, max_ops_per_hour=1, max_docs_per_hour=10)
        assert excinfo.value.retry_after > 0

    def test_action_limit_is_per_user_and_action(self):
        rl = RateLimiter()
        rl.check_action("u1", "chat", max_requests=1)
        with pytest.raises(RateLimitExceeded):
            rl.check_action("u1", "chat", max_requests=1)
        rl.check_action("u1", "search", max_requests=1)
        rl.check_action("u2", "chat", max_requests=1)
