# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Стабильные коды ошибок API — единый словарь для всех слоёв.

Отдельный модуль, а не часть app/api: коды нужны и сервисному слою (доменные
ошибки несут их с собой), а импортировать app.api из app.services означало бы
инверсию слоёв.

Код — контракт для клиента: по нему фронтенд берёт текст из своего словаря
(frontend/src/i18n/locales) и показывает его на языке интерфейса. Менять
значение кода — ломать клиентов; добавлять новый — не забыть ключ
`apiError.<code>` в ru.js и en.js.
"""
# --- Не найдено ---
DOCUMENT_NOT_FOUND = "document_not_found"
DEVELOPMENT_NOT_FOUND = "development_not_found"
FILE_NOT_FOUND = "file_not_found"
CHUNK_NOT_FOUND = "chunk_not_found"
SESSION_NOT_FOUND = "session_not_found"
JOB_NOT_FOUND = "job_not_found"
TAG_NOT_FOUND = "tag_not_found"
VALUE_NOT_FOUND = "value_not_found"
TEXT_NOT_FOUND = "text_not_found"
LOCALE_NOT_FOUND = "locale_not_found"
OVERRIDE_NOT_FOUND = "override_not_found"
USER_NOT_FOUND = "user_not_found"

# --- Доступ ---
AUTH_REQUIRED = "auth_required"
AUTH_DISABLED = "auth_disabled"
FORBIDDEN = "forbidden"
NO_ROLE = "no_role"
USER_BLOCKED = "user_blocked"
SELF_APPROVAL = "self_approval"

# --- Состояние / конфликт ---
ALREADY_PROCESSING = "already_processing"
ALREADY_IN_TRASH = "already_in_trash"
DUPLICATE = "duplicate"
DUPLICATE_NUMBER = "duplicate_number"
VERSION_CONFLICT = "version_conflict"
QUEUE_OVERLOADED = "queue_overloaded"

# --- Ввод ---
EMPTY_FILE = "empty_file"
FILE_TOO_LARGE = "file_too_large"
EMPTY_DOCUMENT_LIST = "empty_document_list"
DOCUMENT_LIMIT_EXCEEDED = "document_limit_exceeded"
INVALID_REQUEST = "invalid_request"
CONFLICT = "conflict"

# --- Инфраструктура (уже используются) ---
DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
INTERNAL_ERROR = "internal_error"
RATE_LIMITED = "rate_limited"
STORAGE_FULL = "storage_full"
TIMEOUT = "timeout"
SERVER_RESTARTED = "server_restarted"
JOB_INTERRUPTED = "job_interrupted"
GENERATION_RETRYING = "generation_retrying"
GENERATION_TIMEOUT = "generation_timeout"
GENERATION_RATE_LIMITED = "generation_rate_limited"
PROCESSING_UNAVAILABLE = "processing_unavailable"

# --- Доменные правила (коды для DomainError сервисного слоя) ---
NOT_IN_TRASH = "not_in_trash"
NOT_RESUMABLE = "not_resumable"
UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
EMPTY_VALUE = "empty_value"
TAG_IN_USE = "tag_in_use"
VALUE_IN_USE = "value_in_use"
UNKNOWN_JOB_TYPE = "unknown_job_type"
JOB_NOT_AWAITING_APPROVAL = "job_not_awaiting_approval"
JOB_NOT_CANCELLABLE = "job_not_cancellable"
REGENERATE_FAILED = "regenerate_failed"
REGENERATE_TIMEOUT = "regenerate_timeout"
TRANSLATION_EMPTY = "translation_empty"
DICT_VERSION_NOT_FOUND = "dict_version_not_found"
SOURCE_LOCALE_INVALID = "source_locale_invalid"
GLOSSARY_TERM_NOT_FOUND = "glossary_term_not_found"
GLOSSARY_ALIAS_NOT_FOUND = "glossary_alias_not_found"
GLOSSARY_ALIAS_CONFLICT = "glossary_alias_conflict"
GLOSSARY_CANONICAL_CONFLICT = "glossary_canonical_conflict"
GLOSSARY_INVALID_ALIAS = "glossary_invalid_alias"
GLOSSARY_UNSAFE_AUTO_EXPAND = "glossary_unsafe_auto_expand"
GLOSSARY_INVALID_LOCALE = "glossary_invalid_locale"
GLOSSARY_IDENTITY_CONFLICT = "glossary_identity_conflict"
GLOSSARY_RULE_OVERLAP = "glossary_rule_overlap"
GLOSSARY_RULE_MERGE_UNSAFE = "glossary_rule_merge_unsafe"
GLOSSARY_RULE_NOT_FOUND = "glossary_rule_not_found"
GLOSSARY_INVALID_RULE = "glossary_invalid_rule"
GLOSSARY_MIGRATION_REQUIRED = "glossary_migration_required"
GLOSSARY_REDUNDANT_ALIAS = "glossary_redundant_alias"
GLOSSARY_MULTIPLE_INFOTYPE_NUMBERS = "glossary_multiple_infotype_numbers"
GLOSSARY_MERGE_CHOICES_REQUIRED = "glossary_merge_choices_required"
GLOSSARY_MERGE_PREVIEW_STALE = "glossary_merge_preview_stale"
GLOSSARY_IDEMPOTENCY_CONFLICT = "glossary_idempotency_conflict"

# --- Массовый экспорт исходных документов ---
BULK_EXPORT_DISABLED = "bulk_export_disabled"
BULK_EXPORT_SOURCE_CONFLICT = "bulk_export_source_conflict"
BULK_EXPORT_SOURCE_CHANGED = "bulk_export_source_changed"
BULK_EXPORT_SIZE_LIMIT = "bulk_export_size_limit"
BULK_EXPORT_USER_ACTIVE = "bulk_export_user_active"
BULK_EXPORT_QUEUE_FULL = "bulk_export_queue_full"
BULK_EXPORT_RATE_LIMITED = "bulk_export_rate_limited"
BULK_EXPORT_STORAGE_QUOTA = "bulk_export_storage_quota"
BULK_EXPORT_STORAGE_RESERVE = "bulk_export_storage_reserve"
BULK_EXPORT_AUDIT_UNAVAILABLE = "bulk_export_audit_unavailable"
BULK_EXPORT_NOT_READY = "bulk_export_not_ready"
BULK_EXPORT_GONE = "bulk_export_gone"
BULK_EXPORT_PART_NOT_FOUND = "bulk_export_part_not_found"
