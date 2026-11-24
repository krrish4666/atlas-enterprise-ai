"""app/rag/generator.py — Grounded answer synthesis using Google Gemini API.

Implements ADR-005 and docs/PLAN.md:
- Uses langchain-google-genai (ChatGoogleGenerativeAI) with Gemini 2.0 Flash.
- Enforces strict document grounding and inline bracket citations ([1], [2]).
- Provides graceful fallback when context is absent or question is unanswerable.
"""

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Atlas Enterprise AI, an enterprise assistant answering employee questions based strictly and solely on the provided organizational documents.

STRICT GROUNDING RULES:
1. Ground your response entirely on the provided Retrieved Context below. Do not assume, invent, or extrapolate facts not explicitly stated.
2. If the user's question cannot be answered using the provided context, state clearly and politely:
   "I could not find information about this in the uploaded organizational documents."
3. Never invent facts, company policies, dollar amounts, contact info, or procedures.
4. When stating facts from the context, insert inline bracket citation numbers like [1], [2] referencing the source document chunk where that fact is found.
5. If multiple sources corroborate a point, you may cite them together like [1][2].
"""

FALLBACK_NO_DOCS = (
    "I could not find information about this in the uploaded organizational documents."
)


def format_retrieved_context(chunks: list[dict[str, Any]]) -> str:
    """Format a list of retrieved chunks into numbered context blocks for the LLM.

    Capped at top 3 chunks and max 450 characters per chunk to stay well within
    Gemini Free Tier token and deadline constraints.
    """
    if not chunks:
        return "No relevant documents found."

    blocks: list[str] = []
    for idx, c in enumerate(chunks[:3], start=1):
        filename = c.get("filename", "unknown")
        meta = c.get("metadata") or {}
        page = meta.get("page", 1)
        raw_content = (c.get("content") or "").strip()
        if len(raw_content) > 450:
            content = raw_content[:450].rsplit(" ", 1)[0] + "..."
        else:
            content = raw_content
        blocks.append(f"[{idx}] (Document: {filename}, Page: {page}):\n{content}")

    return "\n\n".join(blocks)


def build_citations(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert retrieved chunks into API citation objects matching SCHEMA_AND_API.md."""
    citations: list[dict[str, Any]] = []
    for c in chunks[:3]:
        content_text = c.get("content") or ""
        snippet = c.get("snippet") or content_text[:200]
        citations.append(
            {
                "document_id": str(c.get("document_id")),
                "filename": c.get("filename", "unknown"),
                "chunk_index": c.get("chunk_index", 0),
                "score": float(c.get("score", 0.0)),
                "snippet": snippet,
            }
        )
    return citations


def get_llm(callbacks: list[Any] | None = None) -> ChatGoogleGenerativeAI:
    """Instantiate ChatGoogleGenerativeAI with project settings."""
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.google_api_key,
        temperature=0.0,
        max_retries=3,
        timeout=120.0,
        callbacks=callbacks or [],
    )


async def generate_answer(
    query: str,
    retrieved_chunks: list[dict[str, Any]],
    chat_history: list[dict[str, str]] | None = None,
    callbacks: list[Any] | None = None,
    llm: Any | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Synthesize a grounded answer using Gemini API with citations.

    Args:
        query: User prompt.
        retrieved_chunks: List of chunk dictionaries retrieved from pgvector.
        chat_history: Optional recent conversation turns.
        callbacks: Optional Langfuse callback handler.
        llm: Optional preconfigured LLM instance (for testing/mocking).

    Returns:
        Tuple of (answer_text, citations_list).
    """
    citations = build_citations(retrieved_chunks)

    # If no chunks were retrieved at all, return the standard fallback directly
    if not retrieved_chunks:
        return FALLBACK_NO_DOCS, []

    formatted_context = format_retrieved_context(retrieved_chunks)

    # Build prompt messages
    messages: list[Any] = [SystemMessage(content=SYSTEM_PROMPT)]

    # Add conversation history turns (if present, limited to last 2 messages)
    if chat_history:
        recent_history = chat_history[-2:] if len(chat_history) > 2 else chat_history
        for turn in recent_history:
            role = turn.get("role")
            content = turn.get("content", "")
            if role == "user":
                # Limit previous user query to 300 chars
                messages.append(HumanMessage(content=content[:300]))
            elif role == "assistant":
                # Truncate previous assistant response to 250 chars for compact context
                trimmed = content[:250] + ("..." if len(content) > 250 else "")
                messages.append(AIMessage(content=trimmed))

    # Add user query with formatted grounding context
    user_prompt = f"Retrieved Context:\n{formatted_context}\n\nUser Question: {query}"
    messages.append(HumanMessage(content=user_prompt))

    # Check for Gemini API key
    active_llm = llm
    if active_llm is None:
        if (
            not settings.google_api_key
            or settings.google_api_key == "your_google_gemini_api_key_here"
        ):
            logger.warning("GOOGLE_API_KEY is not configured; using offline grounded fallback.")
            return (
                f"[Offline Demo Grounded Answer] Based on {citations[0]['filename']} [1]: "
                f"{retrieved_chunks[0]['content'][:150]}...",
                citations,
            )
        active_llm = get_llm(callbacks=callbacks)

    try:
        response = await active_llm.ainvoke(messages)
        if hasattr(response, "content"):
            content = response.content
            if isinstance(content, list):
                text_parts: list[str] = []
                for part in content:
                    if isinstance(part, str):
                        text_parts.append(part)
                    elif isinstance(part, dict) and "text" in part:
                        text_parts.append(str(part["text"]))
                    elif hasattr(part, "text"):
                        text_parts.append(str(getattr(part, "text", "")))
                    else:
                        text_parts.append(str(part))
                answer = "".join(text_parts)
            else:
                answer = str(content)
        else:
            answer = str(response)
        return answer.strip(), citations
    except Exception as exc:
        logger.error("Gemini answer generation failed: %s", exc)
        raise RuntimeError(f"LLM answer generation failed: {exc}") from exc
