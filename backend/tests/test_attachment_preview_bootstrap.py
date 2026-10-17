"""Windows spawn must not import the backend's provider stack before worker limits."""
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_spawn_reimport_of_diagnostic_entrypoint_stays_light(tmp_path):
    script = tmp_path / "bootstrap_probe.py"
    script.write_text(textwrap.dedent('''
        import json
        import multiprocessing
        import sys
        from app import diagnostic_entrypoint

        def probe(connection):
            connection.send([name for name in (
                "app.services.diagnostics.runtime", "app.services.embedder",
                "litellm", "qdrant_client", "app.main",
            ) if name in sys.modules])
            connection.close()

        if __name__ == "__main__":
            context = multiprocessing.get_context("spawn")
            parent, child = context.Pipe(duplex=False)
            process = context.Process(target=probe, args=(child,))
            process.start()
            child.close()
            try:
                assert parent.poll(20), "worker did not start"
                print(json.dumps(parent.recv()))
                process.join(2)
                assert process.exitcode == 0
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(2)
                parent.close()
    '''), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                            timeout=40, env=env, check=True)
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []
