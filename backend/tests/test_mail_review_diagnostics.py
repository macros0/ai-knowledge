"""Parser diagnostics must survive publication and the document problem filter."""
from email.message import EmailMessage

import pytest

from tests.test_generation_pipeline import DOC_ID  # noqa: F401
from tests.test_generation_pipeline import pipeline_env as pipeline_env


@pytest.mark.parametrize("case,problem,warning", [
    ("decode", "mail_text_partial_result", "mail_decode_recovered"),
    ("alternative", "mail_text_partial_result", "mail_alternative_mismatch"),
    ("size", "attachment_partial_result", "attachment_size_exceeded"),
])
def test_review_parser_warning_is_published_with_problem(pipeline_env, monkeypatch, case, problem, warning):
    pipeline, source, _write = pipeline_env
    if case == "decode":
        source.write_bytes(b"From: sender@example.test\nContent-Type: text/plain; charset=utf-8\n\nDecision: 1\xff8 days.")
    else:
        message = EmailMessage()
        message["From"] = "sender@example.test"
        message.set_content("Decision: 23 days.")
        if case == "alternative":
            message.add_alternative("<p>Decision: 47 days.</p>", subtype="html")
        else:
            from docparser import embedded

            # Below MIME's own admission threshold, above the central payload
            # threshold: exercise the previously unreported late rejection.
            monkeypatch.setattr(embedded, "MAX_ATTACHMENT_PAYLOAD", 4)
            message.add_attachment(b"12345", maintype="application", subtype="octet-stream", filename="proof.bin")
        source.write_bytes(message.as_bytes())
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    document = pipeline.registry.get(DOC_ID)
    assert document["status"] == "done"
    assert document["problem"] == problem
    assert any(item["code"] == warning for item in document["parse_warnings"])
