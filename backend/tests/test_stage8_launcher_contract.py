from pathlib import Path


def test_stage8_launcher_checks_start_all_invocation_status():
    script = (
        Path(__file__).resolve().parents[2] / "tests" / "scripts" / "stage8" / "start-stage8-test.ps1"
    ).read_text(encoding="utf-8")
    start = script.index("& (Join-Path $Root 'scripts\\start-all.ps1')")
    end = script.index("function Ensure-TestDatabase", start)
    handoff = script[start:end]

    assert "if (-not $?)" in handoff
    assert "$LASTEXITCODE" not in handoff


def test_stage8_launcher_ignores_inaccessible_global_helper():
    script = (
        Path(__file__).resolve().parents[2] / "tests" / "scripts" / "stage8" / "start-stage8-test.ps1"
    ).read_text(encoding="utf-8")

    assert (
        "Test-Path -LiteralPath $helper -ErrorAction SilentlyContinue" in script
    )


def test_stage8_launcher_requires_measurement_profile_before_ready():
    script = (
        Path(__file__).resolve().parents[2] / "tests" / "scripts" / "stage8" / "start-stage8-test.ps1"
    ).read_text(encoding="utf-8")

    assert (
        "if ($health.status -eq 'ok' -and "
        "$health.knowledge_profile -eq $profile.KnowledgeProfile)"
        in script
    )


def test_repo_background_helper_has_a_powershell_process_stop_fallback():
    script = (
        Path(__file__).resolve().parents[2] / "scripts" / "start-background.ps1"
    ).read_text(encoding="utf-8")

    assert "Stop-Process -Id $ProcessId -Force" in script


def test_repo_background_helper_waits_for_port_release():
    script = (
        Path(__file__).resolve().parents[2] / "scripts" / "start-background.ps1"
    ).read_text(encoding="utf-8")

    assert "function Wait-PortFree" in script
    assert "Wait-PortFree -Port $Port" in script
