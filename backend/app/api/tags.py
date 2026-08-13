"""Роут глобального справочника тегов."""
from fastapi import APIRouter

from app.models.schemas import TagListOut, TagOut
from app.services.tag_registry import TagRegistry

router = APIRouter(prefix="/tags", tags=["tags"])

_tag_registry = TagRegistry()


@router.get("", response_model=TagListOut)
def list_tags():
    return TagListOut(tags=[TagOut(**item) for item in _tag_registry.all()])
