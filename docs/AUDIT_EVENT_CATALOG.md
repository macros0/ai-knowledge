# Audit event catalog

Версия 1. Реализация — рабочая ветка `codex/kubernetes-rollout`; общий payload ограничен 16 KiB. Legacy DB/UI old/new/meta сохраняются; stdout использует только поля ниже. Auth identity берётся из проверенной сервером сессии/claims.

| Action | stdout old/new fields (type) | default outcome |
|---|---|---|
| `document_upload` | size: int | success |
| `document_delete` | нет | success |
| `document_bulk_delete` | нет | success |
| `document_regenerate` | нет | success |
| `document_update_cancel` | нет | success |
| `document_bulk_regenerate` | нет | success |
| `document_resume` | нет | success |
| `document_bulk_resume` | нет | success |
| `document_development_set` | нет | success |
| `document_tags_update` | doc_ids: summary | success |
| `document_bulk_tags_update` | doc_ids: summary | success |
| `document_source_locale_update` | нет | success |
| `job_approve` | нет | success |
| `job_cancel` | нет | success |
| `user_block` | нет | success |
| `user_unblock` | count: int | success |
| `development_create` | нет | success |
| `development_update` | нет | success |
| `development_delete` | нет | success |
| `attribute_create` | нет | success |
| `attribute_delete` | нет | success |
| `tag_delete` | нет | success |
| `tag_cleanup` | deleted: summary | success |
| `document_restore` | нет | success |
| `document_bulk_restore` | нет | success |
| `document_auto_delete` | нет | success |
| `chat_history_view` | нет | success |
| `chat_history_auto_delete` | нет | success |
| `document_export` | нет | success |
| `document_bulk_export_requested` | нет | success |
| `document_bulk_export_completed` | document_count: int, source_bytes: int, total_bytes: int, part_count: int | success |
| `document_bulk_export_failed` | processed: int, total: int | failure |
| `document_bulk_export_download_started` | part_number: int, size_bytes: int, export_count: int | success |
| `document_bulk_export_expired` | part_count: int | success |
| `document_bulk_export_deleted` | part_count: int | success |
| `locale_create` | нет | success |
| `locale_update` | нет | success |
| `locale_activate` | нет | success |
| `locale_disable` | нет | success |
| `stopwords_import` | нет | success |
| `stopwords_update` | нет | success |
| `stopwords_rollback` | нет | success |
| `tag_translation_update` | нет | success |
| `tag_translation_review` | tag_ids: summary, reviewed: int | success |
| `translations_backfill` | нет | success |
| `ui_dictionary_import` | version: int, total: int | success |
| `ui_dictionary_rollback` | version: int | success |
| `glossary_term_create` | нет | success |
| `glossary_term_update` | нет | success |
| `glossary_term_merge` | нет | success |
| `glossary_source_update` | нет | success |
| `glossary_alias_create` | нет | success |
| `glossary_alias_update` | нет | success |
| `glossary_alias_delete` | нет | success |
| `glossary_translation_update` | нет | success |
| `glossary_translation_review` | нет | success |
| `glossary_translation_backfill` | нет | success |
| `glossary_rule_create` | нет | success |
| `glossary_rule_update` | нет | success |
| `glossary_rule_delete` | нет | success |
| `glossary_identity_migration` | нет | success |
| `diagnostic_session_started` | minutes: int, policy_version: int | success |
| `diagnostic_session_stopped` | bytes_written: int | success |
| `diagnostic_bundle_requested` | нет | success |
| `diagnostic_bundle_ready` | size_bytes: int | success |
| `diagnostic_bundle_failed` | нет | failure |
| `diagnostic_bundle_download_started` | size_bytes: int | success |
| `diagnostic_bundle_deleted` | deleted: bool | success |
| `diagnostic_bundle_expired` | expired: bool | success |
| `diagnostic_view` | preview: bool, event_count: int | success |
| `diagnostic_browser_invited` | нет | success |
| `diagnostic_browser_joined` | нет | success |
| `diagnostic_browser_left` | нет | success |
| `auth_access_denied` | нет | denied |
| `auth_login_denied` | нет | denied |
| `auth_login_success` | нет | success |
| `auth_logout` | нет | success |

