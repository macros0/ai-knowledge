import sys
import threading
import time

import pytest


def fake(tmp_path, behavior):
    path = tmp_path / 'fake.py'
    path.write_text('import sys,json,time\nprint(json.dumps({"status":"ready"}),flush=True)\n'
                    'for line in sys.stdin:\n r=json.loads(line)\n ' + behavior + '\n',
                    encoding='utf-8')
    return [sys.executable, '-u', str(path)]


def worker(command):
    from test_scripts.reranker_eval.runner import WorkerRunner
    return WorkerRunner(command, startup_timeout=5)


def test_success_and_invalid_response_return_whole_prefix(tmp_path):
    command = fake(tmp_path, 'print(json.dumps({"request_id":r["request_id"],'
                   '"status":"applied","scores":[{"index":0,"score":.1},'
                   '{"index":1,"score":.9}]}),flush=True)')
    with worker(command) as runner:
        blocks = [{'content': 'a'}, {'content': 'b'}, {'content': 'tail'}]
        result = runner.run_rerank('q', blocks, top_n=2)
        assert result['status'] == 'applied'
        assert result['blocks'] == [blocks[1], blocks[0], blocks[2]]


@pytest.mark.parametrize('behavior,expected', [
    ('time.sleep(10)', 'timeout'),
    ('print("{",flush=True)', 'invalid_response'),
    ('print("x"*70000,flush=True)', 'invalid_response'),
    ('print(json.dumps({"request_id":"wrong","status":"applied","scores":[]}),flush=True)',
     'invalid_response'),
    ('print(json.dumps({"request_id":r["request_id"],"status":"applied","scores":[]}),flush=True)',
     'invalid_response'),
    ('sys.exit(1)', 'unavailable'),
])
def test_failure_stops_worker_and_preserves_order(tmp_path, behavior, expected):
    with worker(fake(tmp_path, behavior)) as runner:
        process = runner.process
        blocks = [{'content': 'a'}]
        result = runner.run_rerank('q', blocks, top_n=40, deadline_seconds=.3)
        assert result['status'] == expected
        assert result['blocks'] == blocks
        assert process.poll() is not None


def test_busy_has_no_queue_and_cancel_stops_inference(tmp_path):
    from test_scripts.reranker_eval.runner import RerankCancelled
    with worker(fake(tmp_path, 'time.sleep(10)')) as runner:
        cancel, errors = threading.Event(), []
        def task():
            try:
                runner.run_rerank('q', [{'content': 'a'}], top_n=40, cancel=cancel)
            except RerankCancelled:
                errors.append('cancelled')
        thread = threading.Thread(target=task)
        thread.start()
        time.sleep(.1)
        assert runner.run_rerank('q', [], top_n=40)['status'] == 'busy'
        cancel.set()
        thread.join(3)
        assert errors == ['cancelled']
        assert runner.process.poll() is not None


def test_not_ready_is_unavailable_without_loading_on_request(tmp_path):
    from test_scripts.reranker_eval.runner import WorkerRunner
    runner = WorkerRunner(fake(tmp_path, 'pass'), startup_timeout=2)
    assert runner.run_rerank('q', [], top_n=40)['status'] == 'unavailable'


def test_cancel_wins_over_busy_and_missing_runner(tmp_path):
    from test_scripts.reranker_eval.runner import RerankCancelled, run_rerank
    cancel = threading.Event()
    cancel.set()
    runner = worker(fake(tmp_path, 'pass'))
    runner.lock.acquire()
    with pytest.raises(RerankCancelled):
        runner.run_rerank('q', [], top_n=40, cancel=cancel)
    runner.lock.release()
    with pytest.raises(RerankCancelled):
        run_rerank('q', [], top_n=40, cancel=cancel)
