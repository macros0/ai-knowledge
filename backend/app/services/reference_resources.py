"""Typed reference identities and access checks, separate from lease mechanics."""
from dataclasses import dataclass
import json
import re

from app.db import models

NUMERIC_TYPES = {"glossary_term", "glossary_rule", "development", "tag"}
LOCALE_TYPES = {"stopword_set", "ui_dictionary", "locale"}
RESOURCE_TYPES = NUMERIC_TYPES | LOCALE_TYPES | {"attribute_catalog"}
LEASE_TYPES = NUMERIC_TYPES | {"stopword_set", "ui_dictionary"}


@dataclass(frozen=True)
class ReferenceKey:
    resource_type: str
    resource_id: str
    kind: str | None = None
    org_id: int | None = None

    def __post_init__(self):
        if self.resource_type not in RESOURCE_TYPES:
            raise ValueError("Unknown reference resource type")
        identifier = str(self.resource_id)
        if self.resource_type in NUMERIC_TYPES:
            if not re.fullmatch(r"[0-9]{1,18}", identifier) or int(identifier) < 1:
                raise ValueError("Invalid reference ID")
            identifier = str(int(identifier))
        elif self.resource_type in LOCALE_TYPES:
            if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", identifier):
                raise ValueError("Invalid reference locale")
        elif not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", identifier):
            raise ValueError("Invalid attribute key")
        if self.resource_type == "stopword_set":
            if self.kind not in {"bm25", "marker"}:
                raise ValueError("Invalid stopword kind")
        elif self.kind is not None:
            raise ValueError("Unexpected kind")
        if self.org_id is not None and (self.resource_type != "attribute_catalog" or
                type(self.org_id) is not int or self.org_id < 1):
            raise ValueError("Invalid organization scope")
        object.__setattr__(self, "resource_id", identifier)

    @property
    def canonical(self):
        return json.dumps([self.resource_type, self.resource_id, self.kind, self.org_id], separators=(",", ":"))


def require_access(key, actor, *, admin=False):
    from app.config import get_settings
    from app.services.reference_concurrency import ReferenceError
    if get_settings().auth_provider == "disabled":
        return
    required = {"admin"} if admin or key.resource_type in {"locale", "ui_dictionary", "stopword_set", "glossary_rule"} else {"editor", "admin"}
    if not actor or not required.intersection(actor.roles):
        raise ReferenceError("forbidden", 403)


def require_resource(session, key):
    from app.services.reference_concurrency import ReferenceError
    entity = {"glossary_term": models.DomainTerm, "glossary_rule": models.GlossaryInfotypeRule,
              "development": models.Development, "tag": models.Tag}.get(key.resource_type)
    if entity:
        if key.resource_type.startswith("glossary_"):
            state = session.get(models.GlossaryState, 1, populate_existing=True)
            if state is not None and not state.identities_ready:
                raise ReferenceError("glossary_migration_required", 503)
        resource = session.get(entity, int(key.resource_id), populate_existing=True)
        if resource is None or (key.resource_type == "tag" and resource.deleted_at is not None):
            raise ReferenceError("not_found", 404)
        return resource
    if key.resource_type in LOCALE_TYPES:
        resource = session.get(models.Locale, key.resource_id, populate_existing=True)
        if resource is None:
            raise ReferenceError("locale_not_found", 404)
        return resource
    return None  # An empty typed attribute catalog is a valid resource.
