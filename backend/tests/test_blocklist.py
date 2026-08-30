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

    def test_list_active(self):
        bl = Blocklist()
        bl.block("u1", blocked_by="s", reason="r1")
        bl.block("u2", blocked_by="s", reason="r2")
        blocks = bl.list_active()
        assert {b["external_id"] for b in blocks} == {"u1", "u2"}
        assert all(b["active"] for b in blocks)

    def test_list_active_after_unblock_empty(self):
        bl = Blocklist()
        bl.block("u1", blocked_by="s")
        bl.unblock("u1")
        assert bl.list_active() == []
