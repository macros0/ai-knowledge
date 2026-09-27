"""Opt-in live acceptance, synthetic inputs only; never writes knowledge records.

Run from backend: python scripts/probe_local_qwen.py --output <report.json>
The selected runtime environment must already configure LLM_PROFILE=local_qwen.
"""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.prompts.store import get_store
from app.services import gen_quality
from app.services.llm_client import LLMClient
from app.services.llm_profiles import request_scope
from app.services.okf_generator import OKFGenerator


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if settings.llm_profile != "local_qwen":
        parser.error("requires the explicit local_qwen profile")
    rows = []

    def record(name, action, check):
        started = time.monotonic()
        row = {"name": name}
        try:
            value = action()
            row.update(result=value, passed=bool(check(value)))
        except Exception as exc:
            row.update(error=type(exc).__name__, passed=False)
        row["seconds"] = round(time.monotonic() - started, 2)
        rows.append(row)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in row.items() if k != "result"}, ensure_ascii=False), flush=True)

    client = LLMClient(interactive=True)
    system = get_store().get("chat_system")
    facts = "[1] Регламент ZQW_TEST. Статус READY разрешает отправку. Статус HOLD запрещает отправку."
    def answer(context, question):
        first = []
        started = time.monotonic()
        with request_scope(on_text=lambda text: first.append(time.monotonic()) if not first else None):
            text = client.chat(system, f"Контекст:\n{context}\n\nВопрос: {question}")
        return {"text": text, "first_token_seconds": round(first[0] - started, 2) if first else None}
    record("rag_fact", lambda: answer(facts, "При каком статусе ZQW_TEST разрешает отправку?"),
           lambda v: "READY" in v["text"] and "[1]" in v["text"] and "<think>" not in v["text"])
    long_context = facts + "\n" + "\n".join(
        f"[{i}] Архивная справка {i}: поле ZARCH{i} хранит описание архивной записи." for i in range(2, 160))
    record("rag_long", lambda: answer(long_context, "При каком статусе ZQW_TEST запрещает отправку?"),
           lambda v: "HOLD" in v["text"] and "[1]" in v["text"])
    if not args.quick:
        record("rag_missing_fact", lambda: answer(facts, "Какой размер штрафа за задержку отправки?"),
               lambda v: any(s in v["text"].lower() for s in ("не указан", "нет информац", "не содержит", "недостаточно", "отсутствует информация")))
        record("translation", lambda: LLMClient().chat_json(
            "Translate each Russian term into English. Return a JSON array of strings in input order.",
            '["Статус отправки", "Табельный номер"]', task="translation"),
            lambda v: len(v) == 2 and all(isinstance(x, str) and x.strip() for x in v))
        record("development", lambda: LLMClient().chat_json(
            "Extract dev_number, dev_name, module from the document. Return a JSON object.",
            "Разработка 12345. Название: Проверка статуса. Модуль PY.", task="development"),
            lambda v: v["dev_number"] == "12345" and v["module"] == "PY")
        generator = OKFGenerator()
        for name, source in [
            ("okf_procedure", "# Отправка ZQW_TEST\n\nСтатус READY разрешает отправку. Статус HOLD запрещает отправку.\n\n"
             "Перед отправкой выполните CHECK_STATUS. При результате 0 выполните SEND_DOCUMENT. "
             "При результате 8 сохраните ошибку и завершите обработку.\n\n"
             "```abap\nIF lv_status = 'READY'.\n  PERFORM send_document.\nENDIF.\n```"),
            ("okf_mail", "# Письмо\n\nОт: Ирина\nТема: Решение по ZQW_TEST\n\n"
             "Согласовано: повторная отправка разрешена только после проверки CHECK_STATUS. "
             "Срок внедрения в письме не указан."),
            ("okf_table", "# Поля сообщения ZQW_TEST\n\n| Поле | Тип | Описание |\n|---|---|---|\n"
             "| STATUS | CHAR5 | Статус отправки |\n| DOC_ID | CHAR20 | Идентификатор |\n"
             "| READY_AT | DATS | Дата готовности |\n| ERROR | CHAR80 | Текст ошибки |\n"
             "| RETRIES | INT4 | Число повторов |\n| OWNER | CHAR12 | Ответственный |"),
        ]:
            def generate(source=source, name=name):
                gen_quality.drain()
                concepts = generator.generate_chunk(source, name + (".eml" if name == "okf_mail" else ".md"),
                                                     1, 1, doc_id="local-acceptance", source_is_mail=name == "okf_mail")
                return {"concepts": [c.model_dump(mode="json") for c in concepts],
                        "degradation": gen_quality.drain(),
                        "quotes_exact": all(q in source for c in concepts for q in c.source_quotes)}
            record(name, generate, lambda v: bool(v["concepts"]) and v["quotes_exact"] and not v["degradation"])
    if not all(row["passed"] for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
