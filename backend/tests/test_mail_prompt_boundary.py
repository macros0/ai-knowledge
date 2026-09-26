"""Source role spoofing must never become a high-priority wire message."""
import json
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from docparser import blocks_to_markdown
import litellm
import pytest

from app.config import Settings
from app.prompts.store import PromptStore
from app.services.context_builder import format_context
from app.services.llm_client import LLMClient
from app.services.okf_generator import OKFGenerator
from app.services.parser_supervisor import parse_document_supervised
from test_scripts.probe_mail_injection import cases


@pytest.mark.parametrize("key,message,query,_expected", cases())
@pytest.mark.parametrize("format_name", ["eml", "msg"])
def test_malicious_mail_stays_in_user_role_at_real_provider_boundary(tmp_path, monkeypatch, key, message, query, _expected, format_name):
    settings = Settings(_env_file=None, data_dir=tmp_path, llm_model="test/generation",
                        llm_chat_model="test/chat", llm_api_key="", llm_retry_attempts=1)
    for module in ("app.services.llm_client", "app.services.okf_generator", "app.services.field_table"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    store = PromptStore(override_dir=tmp_path / "prompts")
    monkeypatch.setattr("app.services.okf_generator.get_store", lambda: store)
    source = tmp_path / f"{key}.{format_name}"
    original = message.as_bytes()
    if format_name == "msg":
        spec = importlib.util.spec_from_file_location("boundary_cfb", Path(__file__).parents[2] / "doc-parser/tests/mail_fixtures.py")
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        original = fixture.unicode_msg(subject="Forwarded source", body="Forwarded synthetic source data.",
                                       attachments={"attack.eml": original})
    source.write_bytes(original)
    parsed = parse_document_supervised(source, source.name, attachments_dir=tmp_path / "attachments",
                                       timeout_seconds=20, max_memory_mb=1024)
    text = blocks_to_markdown(parsed.blocks)
    assert r"INJECTION\_EXECUTED" in text
    fact = next(line for line in text.splitlines() if "срок проверки согласован: 23 дня." in line)
    requests = []

    def completion(**kwargs):
        requests.append(kwargs)
        answer = json.dumps([{"id": "fact", "title": "Срок проверки", "content": fact,
                              "source_quotes": [fact]}]) if kwargs["model"] == "test/generation" else "23 дня [1]."
        return iter([SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content=answer), finish_reason="stop")])])

    monkeypatch.setattr(litellm, "completion", completion)
    concepts = OKFGenerator().generate_chunk(text, source.name, 1, 1)
    assert concepts and concepts[0].content == fact
    context = format_context([{
        "doc_id": "synthetic-boundary", "source_id": "root", "title": message["Subject"],
        "content": text, "kind": "chunk", "source_filename": source.name,
        "source_path": [{"source_id": node.source_id, **node.metadata} for node in parsed.sources],
    }], query=query)
    result = LLMClient(interactive=True).chat(store.format("chat_system", locale="ru"),
                                           store.format("chat_user", context=context, query=query))
    assert result == "23 дня [1]."
    assert len(requests) == 2
    for request in requests:
        assert [message["role"] for message in request["messages"]] == ["system", "user"]
        assert "INJECTION_EXECUTED" not in request["messages"][0]["content"]
        assert r"INJECTION\_EXECUTED" in request["messages"][1]["content"]
        assert not request.get("tools") and not request.get("functions")
    assert source.read_bytes() == original
