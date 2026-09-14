from sqlalchemy import delete, inspect, select

from app.db.models import DomainTerm, GlossaryState
from app.db.session import get_engine, session_scope
from app.services.glossary.mutation import bump_glossary_revision, glossary_write_session, initialize_glossary_state


def test_exact_glossary_schema_is_created_for_dev_database():
    tables = set(inspect(get_engine()).get_table_names())
    assert {
        "glossary_infotype_rules",
        "glossary_infotype_prefixes",
        "glossary_identity_keys",
        "glossary_state",
        "glossary_merge_receipts",
    } <= tables
    assert "infotype_number" in {
        item["name"] for item in inspect(get_engine()).get_columns("domain_terms")
    }


def test_write_session_locks_state_and_bumps_only_when_mutating():
    with glossary_write_session() as (session, state):
        assert state.id == 1
        assert state.revision == 0
        bump_glossary_revision(session, state)
    with session_scope() as session:
        assert session.scalar(select(GlossaryState.revision).where(GlossaryState.id == 1)) == 1


def test_existing_terms_start_unready_when_state_is_initialized():
    with session_scope() as session:
        session.execute(delete(GlossaryState))
        session.add(DomainTerm(canonical="LEGACY", kind="business_term", original_name="Legacy"))
    with session_scope() as session:
        state = initialize_glossary_state(session)
        assert state.identities_ready is False
