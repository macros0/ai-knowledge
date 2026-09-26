"""Compare chat models on identical saved synthetic mail contexts and controls.

This calls configured credentials but never changes application settings/files.
Controls modify context explicitly; they are not an upload/pipeline acceptance.
Semantic judgments remain manual, separate from successful API completion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def build_cases(baseline):
    cases = {}
    for key in ("s03", "s04", "s06", "s07"):
        source = baseline["cases"][key]
        cases[key] = {**source["prompts"][0], "expected": source["acceptance"], "kind": "unchanged"}
    changes = (
        ("s04_revoked", "s04", [(
            "Для проекта Орбита-42 продление лицензии не согласовано.",
            "Для проекта Орбита-42 продление лицензии не согласовано. Отменяю согласование, направленное Анной 24 сентября 2026 года.",
        )], "Явная отмена первого согласования Борисом 25 сентября; назвать оба решения/отправителей."),
        ("s06_signed", "s06", [
            ("Требуется 22 дня.", "Ольга: Требуется 22 дня."),
            ("Достаточно электронного акта.", "Ольга: Достаточно электронного акта."),
        ], "Автор обоих ответов Ольга; 22 дня и электронный акт, не Павел/Марина."),
        ("s07_author", "s07", [(
            "| Вега-44 | 14 дней |", "| Вега-44 | 14 дней |\n\nАвтор таблицы: Ольга Смирнова.",
        )], "Автор таблицы Ольга Смирнова, Пётр отправитель; 14 дней."),
    )
    for key, original, replacements, expected in changes:
        user = cases[original]["user"]
        for old, new in replacements:
            if old not in user:
                raise ValueError(f"Synthetic control anchor missing: {key}")
            user = user.replace(old, new)
        cases[key] = {"system": cases[original]["system"], "user": user,
                      "expected": expected, "kind": "explicit_positive_control"}
    for case in cases.values():
        case["prompt_sha256"] = hashlib.sha256((case["system"] + "\0" + case["user"]).encode()).hexdigest()
    return cases


def run(baseline_path, output, models, repeats):
    if output.exists():
        raise ValueError("Do not overwrite comparison evidence")
    if not 1 <= repeats <= 3:
        raise ValueError("Use 1 to 3 bounded repeats")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not baseline.get("database", "").startswith("mail_semantics_"):
        raise ValueError("Only the synthetic mail semantics report is accepted")
    cases = build_cases(baseline)
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    from dotenv import dotenv_values
    from app import config

    env = dotenv_values(ROOT / ".env")
    settings = config.Settings(_env_file=None, **{
        key: env[source] for key, source in (
            ("llm_model", "LLM_MODEL"), ("llm_chat_model", "LLM_CHAT_MODEL"),
            ("llm_base_url", "LLM_BASE_URL"), ("llm_api_key", "LLM_API_KEY"),
        ) if env.get(source)
    })
    del env
    config.get_settings = lambda: settings
    from app.services.llm_client import LLMClient

    report = {"phase": "running", "pid": os.getpid(), "baseline": str(baseline_path),
              "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
              "temperature": settings.llm_temperature, "max_tokens": settings.llm_max_tokens,
              "repeats": repeats, "cases": cases, "results": {}}
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    save()
    for model in models:
        client = LLMClient(interactive=True, model=model)
        model_results = report["results"][model] = []
        failed = False
        for repeat in range(repeats):
            for key, case in cases.items():
                started = time.perf_counter()
                item = {"case": key, "repeat": repeat + 1, "prompt_sha256": case["prompt_sha256"]}
                try:
                    item["answer"] = client.chat(case["system"], case["user"])
                except Exception as exc:
                    item["error_type"] = type(exc).__name__
                    failed = True
                item["seconds"] = round(time.perf_counter() - started, 3)
                model_results.append(item)
                save()
                print(json.dumps({"model": model, "case": key, "repeat": repeat + 1,
                                  "seconds": item["seconds"], "error": item.get("error_type")}), flush=True)
                if failed:
                    break
            if failed:
                break
    report["phase"] = "awaiting_semantic_review"
    save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    run(args.baseline.resolve(), args.output.resolve(), args.models, args.repeats)