`int`: целое 0..2^63−1 без bool; `bool`: boolean; `summary`: только count списка и truncated, без его элементов. Все meta в stdout — null. Неизвестные и неверно типизированные поля отбрасываются. Actor ID/name и target ID/type имеют пределы 255/255/255/64 символа; их oversize отвергается, а не обрезается. UTC timestamp и UUID создаются один раз; dispatcher не меняет payload.

## Callers и границы транзакций

`record_in_session` сохраняет факт в SQL transaction владельца без commit. `record`/`append` открывают собственную transaction: для прежних FS/Qdrant операций это legacy-after-action, не атомарность с внешними хранилищами. В durable login/logout/block/unblock audit объединён с изменением SQL state. Denial wrapper имеет собственную bounded transaction; event только после её commit.

Ниже инвентарь статических call sites и dynamic wrappers по текущему исходнику. В выражениях показаны только action и имена old/new/meta keys, без значений корпуса. Dynamic action разрешается только через общий catalog; async wrapper передаёт тот же action в threadpool.

| Caller | Action expression | SQL owner | old/new/meta source |
|---|---|---|---|
| `backend/app/api/admin_locales.py:77` | `audit.LOCALE_CREATE` | own transaction / legacy-after-action | new_value: code, name |
| `backend/app/api/admin_locales.py:94` | `audit.LOCALE_UPDATE` | own transaction / legacy-after-action | old_value: dynamic expression; new_value: name, status |
| `backend/app/api/admin_locales.py:112` | `audit.LOCALE_ACTIVATE` | own transaction / legacy-after-action | нет / wrapper arguments |
| `backend/app/api/admin_locales.py:134` | `audit.LOCALE_DISABLE` | own transaction / legacy-after-action | нет / wrapper arguments |
| `backend/app/api/attributes.py:47` | `audit.ATTRIBUTE_CREATE` | own transaction / legacy-after-action | new_value: attribute_key, value |
| `backend/app/api/attributes.py:79` | `audit.ATTRIBUTE_DELETE` | own transaction / legacy-after-action | old_value: attribute_key, value |
| `backend/app/api/audit.py:11` | `action_type (wrapper)` | own transaction / legacy-after-action | нет / wrapper arguments |
| `backend/app/api/chat_history.py:122` | `audit.CHAT_HISTORY_VIEW` | own transaction / legacy-after-action | meta: owner_user_id |
| `backend/app/api/developments.py:92` | `audit.DEVELOPMENT_CREATE` | own transaction / legacy-after-action | new_value: number, name, module |
| `backend/app/api/developments.py:155` | `audit.DEVELOPMENT_UPDATE` | own transaction / legacy-after-action | new_value: number, name, module |
| `backend/app/api/developments.py:190` | `audit.DEVELOPMENT_DELETE` | own transaction / legacy-after-action | нет / wrapper arguments |
| `backend/app/api/diagnostics.py:80` | `action` | caller transaction | new_value: dynamic expression |
| `backend/app/api/diagnostics.py:111` | `'diagnostic_view'` | diagnostics wrapper transaction | нет / wrapper arguments |
| `backend/app/api/diagnostics.py:194` | `'diagnostic_view'` | caller transaction | new_value: preview, filters, event_count |
| `backend/app/api/documents.py:443` | `audit.DOCUMENT_UPLOAD` | own transaction / legacy-after-action | new_value: audit_value (dynamic) |
| `backend/app/api/documents.py:717` | `audit.DOCUMENT_DEVELOPMENT_SET` | own transaction / legacy-after-action | old_value: development_id; new_value: development_id |
| `backend/app/api/documents.py:814` | `audit.DOCUMENT_SOURCE_LOCALE_UPDATE` | own transaction / legacy-after-action | old_value: source_locale, source_locale_source; new_value: fields (dynamic) |
| `backend/app/api/documents.py:868` | `audit.DOCUMENT_DEVELOPMENT_SET` | own transaction / legacy-after-action | old_value: development_id; new_value: development_id; meta: source |
| `backend/app/api/documents.py:921` | `audit.DOCUMENT_DELETE` | own transaction / legacy-after-action | old_value: filename |
| `backend/app/api/documents.py:1035` | `audit.DOCUMENT_RESUME` | own transaction / legacy-after-action | нет / wrapper arguments |
| `backend/app/api/documents.py:1076` | `audit.DOCUMENT_REGENERATE` | own transaction / legacy-after-action | old_value: filename |
| `backend/app/api/documents.py:1333` | `audit.DOCUMENT_EXPORT` | own transaction / legacy-after-action | нет / wrapper arguments |
| `backend/app/api/tags.py:65` | `audit.TAG_DELETE` | own transaction / legacy-after-action | old_value: name; new_value: dynamic expression |
| `backend/app/api/tags.py:84` | `audit.TAG_CLEANUP` | own transaction / legacy-after-action | old_value: dynamic expression; new_value: deleted |
| `backend/app/api/tags.py:116` | `audit.TAG_TRANSLATION_UPDATE` | own transaction / legacy-after-action | old_value: dynamic expression; new_value: locale, text |
| `backend/app/api/tags.py:136` | `audit.TAG_TRANSLATION_REVIEW` | own transaction / legacy-after-action | new_value: tag_ids, reviewed |
| `backend/app/api/users.py:44` | `audit.USER_BLOCK` | caller transaction | new_value: reason |
| `backend/app/api/users.py:57` | `audit.USER_BLOCK` | own transaction / legacy-after-action | new_value: reason |
| `backend/app/api/users.py:80` | `audit.USER_UNBLOCK` | caller transaction | new_value: count |
| `backend/app/api/users.py:96` | `audit.USER_UNBLOCK` | own transaction / legacy-after-action | нет / wrapper arguments |
| `backend/app/auth/api.py:109` | `'auth_login_denied'` | dedicated denial transaction | нет / wrapper arguments |
| `backend/app/auth/api.py:155` | `'auth_login_denied'` | dedicated denial transaction | нет / wrapper arguments |
| `backend/app/auth/api.py:159` | `'auth_login_denied'` | dedicated denial transaction | нет / wrapper arguments |
| `backend/app/auth/api.py:178` | `'auth_login_denied'` | dedicated denial transaction | нет / wrapper arguments |
| `backend/app/auth/api.py:182` | `'auth_login_denied'` | dedicated denial transaction | нет / wrapper arguments |
| `backend/app/auth/api.py:188` | `'auth_login_denied'` | dedicated denial transaction | нет / wrapper arguments |
| `backend/app/auth/service.py:81` | `'auth_login_success'` | auth session transaction | нет / wrapper arguments |
| `backend/app/auth/service.py:100` | `'auth_logout'` | auth session transaction | нет / wrapper arguments |
| `backend/app/main.py:119` | `auth_access_denied (default)` | dedicated denial transaction | нет / wrapper arguments |
| `backend/app/main.py:410` | `auth_access_denied (default)` | dedicated denial transaction | нет / wrapper arguments |
| `backend/app/services/audit.py:279` | `action_type` | caller transaction | old_value: old_value (dynamic); new_value: new_value (dynamic); meta: meta (dynamic) |
| `backend/app/services/audit.py:370` | `action_type` | own transaction / legacy-after-action | old_value: old_value (dynamic); new_value: new_value (dynamic); meta: meta (dynamic) |
| `backend/app/services/audit_security.py:40` | `action` | caller transaction | нет / wrapper arguments |
| `backend/app/services/audit_security.py:61` | `action` | caller transaction | нет / wrapper arguments |
| `backend/app/services/chat_history.py:430` | `audit.CHAT_HISTORY_AUTO_DELETE` | own transaction / legacy-after-action | old_value: user_id, title, deleted_at |
| `backend/app/services/diagnostics/browser.py:82` | `action` | caller transaction | new_value: browser_participation |
| `backend/app/services/diagnostics/bundle_queue.py:127` | `'diagnostic_bundle_requested'` | caller transaction | new_value: cutoff_at |
| `backend/app/services/diagnostics/bundle_queue.py:160` | `'diagnostic_bundle_failed'` | caller transaction | new_value: error_code |
| `backend/app/services/diagnostics/bundle_queue.py:194` | `'diagnostic_bundle_failed'` | caller transaction | new_value: error_code |
| `backend/app/services/diagnostics/bundle_queue.py:248` | `'diagnostic_bundle_ready'` | caller transaction | new_value: size_bytes, sha256 |
| `backend/app/services/diagnostics/bundle_queue.py:435` | `'diagnostic_bundle_download_started'` | caller transaction | new_value: size_bytes |
| `backend/app/services/diagnostics/bundle_queue.py:471` | `'diagnostic_bundle_deleted'` | caller transaction | new_value: deleted |
| `backend/app/services/diagnostics/bundle_queue.py:496` | `'diagnostic_bundle_expired'` | caller transaction | new_value: expired |
| `backend/app/services/diagnostics/sessions.py:99` | `'diagnostic_session_started'` | caller transaction | new_value: scope, minutes, doc_id, capture_level, policy_version |
| `backend/app/services/diagnostics/sessions.py:242` | `event['action']` | caller transaction | new_value: reason, bytes_written |
| `backend/app/services/document_tag_service.py:333` | `audit.DOCUMENT_BULK_TAGS_UPDATE if bulk else audit.DOCUMENT_TAGS_UPDATE` | own transaction / legacy-after-action | old_value: audit_old (dynamic); new_value: audit_new (dynamic); meta: meta (dynamic) |
| `backend/app/services/export_queue.py:231` | `audit_mod.DOCUMENT_BULK_EXPORT_REQUESTED` | caller transaction | new_value: plan.audit_value(...) (dynamic) |
| `backend/app/services/export_queue.py:331` | `audit_mod.DOCUMENT_BULK_EXPORT_FAILED` | caller transaction | new_value: reason |
| `backend/app/services/export_queue.py:344` | `audit_mod.DOCUMENT_BULK_EXPORT_DELETED` | caller transaction | new_value: reason |
| `backend/app/services/export_queue.py:412` | `audit_mod.DOCUMENT_BULK_EXPORT_DOWNLOAD_STARTED` | caller transaction | new_value: part_number, filename, size_bytes, export_count |
| `backend/app/services/export_queue.py:493` | `action` | caller transaction | new_value: reason, part_count |
| `backend/app/services/export_queue.py:714` | `audit_mod.DOCUMENT_BULK_EXPORT_COMPLETED` | caller transaction | new_value: document_count, source_bytes, total_bytes, part_count |
| `backend/app/services/export_queue.py:746` | `audit_mod.DOCUMENT_BULK_EXPORT_FAILED` | caller transaction | new_value: error_code, processed, total |
| `backend/app/services/glossary/registry.py:494` | `audit.GLOSSARY_TERM_CREATE` | caller transaction | new_value: _audit_term_dict(...) (dynamic) |
| `backend/app/services/glossary/registry.py:870` | `audit.GLOSSARY_SOURCE_UPDATE if source_changed else audit.GLOSSARY_TERM_UPDATE` | caller transaction | old_value: old_value (dynamic); new_value: _audit_term_dict(...) (dynamic) |
| `backend/app/services/glossary/registry.py:959` | `audit.GLOSSARY_ALIAS_CREATE` | caller transaction | new_value: item (dynamic) |
| `backend/app/services/glossary/registry.py:1004` | `audit.GLOSSARY_ALIAS_DELETE` | caller transaction | old_value: _audit_alias_dict(...) (dynamic) |
| `backend/app/services/glossary/registry.py:1109` | `audit.GLOSSARY_ALIAS_UPDATE` | caller transaction | old_value: old_alias (dynamic); new_value: alias, normalized_alias, locale, auto_expand, search_enabled |
| `backend/app/services/glossary/registry.py:1343` | `audit.GLOSSARY_TERM_MERGE` | caller transaction | old_value: source, target; new_value: target, selections, draft, source_edit |
| `backend/app/services/glossary/rule_registry.py:154` | `audit.GLOSSARY_RULE_CREATE` | caller transaction | new_value: _audit_dict(...) (dynamic) |
| `backend/app/services/glossary/rule_registry.py:186` | `audit.GLOSSARY_RULE_UPDATE` | caller transaction | old_value: old (dynamic); new_value: _audit_dict(...) (dynamic) |
| `backend/app/services/glossary/rule_registry.py:199` | `audit.GLOSSARY_RULE_DELETE` | caller transaction | old_value: old (dynamic) |
| `backend/app/services/glossary/rule_registry.py:282` | `audit.GLOSSARY_RULE_DELETE` | caller transaction | old_value: old_source (dynamic); new_value: merged_into |
| `backend/app/services/glossary/rule_registry.py:285` | `audit.GLOSSARY_RULE_UPDATE` | caller transaction | old_value: old_target (dynamic); new_value: target, source |
| `backend/app/services/glossary/translations.py:196` | `audit.GLOSSARY_TRANSLATION_UPDATE` | caller transaction | old_value: old_value (dynamic); new_value: _translation_dict(...) (dynamic) |
| `backend/app/services/glossary/translations.py:246` | `audit.GLOSSARY_TRANSLATION_BACKFILL` | own transaction / legacy-after-action | new_value: result (dynamic) |
| `backend/app/services/glossary/translations.py:316` | `audit.GLOSSARY_TRANSLATION_BACKFILL` | own transaction / legacy-after-action | new_value: result (dynamic) |
| `backend/app/services/glossary/translations.py:389` | `audit.GLOSSARY_TRANSLATION_UPDATE` | caller transaction | old_value: old_value (dynamic); new_value: _translation_dict(...) (dynamic) |
| `backend/app/services/glossary/translations.py:446` | `audit.GLOSSARY_TRANSLATION_REVIEW` | caller transaction | old_value: old_value (dynamic); new_value: _translation_dict(...) (dynamic) |
| `backend/app/services/job_queue.py:279` | `audit_mod.JOB_APPROVE` | own transaction / legacy-after-action | нет / wrapper arguments |
| `backend/app/services/job_queue.py:308` | `audit_mod.JOB_CANCEL` | own transaction / legacy-after-action | нет / wrapper arguments |
| `backend/app/services/job_queue.py:364` | `audit_mod.DOCUMENT_BULK_DELETE` | own transaction / legacy-after-action | нет / wrapper arguments |
| `backend/app/services/job_queue.py:378` | `audit_mod.DOCUMENT_BULK_RESUME if job['job_type'] == BULK_RESUME else audit_mod.DOCUMENT_BULK_REGENERATE` | own transaction / legacy-after-action | new_value: job_id, phase |
| `backend/app/services/locale_service.py:319` | `audit.STOPWORDS_IMPORT` | own transaction / legacy-after-action | old_value: words, kind; new_value: words, kind; meta: mode, added, removed, unchanged |
| `backend/app/services/locale_service.py:353` | `audit.STOPWORDS_UPDATE` | own transaction / legacy-after-action | old_value: dynamic expression; new_value: word, kind |
| `backend/app/services/locale_service.py:378` | `audit.STOPWORDS_UPDATE` | own transaction / legacy-after-action | old_value: word, kind; new_value: dynamic expression |
| `backend/app/services/locale_service.py:409` | `audit.STOPWORDS_UPDATE` | own transaction / legacy-after-action | old_value: word, kind; new_value: word, kind |
| `backend/app/services/locale_service.py:462` | `audit.STOPWORDS_ROLLBACK` | own transaction / legacy-after-action | old_value: kind, from_entry; new_value: words, kind |
| `backend/app/services/pipeline.py:360` | `audit.DOCUMENT_UPDATE_CANCEL` | caller transaction | new_value: update_id, previous_version_preserved |
| `backend/app/services/translation.py:214` | `audit.TRANSLATIONS_BACKFILL` | own transaction / legacy-after-action | new_value: entities, result |
| `backend/app/services/trash.py:68` | `audit.DOCUMENT_RESTORE` | own transaction / legacy-after-action | old_value: deleted_at; new_value: deleted_at |
| `backend/app/services/trash.py:106` | `audit.DOCUMENT_BULK_RESTORE` | own transaction / legacy-after-action | нет / wrapper arguments |
| `backend/app/services/trash.py:149` | `audit.DOCUMENT_AUTO_DELETE` | own transaction / legacy-after-action | old_value: filename, deleted_at |
| `backend/app/services/ui_dictionary.py:251` | `audit.UI_DICTIONARY_IMPORT` | own transaction / legacy-after-action | new_value: version, total; meta: note, added, removed, activated |
| `backend/app/services/ui_dictionary.py:376` | `audit.UI_DICTIONARY_ROLLBACK` | own transaction / legacy-after-action | new_value: version |

Dynamic dictionary sources остаются в legacy DB; stdout allowlist применяется к результату до INSERT. Утверждение внешнего списка обязательных событий, account CRUD внешнего IdP, trusted IP и downstream delivery остаётся отдельным gate.
