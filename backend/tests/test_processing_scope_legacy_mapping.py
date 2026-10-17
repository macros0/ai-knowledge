from copy import deepcopy
import pytest
from tests.test_processing_scope_change import example


def test_unique_verified_original_occurrence_recovers_legacy_locator_without_mutating_input():
    from app.services.processing_scope_change import recover_legacy_source_locators,analyze_scope_change
    base,selection=example()
    for row in base.rows["sources"][1:]:
        row["metadata"]={};row.pop("container_locator",None);row["saved_path"]="old.xlsx"
    before=deepcopy(base.rows["sources"])
    proof={s["source_id"]:s["content_fingerprint"] for s in before[1:]}
    base.rows["sources"]=recover_legacy_source_locators(before,selection["inventory"],root_verified=True,original_hashes=proof)
    selection["decisions"][0]["effective_included"]=False
    plan=analyze_scope_change(base,selection)
    assert plan.mode=="fast_exclude" and plan.excluded_source_ids==["root/0"]
    assert before[1].get("container_locator") is None
    assert base.rows["sources"][1]["metadata"]["legacy_locator_verified"] is True


@pytest.mark.parametrize("broken",["root","no_original","duplicate_occurrence","duplicate_source","changed_original"])
def test_legacy_recovery_never_guesses_from_a_hash_or_ordinal_alone(broken):
    from app.services.processing_scope_change import recover_legacy_source_locators,analyze_scope_change
    base,selection=example()
    for row in base.rows["sources"][1:]:
        row["metadata"]={};row.pop("container_locator",None);row["saved_path"]="old.xlsx"
    proof={s["source_id"]:s["content_fingerprint"] for s in base.rows["sources"][1:]}
    if broken=="no_original":proof.pop("root/0")
    if broken=="changed_original":proof["root/0"]="f"*64
    if broken=="duplicate_occurrence":selection["inventory"]["nodes"][1]["content_fingerprint"]=selection["inventory"]["nodes"][0]["content_fingerprint"]
    if broken=="duplicate_source":base.rows["sources"][2]["content_fingerprint"]=base.rows["sources"][1]["content_fingerprint"]
    base.rows["sources"]=recover_legacy_source_locators(base.rows["sources"],selection["inventory"],root_verified=broken!="root",original_hashes=proof)
    selection["decisions"][0]["effective_included"]=False
    assert analyze_scope_change(base,selection).mode=="blocked"
