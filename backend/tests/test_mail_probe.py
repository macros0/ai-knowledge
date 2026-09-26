"""Acceptance inputs must be byte-identical between baseline and after runs."""
from test_scripts.probe_mail_ingestion import _mail_bytes


def test_mail_profile_corpus_is_reproducible():
    first = [_mail_bytes(index) for index in range(3)]
    second = [_mail_bytes(index) for index in range(3)]
    assert first == second
    assert len(set(first)) == 3
