"""Hermetic Qdrant boundary with real writer IDs and publication readback."""
from qdrant_client import QdrantClient
from app.services.vector_store import VectorStore


def use_memory_qdrant(pipeline, monkeypatch):
    monkeypatch.setattr(pipeline.vector_store, 'client', QdrantClient(':memory:'))
    VectorStore.ensure_collection(pipeline.vector_store)
