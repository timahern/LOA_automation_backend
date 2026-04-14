"""
Query ChromaDB and return a cited answer from gpt-4o-mini.
"""
from __future__ import annotations

import os
from pathlib import Path

import chromadb
from openai import OpenAI

CHROMA_DIR = Path(__file__).parent / "chroma_db"

_chroma_client: chromadb.PersistentClient | None = None
_collections: dict = {}


def _get_collection(source: str):
    global _chroma_client, _collections
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    if source not in _collections:
        _collections[source] = _chroma_client.get_collection(name=source)
    return _collections[source]


def _embed_question(client: OpenAI, question: str) -> list[float]:
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=[question],
    )
    return response.data[0].embedding


SYSTEM_PROMPT = (
    "You are a helpful assistant answering questions about the company Procedure Manual. "
    "Answer using only the context provided. "
    "When citing information, use the exact format: "
    "'As stated on page X of the Procedure Manual, ...' "
    "If the provided context does not contain enough information to answer the question, "
    "say so clearly rather than guessing."
)


def query(
    question: str,
    source: str = "procedure-manual",
    n_results: int = 5,
    history: list[dict] | None = None,
) -> dict:
    """
    Embed question, retrieve top chunks, return a cited GPT-4o-mini answer.

    Args:
        history: Prior turns as [{"role": "user"|"assistant", "content": str}, ...]
                 Do not include the current question — pass it via `question`.

    Returns:
        {"answer": str, "pages": list[int], "source": str}
    """
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    collection    = _get_collection(source)

    query_embedding = _embed_question(openai_client, question)

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        include=["documents", "metadatas"],
    )

    documents: list[str] = results["documents"][0]
    metadatas: list[dict] = results["metadatas"][0]

    context = "\n\n---\n\n".join(
        f"[Page {meta['page']}]\n{doc}"
        for doc, meta in zip(documents, metadatas)
    )
    pages = sorted({meta["page"] for meta in metadatas})

    gpt_messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    if history:
        gpt_messages.extend({"role": m["role"], "content": m["content"]} for m in history)

    gpt_messages.append({
        "role": "user",
        "content": f"Context from the Procedure Manual:\n\n{context}\n\nQuestion: {question}",
    })

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        messages=gpt_messages,
    )

    return {
        "answer": response.choices[0].message.content,
        "pages":  pages,
        "source": source,
    }
