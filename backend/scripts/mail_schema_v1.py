"""Frozen canonical mail/publication schema through 010b1c2d3e4f.

Keep baseline type/nullability/constraint checks without importing future ORM fields.
This is a repair contract, not a replacement for the Alembic main lineage.
Later releases own their additional columns and indexes.
"""
from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Float, ForeignKeyConstraint,
    Index, Integer, JSON, MetaData, String, Table, Text, UniqueConstraint,
)


def mail_metadata():
    metadata = MetaData()
    Table('organizations', metadata, Column("id", Integer(), primary_key=True))
    Table('users', metadata, Column("id", Integer(), primary_key=True))
    Table('developments', metadata, Column("id", Integer(), primary_key=True))
    Table('documents', metadata,
          Column('id', String(length=16), primary_key=True, nullable=False),
          Column('org_id', Integer(), nullable=True),
          Column('owner_id', Integer(), nullable=True),
          Column('uploaded_by', String(length=255), nullable=True),
          Column('filename', String(length=1024), nullable=False),
          Column('content_type', String(length=255), nullable=False),
          Column('size', Integer(), nullable=False),
          Column('status', String(length=32), nullable=False),
          Column('error', Text(), nullable=True),
          Column('error_code', String(length=64), nullable=True),
          Column('problem', String(length=64), nullable=True),
          Column('total_chunks', Integer(), nullable=False),
          Column('processed_chunks', Integer(), nullable=False),
          Column('current_chunk', Integer(), nullable=True),
          Column('okf_concept_count', Integer(), nullable=False),
          Column('created_at', DateTime(timezone=True), nullable=False),
          Column('updated_at', DateTime(timezone=True), nullable=False),
          Column('development_id', Integer(), nullable=True),
          Column('development_confidence', Float(), nullable=True),
          Column('development_confirmed_by', String(length=255), nullable=True),
          Column('development_suggestion', JSON(), nullable=True),
          Column('file_hash', String(length=64), nullable=True),
          Column('content_hash', String(length=64), nullable=True),
          Column('mail_fingerprint', String(length=64), nullable=True),
          Column('parser_version', String(length=64), nullable=True),
          Column('parse_warnings', JSON(), nullable=True),
          Column('minhash', JSON(), nullable=True),
          Column('has_duplicates', Boolean(), nullable=True),
          Column('deleted_at', DateTime(timezone=True), nullable=True),
          Column('deleted_by', String(length=255), nullable=True),
          Column('source_locale', String(length=16), nullable=True),
          Column('source_locale_source', String(length=8), nullable=True),
          ForeignKeyConstraint(['development_id'], ['developments.id'],
                               name=None, ondelete=None),
          ForeignKeyConstraint(['org_id'], ['organizations.id'],
                               name=None, ondelete=None),
          ForeignKeyConstraint(['owner_id'], ['users.id'],
                               name=None, ondelete=None),
    )
    Index('ix_documents_content_hash', metadata.tables['documents'].c['content_hash'], unique=False)
    Index('ix_documents_deleted_at', metadata.tables['documents'].c['deleted_at'], unique=False)
    Index('ix_documents_development_id', metadata.tables['documents'].c['development_id'], unique=False)
    Index('ix_documents_file_hash', metadata.tables['documents'].c['file_hash'], unique=False)
    Index('ix_documents_mail_fingerprint', metadata.tables['documents'].c['mail_fingerprint'], unique=False)
    Table('document_chunks', metadata,
          Column('id', Integer(), primary_key=True, nullable=False),
          Column('doc_id', String(length=16), nullable=False),
          Column('source_id', String(length=255), nullable=True),
          Column('chunk_index', Integer(), nullable=False),
          Column('section_title', String(1024), nullable=True),
          Column('content', Text(), nullable=False),
          Column('content_hash', String(length=64), nullable=True),
          Column('char_count', Integer(), nullable=False),
          Column('created_at', DateTime(timezone=True), nullable=False),
          ForeignKeyConstraint(['doc_id'], ['documents.id'],
                               name=None, ondelete='CASCADE'),
          UniqueConstraint('doc_id', 'chunk_index', name='uq_document_chunks_doc_index'),
    )
    Index('ix_document_chunks_doc_id', metadata.tables['document_chunks'].c['doc_id'], unique=False)
    Index('ix_document_chunks_source_id', metadata.tables['document_chunks'].c['source_id'], unique=False)
    Table('okf_concepts', metadata,
          Column('id', Integer(), primary_key=True, nullable=False),
          Column('doc_id', String(length=16), nullable=False),
          Column('slug', String(length=255), nullable=False),
          Column('title', String(length=1024), nullable=False),
          Column('type', String(length=32), nullable=False),
          Column('tags', JSON(), nullable=True),
          Column('content', Text(), nullable=False),
          Column('relations', JSON(), nullable=True),
          Column('chunk_index', Integer(), nullable=True),
          Column('source_id', String(length=255), nullable=True),
          Column('source_spans', JSON(), nullable=True),
          Column('created_at', DateTime(timezone=True), nullable=False),
          Column('generated_at', DateTime(timezone=True), nullable=True),
          Column('model_id', String(length=255), nullable=True),
          Column('prompt_version', String(length=64), nullable=True),
          ForeignKeyConstraint(['doc_id'], ['documents.id'],
                               name=None, ondelete='CASCADE'),
          UniqueConstraint('doc_id', 'slug', name='uq_okf_concepts_doc_slug'),
    )
    Index('ix_okf_concepts_doc_id', metadata.tables['okf_concepts'].c['doc_id'], unique=False)
    Index('ix_okf_concepts_source_id', metadata.tables['okf_concepts'].c['source_id'], unique=False)
    Table('okf_attachments', metadata,
          Column('id', Integer(), primary_key=True, nullable=False),
          Column('doc_id', String(length=16), nullable=False),
          Column('source_id', String(length=255), nullable=True),
          Column('name', String(length=512), nullable=False),
          Column('kind', String(length=64), nullable=False),
          Column('caption', Text(), nullable=False),
          Column('saved_path', String(length=1024), nullable=True),
          Column('content_type', String(length=255), nullable=True),
          Column('size', Integer(), nullable=True),
          Column('sha256', String(length=64), nullable=True),
          Column('is_processable', Boolean(), nullable=False),
          Column('extraction_status', String(length=32), nullable=True),
          Column('extracted_chars', Integer(), nullable=True),
          Column('processed_at', DateTime(timezone=True), nullable=True),
          Column('error', Text(), nullable=True),
          Column('created_at', DateTime(timezone=True), nullable=False),
          ForeignKeyConstraint(['doc_id'], ['documents.id'],
                               name=None, ondelete='CASCADE'),
          UniqueConstraint('doc_id', 'saved_path', name='uq_okf_attachments_doc_saved_path'),
    )
    Index('ix_okf_attachments_doc_id', metadata.tables['okf_attachments'].c['doc_id'], unique=False)
    Index('ix_okf_attachments_source_id', metadata.tables['okf_attachments'].c['source_id'], unique=False)
    Table('document_staging', metadata,
          Column('doc_id', String(length=16), primary_key=True, nullable=False),
          Column('total_chunks', Integer(), nullable=False),
          Column('processed_chunks', JSON(), nullable=True),
          Column('used_slugs', JSON(), nullable=True),
          Column('global_tags', JSON(), nullable=True),
          Column('chunks_data', JSON(), nullable=True),
          Column('parser_version', String(length=64), nullable=True),
          Column('source_file_hash', String(length=64), nullable=True),
          Column('generation_id', String(length=32), nullable=True),
          Column('status', String(length=32), nullable=False),
          Column('updated_at', DateTime(timezone=True), nullable=False),
          ForeignKeyConstraint(['doc_id'], ['documents.id'],
                               name=None, ondelete='CASCADE'),
    )
    Table('document_sources', metadata,
          Column('doc_id', String(length=16), primary_key=True, nullable=False),
          Column('source_id', String(length=255), primary_key=True, nullable=False),
          Column('parent_source_id', String(length=255), nullable=True),
          Column('ordinal', Integer(), nullable=False),
          Column('kind', String(length=64), nullable=False),
          Column('display_name', String(length=512), nullable=False),
          Column('metadata', JSON(), nullable=True),
          Column('saved_path', String(length=1024), nullable=True),
          Column('extraction_status', String(length=32), nullable=True),
          Column('artifact_kind', String(length=32), nullable=False),
          Column('container_source_id', String(length=255), nullable=True),
          Column('container_locator', String(length=1024), nullable=True),
          Column('content_fingerprint', String(length=64), nullable=True),
          Column('parser_version', String(length=64), nullable=True),
          Column('warnings', JSON(), nullable=True),
          Column('created_at', DateTime(timezone=True), nullable=False),
          ForeignKeyConstraint(['doc_id'], ['documents.id'],
                               name=None, ondelete='CASCADE'),
          ForeignKeyConstraint(['doc_id', 'parent_source_id'], ['document_sources.doc_id', 'document_sources.source_id'],
                               name='fk_document_sources_parent', ondelete='CASCADE'),
    )
    Index('ix_document_sources_doc_parent', metadata.tables['document_sources'].c['doc_id'], metadata.tables['document_sources'].c['parent_source_id'], unique=False)
    Table('document_generation_states', metadata,
          Column('doc_id', String(length=16), primary_key=True, nullable=False),
          Column('active_generation_id', String(length=32), nullable=True),
          Column('candidate_generation_id', String(length=32), nullable=True),
          ForeignKeyConstraint(['doc_id'], ['documents.id'],
                               name=None, ondelete='CASCADE'),
    )
    Table('document_generations', metadata,
          Column('id', String(length=32), primary_key=True, nullable=False),
          Column('doc_id', String(length=16), nullable=False),
          Column('base_generation_id', String(length=32), nullable=True),
          Column('phase', String(length=16), nullable=False),
          Column('legacy_cleanup_pending', Boolean(), nullable=False),
          Column('publication_hash', String(length=64), nullable=True),
          Column('created_at', DateTime(timezone=True), nullable=False),
          Column('activated_at', DateTime(timezone=True), nullable=True),
          CheckConstraint("phase IN ('preparing', 'ready', 'active', 'retired', 'abandoned')", name='ck_document_generations_phase'),
          ForeignKeyConstraint(['doc_id'], ['documents.id'],
                               name=None, ondelete='CASCADE'),
    )
    Index('ix_document_generations_doc_id', metadata.tables['document_generations'].c['doc_id'], unique=False)
    return metadata
