"""Роут чата: RAG — поиск по Qdrant + синтез ответа LLM с источниками."""
from fastapi import APIRouter

from app.models.schemas import ChatRequest, ChatResponse, ChatSource
from app.prompts.store import get_store
from app.services.embedder import Embedder
from app.services.llm_client import LLMClient
from app.services.vector_store import VectorStore

router = APIRouter(prefix="/chat", tags=["chat"])

_embedder = Embedder()
_vector_store = VectorStore()
_llm = LLMClient(interactive=True)
_prompts = get_store()


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest):
    vector = _embedder.embed(req.query)
    hits = _vector_store.search(vector, tags=req.tags or None, top_k=req.top_k)

    context_parts = []
    sources = []
    for i, hit in enumerate(hits, start=1):
        payload = hit["payload"]
        title = payload.get("title", "Без названия")
        content = payload.get("content", "")
        context_parts.append(f"[{i}] ({title})\n{content}")
        sources.append(
            ChatSource(
                title=title,
                filepath=payload.get("filepath", ""),
                score=round(hit["score"], 4),
                tags=payload.get("tags", []),
            )
        )

    context = "\n\n".join(context_parts) or "Контекст пуст."
    system = _prompts.get("chat_system")
    user = _prompts.format("chat_user", context=context, query=req.query)
    answer = _llm.chat(system, user)
    return ChatResponse(query=req.query, answer=answer, sources=sources)
