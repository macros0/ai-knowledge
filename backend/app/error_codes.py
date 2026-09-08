# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

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
