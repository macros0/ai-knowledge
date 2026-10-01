"""Synthetic admin HTTP smoke for a disposable simulation-auth stack."""
import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import time
import urllib.error
from zipfile import ZipFile
from probe_diagnostics_http import Client


def expect_http(client, method, path, status):
    try:
        client.call(method, path)
    except urllib.error.HTTPError as exc:
        if exc.code == status:
            return
        raise
    raise RuntimeError(f"Expected HTTP {status} for {method} {path}")


def run(base_url):
    admin = Client(base_url)
    admin.login()
    status = admin.call("GET", "/api/admin/diagnostics/status")
    if status.get("capabilities", {}).get("capture_levels") != ["standard", "detailed"]:
        raise RuntimeError("Capture level capability missing")
    session = admin.call("POST", "/api/admin/diagnostics/sessions",
                         {"scope": "system", "capture_level": "standard", "minutes": 5})
    session_id = session["id"]
    try:
        for _ in range(20):
            admin.call("GET", "/api/settings")
    finally:
        admin.call("POST", f"/api/admin/diagnostics/sessions/{session_id}/stop", {})
    bundle = admin.call("POST", "/api/admin/diagnostics/bundles", {"session_id": session_id})
    bundle_id = bundle["id"]
    preview = None
    for _ in range(60):
        try:
            preview = admin.call("POST", f"/api/admin/diagnostics/bundles/{bundle_id}/preview", {})
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 409:
                raise
            time.sleep(1)
    if preview is None:
        raise RuntimeError("Bundle did not become ready")
    manifest = preview["manifest"]
    if manifest.get("format_version") != 2 or not any(
        row.get("session_id") == session_id and row.get("capture_level") == "standard"
        for row in manifest.get("capture_policies", [])
    ):
        raise RuntimeError("Policy coverage mismatch")
    content = admin.call("GET", f"/api/admin/diagnostics/bundles/{bundle_id}/download")
    if sha256(content).hexdigest() != preview["sha256"]:
        raise RuntimeError("Bundle SHA-256 mismatch")
    with ZipFile(BytesIO(content)) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Bundle CRC mismatch")
        expected = {"summary.txt", "manifest.json", "events/backend.jsonl",
                    "events/frontend.jsonl", "events/browser.jsonl",
                    "snapshots/runtime.json", "snapshots/operations.json"}
        if set(archive.namelist()) != expected:
            raise RuntimeError("Bundle entries mismatch")
        for name, digest in manifest["checksums"].items():
            if sha256(archive.read(name)).hexdigest() != digest:
                raise RuntimeError("Component checksum mismatch")
    reader = Client(base_url)
    reader.call("GET", "/api/auth/me")
    reader.call("POST", "/api/auth/simulate", {"username": "demo.user"})
    expect_http(reader, "GET", "/api/admin/diagnostics/status", 403)
    expect_http(reader, "GET", f"/api/admin/diagnostics/bundles/{bundle_id}/download", 403)
    # Simulation auth intentionally bypasses CSRF; production-mode CSRF is
    # covered by authenticated backend tests with a real cookie session.
    admin.call("DELETE", f"/api/admin/diagnostics/bundles/{bundle_id}")
    expect_http(admin, "GET", f"/api/admin/diagnostics/bundles/{bundle_id}/download", 410)
    return {"session_id": session_id, "bundle_id": bundle_id,
            "manifest_version": manifest["format_version"],
            "partial": manifest["partial"], "gaps": manifest["gaps"],
            "coverage_modes": manifest["coverage_modes"],
            "loss_counters_state": manifest["loss_counters_state"],
            "events": manifest["counts"].get("events"), "bundle_bytes": len(content),
            "admin_lifecycle": "passed", "reader_denial": "passed",
            "csrf_denial": "not_applicable_simulation", "delete_denial": "passed", "crc": "passed"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18084")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.base_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
