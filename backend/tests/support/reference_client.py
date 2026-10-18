"""Upgrade legacy glossary domain tests to an observed-revision client.

Only snapshots actually returned to this client supply preconditions. There is
no extra GET or database lookup before a write. Raw protocol tests use TestClient.
"""
import re
from urllib.parse import parse_qsl, urlsplit

from fastapi.testclient import TestClient


class ObservedGlossaryClient(TestClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.observed_references = {}

    def request(self, method, url, **kwargs):
        path = urlsplit(str(url)).path
        prefix = "/api/admin/glossary"
        if path.startswith(prefix) and method.upper() in {"PATCH", "POST", "DELETE"}:
            match = re.fullmatch(prefix + r"/(rules/)?(\d+)(?:/.*)?", path)
            if match:
                kind = "glossary_rule" if match[1] else "glossary_term"
                revision = self.observed_references.get((kind, int(match[2])))
                if method.upper() == "DELETE":
                    params = dict(parse_qsl(urlsplit(str(url)).query))
                    params.update(kwargs.get("params") or {})
                    params.setdefault("expected_reference_revision", revision)
                    kwargs["params"] = params
                elif isinstance(kwargs.get("json"), dict):
                    kwargs["json"] = {"expected_reference_revision": revision, **kwargs["json"]}
            elif path in {prefix + "/merge", prefix + "/rules/merge"} and isinstance(kwargs.get("json"), dict):
                body = kwargs["json"]
                kind = "glossary_rule" if "/rules/" in path else "glossary_term"
                name = "rule" if kind == "glossary_rule" else "term"
                identifiers = [body.get(f"{side}_{name}_id") for side in ("source", "target")]
                kwargs["json"] = {"expected_reference_revisions": {value: self.observed_references.get((kind, value))
                    for value in identifiers if value is not None}, **body}
        response = super().request(method, url, **kwargs)
        if path.startswith(prefix) and response.is_success:
            try:
                data = response.json()
            except ValueError:
                return response
            kind = "glossary_rule" if path.startswith(prefix + "/rules") else "glossary_term"
            def remember(value):
                if isinstance(value, list):
                    for item in value:
                        remember(item)
                elif isinstance(value, dict):
                    if value.get("id") and "reference_revision" in value:
                        self.observed_references[kind, value["id"]] = value["reference_revision"]
                    for key in ("terms", "source", "target"):
                        if key in value:
                            remember(value[key])
            remember(data)
        return response


def reference_request(client, method, url, **kwargs):
    """Explicit modern-client flow for legacy locale domain/RBAC tests.

    These tests are not concurrency tests: they review a fresh preview before
    confirming. Protocol/race tests use raw TestClient and original snapshots.
    An explicitly supplied precondition is always retained.
    """
    path = urlsplit(str(url)).path
    prefix = "/api/admin/locales/"
    method = method.upper()
    if not path.startswith(prefix):
        return client.request(method, url, **kwargs)
    body = dict(kwargs.get("json") or {})
    base_options = {key: value for key, value in kwargs.items() if key != "json"}
    query = dict(parse_qsl(urlsplit(str(url)).query))
    query.update(kwargs.get("params") or {})
    parts = path[len(prefix):].split("/")
    code, action = parts[0], parts[1:]
    if action and action[-1] in {"import", "rollback"} and (body.get("confirm") or (action[-1] == "rollback" and "confirm" not in body)):
        if "expected_reference_revision" in body and "preview_digest" in body:
            return client.request(method, url, **kwargs)
        if action[0] == "stopwords" and action[-1] == "rollback" and "kind" not in body:
            history = client.get(prefix + code + "/stopwords/history")
            if not history.is_success:
                return history
            entry = next((entry for entry in history.json()["entries"] if entry["id"] == body.get("entry_id")), {})
            body["kind"] = entry.get("kind", "bm25")
        preview = client.request(method, url, json={**body, "confirm": False}, **base_options)
        if not preview.is_success or preview.json().get("errors"):
            return preview
        snapshot = preview.json()
        body.update(confirm=True, expected_reference_revision=snapshot["reference_revision"], preview_digest=snapshot["preview_digest"])
        if action[0] == "ui-dictionary":
            body["expected_active_version"] = snapshot["active_version"]
        kwargs["json"] = body
    elif action and action[0] == "stopwords" and ((len(action) == 1 and method == "POST") or
            (len(action) == 2 and method in {"PATCH", "DELETE"})):
        kind = body.get("kind", query.get("kind", "bm25"))
        snapshot = client.get(prefix + code + "/stopwords", params={"kind": kind})
        if not snapshot.is_success:
            return snapshot
        revision = snapshot.json()["reference_revisions"][kind]
        if method == "DELETE":
            query.setdefault("expected_reference_revision", revision)
            kwargs["params"] = query
        else:
            kwargs["json"] = {"expected_reference_revision": revision, **body}
    elif (action in (["activate"], ["disable"]) and method == "POST") or (not action and method == "PATCH"):
        snapshot = client.get("/api/admin/locales")
        if not snapshot.is_success:
            return snapshot
        loc = next((loc for loc in snapshot.json()["locales"] if loc["code"] == code), {})
        kwargs["json"] = {"expected_reference_revision": loc.get("reference_revision", 1), **body}
    return client.request(method, url, **kwargs)
