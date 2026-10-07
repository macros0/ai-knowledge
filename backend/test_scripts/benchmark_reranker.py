"""Read-only corpus capture and isolated model replay. No production enablement."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_scripts.reranker_eval.cases import load_cases, validate_judgments
from test_scripts.reranker_eval.snapshot import (capture_case, corpus_fingerprint, digest,
                                                working_tree_fingerprint)
from test_scripts.reranker_eval.runner import WorkerRunner
from test_scripts.reranker_eval.metrics import evaluate_run, latency_summary


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_new(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, default=str)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    capture = commands.add_parser('capture')
    capture.add_argument('--cases', required=True)
    capture.add_argument('--output', required=True)
    freeze = commands.add_parser('freeze')
    freeze.add_argument('--cases', required=True)
    freeze.add_argument('--snapshot', required=True)
    freeze.add_argument('--output', required=True)
    for name in ('replay', 'screen', 'profile'):
        replay = commands.add_parser(name)
        replay.add_argument('--snapshot', required=True)
        replay.add_argument('--cases', required=True)
        replay.add_argument('--worker-python', required=True)
        replay.add_argument('--model', required=True)
        replay.add_argument('--revision', required=True)
        replay.add_argument('--cache', required=True)
        replay.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
        replay.add_argument('--batch-size', type=int, default=8)
        replay.add_argument('--top-n', type=int, choices=[40, 80], default=40)
        replay.add_argument('--deadline', type=float, default=3.)
        replay.add_argument('--split', choices=['development', 'holdout'], default='development')
        replay.add_argument('--frozen-config')
        replay.add_argument('--output', required=True)
        if name == 'profile':
            replay.add_argument('--pairs', type=int, default=100)
            replay.add_argument('--concurrent', type=int, choices=[1, 2], default=1)
    report = commands.add_parser('report')
    report.add_argument('--snapshot', required=True)
    report.add_argument('--after', required=True)
    report.add_argument('--cases', required=True)
    report.add_argument('--output', required=True)
    answers = commands.add_parser('answers')
    answers.add_argument('--snapshot', required=True)
    answers.add_argument('--after', required=True)
    answers.add_argument('--cases', required=True)
    answers.add_argument('--output', required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    cases = load_cases(Path(args.cases))
    if args.command == 'capture':
        from app.auth.models import User
        from app.config import get_settings
        settings = get_settings()
        before = corpus_fingerprint()
        root = Path(__file__).resolve().parents[2]
        code_before = working_tree_fingerprint(root)
        results = []
        for case in cases:
            result = capture_case(case, user=User(), settings=settings)
            results.append(result)
            print(json.dumps({'case': case['id'], 'sources': len(result['final']),
                              'elapsed_ms': round(result['elapsed_ms'])}), flush=True)
        if corpus_fingerprint() != before:
            raise ValueError('corpus changed during batch capture')
        if working_tree_fingerprint(root) != code_before:
            raise ValueError('working tree changed during capture')
        sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
        write_new(args.output, {'manifest': {'corpus_sha256': before,
            'cases_sha256': digest(cases), 'git_sha': sha, 'working_tree_sha256': code_before,
            'settings_sha256': results[0]['manifest']['settings_sha256']}, 'cases': results})
        return 0
    baseline = read(args.snapshot)
    if args.command == 'freeze':
        case_map = {c['id']: c for c in cases}
        if {c['id'] for c in baseline['cases']} != set(case_map):
            raise ValueError('case set mismatch')
        for captured in baseline['cases']:
            case = case_map[captured['id']]
            if case.get('judgments_status') != 'source_verified':
                raise ValueError('source review is not verified')
            if captured['query'] != case['query']:
                raise ValueError('query changed after capture')
            from app.models.schemas import ChatRequest
            request = ChatRequest(**{**case['request'], 'query': case['query']}).model_dump(mode='json')
            if request != captured['request']:
                raise ValueError('request changed after capture')
            validate_judgments(case, [b['eval_key'] for b in captured['final']])
        baseline['manifest']['cases_sha256'] = digest(cases)
        baseline['manifest']['labels_frozen'] = True
        write_new(args.output, baseline)
        return 0
    if args.command in {'replay', 'screen', 'profile'}:
        if args.command == 'replay' and (baseline['manifest']['cases_sha256'] != digest(cases)
                                       or not baseline['manifest'].get('labels_frozen')):
            raise ValueError('cases or labels are not frozen')
        if args.command != 'replay' and args.split != 'development':
            raise ValueError('performance screening uses development only')
        case_map = {c['id']: c for c in cases}
        if {c['id'] for c in baseline['cases']} != set(case_map):
            raise ValueError('case set mismatch')
        from app.models.schemas import ChatRequest
        for captured in baseline['cases']:
            c = case_map[captured['id']]
            request = ChatRequest(**{**c['request'], 'query': c['query']}).model_dump(mode='json')
            if captured['query'] != c['query'] or captured['request'] != request:
                raise ValueError('inputs changed after capture')
        selected = [c for c in cases if c['split'] == args.split]
        ids = {c['id'] for c in selected}
        config = {k: getattr(args, k) for k in ('model', 'revision', 'device', 'batch_size',
                                              'top_n', 'deadline')}
        if args.split == 'holdout':
            if not args.frozen_config or read(args.frozen_config) != config:
                raise ValueError('holdout requires matching frozen configuration')
        command = [args.worker_python, '-u', '-m', 'test_scripts.reranker_eval.worker',
                   '--model', args.model, '--revision', args.revision, '--cache', args.cache,
                   '--device', args.device, '--batch-size', str(args.batch_size)]
        # The worker uses no backend dependencies. Its module search path is explicit.
        os.environ['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        started = perf_counter()
        results = []
        with WorkerRunner(command) as runner:
            selected_snapshots = [c for c in baseline['cases'] if c['id'] in ids]
            if selected_snapshots:
                warm = selected_snapshots[0]
                warmup = runner.run_rerank(warm['query'], warm['final'][:1], top_n=40,
                                           deadline_seconds=30, forms=warm.get('forms', ()))
                if warmup['status'] != 'applied':
                    raise RuntimeError('worker warmup failed')
            if args.command == 'profile':
                from test_scripts.reranker_eval.profiles import paired_profile
                profile = paired_profile(selected_snapshots, runner=runner, pairs=args.pairs,
                    concurrent=args.concurrent, top_n=args.top_n, deadline=args.deadline)
                write_new(args.output, {**profile, 'manifest': baseline['manifest'],
                    'configuration': config, 'worker': runner.metadata,
                    'execution': {'working_tree_sha256': working_tree_fingerprint(
                        Path(__file__).resolve().parents[2])}})
                return 0
            for case in selected_snapshots:
                result = runner.run_rerank(case['query'], case['final'], top_n=args.top_n,
                    deadline_seconds=args.deadline, forms=case.get('forms', ()))
                results.append({'id': case['id'], 'final': result.pop('blocks'), **result})
                print(json.dumps({'case': case['id'], 'status': result['status'],
                                  'elapsed_ms': round(result['elapsed_ms'])}), flush=True)
            metadata = runner.metadata
        write_new(args.output, {'manifest': baseline['manifest'], 'configuration': config,
            'execution': {'working_tree_sha256': working_tree_fingerprint(
                Path(__file__).resolve().parents[2]), 'deadline_seconds': args.deadline},
            'performance_only': args.command == 'screen',
            'worker': metadata, 'elapsed_ms': (perf_counter() - started) * 1000,
            'split': args.split, 'cases': results, 'latency': latency_summary(results)})
        return 0
    after = read(args.after)
    if after.get('performance_only'):
        raise ValueError('performance screening cannot establish quality')
    if baseline['manifest']['cases_sha256'] != digest(cases):
        raise ValueError('cases changed after freeze')
    selected_ids = {c['id'] for c in after['cases']}
    selected_cases = [c for c in cases if c['id'] in selected_ids]
    selected_baseline = {**baseline, 'cases': [c for c in baseline['cases'] if c['id'] in selected_ids]}
    report = evaluate_run(selected_baseline, after, selected_cases)
    if args.command == 'report':
        report['latency'] = after['latency']
        report['configuration'] = after['configuration']
        report['decision'] = 'insufficient_data' if after['split'] != 'holdout' else (
            'keep_disabled' if not report['quality_passed'] or not after['latency']['passed'] else
            'answer_and_load_validation_required')
        write_new(args.output, report)
        return 0
    if corpus_fingerprint() != baseline['manifest']['corpus_sha256']:
        raise ValueError('corpus changed; live answers would use historical evidence')
    from test_scripts.reranker_eval.answers import compare_repeated_answers, live_generator
    original = {c['id']: c for c in selected_baseline['cases']}
    ranked = {c['id']: c for c in after['cases']}
    results = []
    for index, case in enumerate(selected_cases):
        pairs = compare_repeated_answers(case, original[case['id']]['final'], ranked[case['id']]['final'],
                               generator=live_generator, after_first=bool(index % 2))
        results.append({'id': case['id'], 'pairs': pairs})
        print(json.dumps({'case': case['id'], 'answers': 'generated'}), flush=True)
    write_new(args.output, {'manifest': baseline['manifest'], 'cases': results,
                           'manual_source_validation_required': True})
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # No provider traceback or evidence text in terminal output.
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1)
