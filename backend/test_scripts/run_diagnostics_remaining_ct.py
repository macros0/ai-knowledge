"""Finish required evidence once, serially; numerical failures stop the queue."""
import json

if __package__:
    from . import run_diagnostics_final_ct as queue
    from .run_diagnostics_targeted_ct import EXPECTED_IMAGES
    from .probe_diagnostics_http_matrix import _containers, _image_ids
    from .run_diagnostics_final_ct_tail import original_python
else:
    import run_diagnostics_final_ct as queue
    from run_diagnostics_targeted_ct import EXPECTED_IMAGES
    from probe_diagnostics_http_matrix import _containers, _image_ids
    from run_diagnostics_final_ct_tail import original_python


def main():
    root = queue.ROOT
    queue.STATE = root / "remaining-acceptance-v15-v11-state.json"
    if queue.STATE.exists():
        raise FileExistsError("Inspect the existing remaining-stage ledger before a retry")
    targeted = json.loads((root / "targeted-standard-c8-active-v15-v11-analysis.json").read_text())
    if any(targeted.get(key) is not True for key in queue.MATRIX_GATES) or len(targeted["decisions"]) != 3:
        raise RuntimeError("All three targeted pairs must pass first")
    def run(stage, command):
        if _image_ids(_containers(queue.PROJECT)) != EXPECTED_IMAGES:
            raise RuntimeError("Frozen images changed")
        queue.run(stage, command)
        if _image_ids(_containers(queue.PROJECT)) != EXPECTED_IMAGES:
            raise RuntimeError("Frozen images changed during measurement")
    run("native-ttl-quota-final-v15-v11", queue.private_python([
        "-m", "pytest", "-q", "tests/test_diagnostics_final_acceptance.py",
        "--basetemp", "/tmp/diag-final-ttl-v15-v11"]))
    # Check the stricter cheap controls before expanding the live matrices.
    for scenario in ("search_stub", "settings"):
        for repeat in (1, 2, 3):
            for concurrency in (1, 8):
                modes = ("standard", "detailed") if repeat % 2 else ("detailed", "standard")
                for mode in modes:
                    stage = f"testclient-{scenario}-{mode}-c{concurrency}-r{repeat}-v15-v11"
                    run(stage, queue.private_python(["/app/test_scripts/probe_diagnostics.py",
                        "--root", f"/probe/{stage}", "--output", f"/probe/{stage}.json",
                        "--scenario", scenario, "--capture-level", mode,
                        "--concurrency", str(concurrency), "--warmup-seconds", "30",
                        "--measurement-seconds", "60", "--requests", "1000"]))
    for scenario in ("search_stub", "settings"):
        for repeat in (1, 2, 3):
            for concurrency in (1, 8):
                stage = f"testclient-{scenario}-original-c{concurrency}-r{repeat}-v15-v11"
                run(stage, original_python(["/app/test_scripts/probe_diagnostics.py",
                    "--root", f"/probe/{stage}", "--output", f"/probe/{stage}.json",
                    "--scenario", scenario, "--concurrency", str(concurrency),
                    "--warmup-seconds", "30", "--measurement-seconds", "60", "--requests", "1000"]))
    for reference in (False, True):
        stage = "fixed-input-original-full-v15-v11" if reference else "fixed-input-full-v15-v11"
        command = original_python if reference else queue.private_python
        run(stage, command(["/app/test_scripts/probe_diagnostics_fixed_input.py",
            "--root", f"/probe/{stage}", "--output", f"/probe/{stage}.json",
            "--records", "20000", "--repeats", "3",
            "--reference-version", "original" if reference else "current"]))
    # Later live blocks are deliberately dispatched only after these gates pass.
    # Keep the captured controls as evidence rather than silently claiming completion.
    print("Private controls passed; live 1/100-doc and baseline-off blocks remain required", flush=True)


if __name__ == "__main__":
    main()
