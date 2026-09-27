-- Existing baseline schema compatibility for Stage 8; verified schema-only clone.
-- Execute only on verified Stage 8 target after release gates, with writers stopped.
BEGIN;
ALTER TABLE documents ADD COLUMN mail_fingerprint VARCHAR(64);
ALTER TABLE documents ADD COLUMN parser_version VARCHAR(64);
ALTER TABLE documents ADD COLUMN parse_warnings JSON;
ALTER TABLE document_chunks ADD COLUMN source_id VARCHAR(255);

CREATE TABLE document_generation_states (
	doc_id VARCHAR(16) NOT NULL, 
	active_generation_id VARCHAR(32), 
	candidate_generation_id VARCHAR(32), 
	PRIMARY KEY (doc_id), 
	FOREIGN KEY(doc_id) REFERENCES documents (id) ON DELETE CASCADE
)

;

CREATE TABLE document_generations (
	id VARCHAR(32) NOT NULL, 
	doc_id VARCHAR(16) NOT NULL, 
	base_generation_id VARCHAR(32), 
	phase VARCHAR(16) NOT NULL, 
	legacy_cleanup_pending BOOLEAN NOT NULL, 
	publication_hash VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	activated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_document_generations_phase CHECK (phase IN ('preparing', 'ready', 'active', 'retired', 'abandoned')), 
	FOREIGN KEY(doc_id) REFERENCES documents (id) ON DELETE CASCADE
)

;
CREATE INDEX ix_document_generations_doc_id ON document_generations (doc_id);

CREATE TABLE document_sources (
	doc_id VARCHAR(16) NOT NULL, 
	source_id VARCHAR(255) NOT NULL, 
	parent_source_id VARCHAR(255), 
	ordinal INTEGER NOT NULL, 
	kind VARCHAR(64) NOT NULL, 
	display_name VARCHAR(512) NOT NULL, 
	metadata JSON, 
	saved_path VARCHAR(1024), 
	extraction_status VARCHAR(32), 
	artifact_kind VARCHAR(32) NOT NULL, 
	container_source_id VARCHAR(255), 
	container_locator VARCHAR(1024), 
	content_fingerprint VARCHAR(64), 
	parser_version VARCHAR(64), 
	warnings JSON, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (doc_id, source_id), 
	CONSTRAINT fk_document_sources_parent FOREIGN KEY(doc_id, parent_source_id) REFERENCES document_sources (doc_id, source_id) ON DELETE CASCADE, 
	FOREIGN KEY(doc_id) REFERENCES documents (id) ON DELETE CASCADE
)

;
CREATE INDEX ix_document_sources_doc_parent ON document_sources (doc_id, parent_source_id);
ALTER TABLE document_staging ADD COLUMN parser_version VARCHAR(64);
ALTER TABLE document_staging ADD COLUMN source_file_hash VARCHAR(64);
ALTER TABLE document_staging ADD COLUMN generation_id VARCHAR(32);
ALTER TABLE okf_attachments ADD COLUMN source_id VARCHAR(255);
ALTER TABLE okf_concepts ADD COLUMN source_id VARCHAR(255);
ALTER TABLE okf_concepts ADD COLUMN source_spans JSON;
COMMIT;
