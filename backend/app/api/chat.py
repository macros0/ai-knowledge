"""Роут чата: RAG — поиск по Qdrant + синтез ответа LLM с источниками."""
from fastapi import APIRouter

from app.models.schemas import ChatRequest, ChatResponse, ChatSource
from app.prompts.okf import SYSTEM_CHAT_PROMPT, USER_CHAT_PROMPT
from app.services.embedder import Embedder
from app.services.llm_client import LLMClient
from app.services.vector_store import VectorStore

router = APIRouter(prefix="/chat", tags=["chat"])

_embedder = Embedder()
_vector_store = VectorStore()
_llm = LLMClient()


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
    answer = _llm.chat(SYSTEM_CHAT_PROMPT, USER_CHAT_PROMPT.format(context=context, query=req.query))
    return ChatResponse(query=req.query, answer=answer, sources=sources)
