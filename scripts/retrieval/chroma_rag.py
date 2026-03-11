from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def filter_chunks_by_source_type(chunks: list[dict[str, Any]], source_type: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in chunks:
        md = c.get("metadata", {}) or {}
        if str(md.get("source_type", "")).lower() == source_type.lower():
            out.append(c)
    return out


@dataclass
class IndexResult:
    ok: bool
    error: str | None
    records_indexed: int
    records_total: int
    chroma_result: dict[str, Any] | None


def index_lecture_chunks_to_chroma(
    *,
    chunks_jsonl: Path,
    chroma_path: str,
    chroma_collection: str,
    chroma_batch_size: int,
    lecture_source_type: str = "lecture",
) -> IndexResult:
    from storage.chroma_store import try_store_chroma

    all_chunks = read_jsonl(chunks_jsonl)
    lecture_chunks = filter_chunks_by_source_type(all_chunks, lecture_source_type)
    chroma_result = try_store_chroma(
        chunks=lecture_chunks,
        chroma_path=chroma_path,
        chroma_collection=chroma_collection,
        chroma_batch_size=chroma_batch_size,
    )
    ok = bool(chroma_result.get("ok")) if chroma_result.get("enabled") else False
    err = chroma_result.get("error") if not ok else None
    return IndexResult(
        ok=ok,
        error=err,
        records_indexed=len(lecture_chunks),
        records_total=len(all_chunks),
        chroma_result=chroma_result,
    )


@dataclass
class RetrievalItem:
    query_chunk_id: str
    query_source_path: str
    query_content_type: str
    query_source_type: str
    query_text: str
    top_k: int
    results: dict[str, Any]


def _filter_query_chunks(
    chunks: list[dict[str, Any]],
    source_type: str,
    assignment_id: str | None,
) -> list[dict[str, Any]]:
    """Filter chunks to those matching source_type, and optionally assignment_id."""
    matched = filter_chunks_by_source_type(chunks, source_type)
    if assignment_id is not None:
        matched = [
            c for c in matched
            if str((c.get("metadata") or {}).get("assignment_id", "")) == str(assignment_id)
        ]
    return matched


def retrieve_lecture_context(
    *,
    chroma_path: str,
    chroma_collection: str,
    chunks_jsonl: Path,
    top_k: int,
    out_jsonl: Path,
    query_source_type: str = "rubric",
    assignment_id: str | None = None,
) -> dict[str, Any]:
    """
    Retrieve relevant lecture chunks from ChromaDB by querying with chunks of
    a specified source_type (default "rubric").

    Using rubric/assignment/reference chunks as the query is preferred over student
    chunks because the rubric defines what needs to be graded — so the retrieved
    lectures will be directly relevant to the assignment topics.

    Results are written to out_jsonl (one line per queried chunk).  The same
    retrieval_results.jsonl can then be passed to grade_submission.
    """
    try:
        import chromadb
        from chromadb.utils import embedding_functions
    except Exception as exc:
        raise RuntimeError(f"chromadb import failed: {exc}") from exc

    all_chunks = read_jsonl(chunks_jsonl)
    query_chunks = _filter_query_chunks(all_chunks, query_source_type, assignment_id)

    if not query_chunks:
        raise RuntimeError(
            f"No chunks found with source_type='{query_source_type}'"
            + (f" and assignment_id='{assignment_id}'" if assignment_id else "")
            + f" in {chunks_jsonl}"
        )

    client = chromadb.PersistentClient(path=chroma_path)

    # Try OpenAI embeddings; fall back to ChromaDB local embeddings on any error.
    embed_fn = None
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        for model in ("text-embedding-3-small", "text-embedding-ada-002"):
            try:
                fn = embedding_functions.OpenAIEmbeddingFunction(
                    model_name=model,
                    api_key=api_key,
                )
                fn(["test"])
                embed_fn = fn
                break
            except Exception:
                continue

    if embed_fn is not None:
        collection = client.get_or_create_collection(name=chroma_collection, embedding_function=embed_fn)
    else:
        collection = client.get_or_create_collection(name=chroma_collection)

    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out_jsonl.open("w", encoding="utf-8") as f:
        for c in query_chunks:
            md = c.get("metadata", {}) or {}
            query_text = str(c.get("content", "") or "").strip()
            if not query_text:
                continue
            q = collection.query(query_texts=[query_text], n_results=int(top_k))
            item = RetrievalItem(
                query_chunk_id=str(c.get("id", "")),
                query_source_path=str(md.get("source_path", "")),
                query_content_type=str(md.get("content_type", "")),
                query_source_type=str(md.get("source_type", query_source_type)),
                query_text=query_text,
                top_k=int(top_k),
                results=q,
            )
            f.write(json.dumps(item.__dict__, ensure_ascii=True) + "\n")
            written += 1

    return {
        "ok": True,
        "query_source_type": query_source_type,
        "assignment_id": assignment_id,
        "query_chunks_considered": len(query_chunks),
        "queries_written": written,
        "out_jsonl": str(out_jsonl),
        "chroma_path": chroma_path,
        "chroma_collection": chroma_collection,
        "top_k": int(top_k),
    }


# Backward-compatible alias (old code queried with student chunks).
def retrieve_lecture_context_for_student_chunks(
    *,
    chroma_path: str,
    chroma_collection: str,
    student_chunks_jsonl: Path,
    top_k: int,
    out_jsonl: Path,
    student_source_type: str = "student",
) -> dict[str, Any]:
    return retrieve_lecture_context(
        chroma_path=chroma_path,
        chroma_collection=chroma_collection,
        chunks_jsonl=student_chunks_jsonl,
        top_k=top_k,
        out_jsonl=out_jsonl,
        query_source_type=student_source_type,
        assignment_id=None,
    )


__all__ = [
    "index_lecture_chunks_to_chroma",
    "retrieve_lecture_context",
    "retrieve_lecture_context_for_student_chunks",
    "IndexResult",
    "RetrievalItem",
]

