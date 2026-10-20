# Каталог audit schema 1

Пакет на базе main; поля деталей разрешаются явно. Остальной old/new/meta в stdout не копируется.

| action | outcome | Разрешённые old/new поля |
|---|---|---|
| `attribute_create` | success | нет |
| `attribute_delete` | success | нет |
| `auth_access_denied` | denied | нет |
| `auth_login_denied` | denied | нет |
| `auth_login_success` | success | нет |
| `auth_logout` | success | нет |
| `chat_history_auto_delete` | success | нет |
| `chat_history_view` | success | нет |
| `development_create` | success | нет |
| `development_delete` | success | нет |
| `development_update` | success | нет |
| `diagnostic_browser_invited` | success | нет |
| `diagnostic_browser_joined` | success | нет |
| `diagnostic_browser_left` | success | нет |
| `diagnostic_bundle_deleted` | success | `deleted`: bool |
| `diagnostic_bundle_download_started` | success | `size_bytes`: int |
| `diagnostic_bundle_expired` | success | `expired`: bool |
| `diagnostic_bundle_failed` | failure | нет |
| `diagnostic_bundle_ready` | success | `size_bytes`: int |
| `diagnostic_bundle_requested` | success | нет |
| `diagnostic_session_started` | success | `minutes`: int, `policy_version`: int |
| `diagnostic_session_stopped` | success | `bytes_written`: int |
| `diagnostic_view` | success | `preview`: bool, `event_count`: int |
| `document_auto_delete` | success | нет |
| `document_bulk_delete` | success | нет |
| `document_bulk_export_completed` | success | `document_count`: int, `source_bytes`: int, `total_bytes`: int, `part_count`: int |
| `document_bulk_export_deleted` | success | `part_count`: int |
| `document_bulk_export_download_started` | success | `part_number`: int, `size_bytes`: int, `export_count`: int |
| `document_bulk_export_expired` | success | `part_count`: int |
| `document_bulk_export_failed` | failure | `processed`: int, `total`: int |
| `document_bulk_export_requested` | success | нет |
| `document_bulk_regenerate` | success | нет |
| `document_bulk_restore` | success | нет |
| `document_bulk_resume` | success | нет |
| `document_bulk_tags_update` | success | `doc_ids`: summary |
| `document_delete` | success | нет |
| `document_development_set` | success | нет |
| `document_export` | success | нет |
| `document_processing_scope_confirmed` | success | нет |
| `document_processing_scope_updated` | success | нет |
| `document_regenerate` | success | нет |
| `document_restore` | success | нет |
| `document_resume` | success | нет |
| `document_source_locale_update` | success | нет |
| `document_tags_update` | success | `doc_ids`: summary |
| `document_update_cancel` | success | нет |
| `document_upload` | success | `size`: int |
| `glossary_alias_create` | success | нет |
| `glossary_alias_delete` | success | нет |
| `glossary_alias_update` | success | нет |
| `glossary_identity_migration` | success | нет |
| `glossary_rule_create` | success | нет |
| `glossary_rule_delete` | success | нет |
| `glossary_rule_update` | success | нет |
| `glossary_source_update` | success | нет |
| `glossary_term_create` | success | нет |
| `glossary_term_merge` | success | нет |
| `glossary_term_update` | success | нет |
| `glossary_translation_backfill` | success | нет |
| `glossary_translation_review` | success | нет |
| `glossary_translation_update` | success | нет |
| `job_approve` | success | нет |
| `job_cancel` | success | нет |
| `locale_activate` | success | нет |
| `locale_create` | success | нет |
| `locale_disable` | success | нет |
| `locale_update` | success | нет |
| `reference_edit_acquired` | success | нет |
| `reference_edit_generations_invalidated` | success | нет |
| `reference_edit_released` | success | нет |
| `reference_edit_revoked` | success | нет |
| `reference_translation_commit` | success | нет |
| `stopwords_import` | success | нет |
| `stopwords_rollback` | success | нет |
| `stopwords_update` | success | нет |
| `tag_cleanup` | success | `deleted`: summary |
| `tag_delete` | success | нет |
| `tag_translation_review` | success | `tag_ids`: summary, `reviewed`: int |
| `tag_translation_update` | success | нет |
| `translations_backfill` | success | нет |
| `ui_dictionary_import` | success | `version`: int, `total`: int |
| `ui_dictionary_rollback` | success | `version`: int |
| `user_block` | success | нет |
| `user_unblock` | success | `count`: int |

`summary` превращает список в count/truncated без содержимого. DB audit сохраняет свои прежние значения; sanitize применяется к outbox payload.
Каталог не означает атомарность всей domain операции. Security login/logout/block/unblock связаны с outbox отдельной транзакционной интеграцией.
