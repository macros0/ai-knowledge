"""Тесты блоклиста пользователей (user_blocks)."""
from app.services.blocklist import Blocklist


class TestBlocklist:
    def test_block_and_check(self):
        bl = Blocklist()
        assert not bl.is_blocked("user-x")
        bl.block("user-x", blocked_by="demo.security", reason="инцидент")
        assert bl.is_blocked("user-x")

    def test_unblock(self):
        bl = Blocklist()
        bl.block("user-x", blocked_by="demo.security")
        assert bl.unblock("user-x") == 1
        assert not bl.is_blocked("user-x")

    def test_unblock_no_active_blocks(self):
        bl = Blocklist()
        assert bl.unblock("nobody") == 0

    def test_anonymous_never_blocked(self):
        bl = Blocklist()
        assert not bl.is_blocked("anonymous")
