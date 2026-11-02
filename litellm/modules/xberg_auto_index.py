import asyncio
import hashlib
import json
import logging
import os
import uuid
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

import litellm
import psycopg
from litellm.integrations.custom_logger import CustomLogger

LOGGER = logging.getLogger("xberg_auto_index")

STORE_ID = "sme-knowledge"
CHUNK_SIZE = 4_000
CHUNK_OVERLAP = 400
MAX_INDEXED_CHARACTERS = 1_000_000
MAX_CHUNKS = 256
EMBEDDING_BATCH_SIZE = 16
INDEX_TIMEOUT_SECONDS = 180
PRESIDIO_TIMEOUT_SECONDS = 30
PRESIDIO_CONCURRENCY = 4
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def _safe_source_label(input_data: dict[str, Any]) -> str:
    kind = input_data.get("kind")
    if kind == "uri":
        raw_uri = input_data.get("uri")
        if not isinstance(raw_uri, str) or not raw_uri:
            raise ValueError("Xberg URI input has no source URI")
        parsed = urlsplit(raw_uri)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            host = parsed.hostname
            if ":" in host:
                host = f"[{host}]"
            if parsed.port is not None:
                host = f"{host}:{parsed.port}"
            return urlunsplit((parsed.scheme, host, parsed.path or "/", "", ""))
        return f"local:{PurePosixPath(raw_uri).name}"
    if kind == "bytes":
        filename = input_data.get("filename")
        if isinstance(filename, str) and filename:
            return f"bytes:{PurePosixPath(filename).name}"
        return "bytes:unnamed"
    raise ValueError("Xberg extraction input kind is not indexable")


def _chunk_text(content: str) -> list[str]:
    normalized = content.strip()
    if not normalized:
        return []
    if len(normalized) > MAX_INDEXED_CHARACTERS:
        raise ValueError("Xberg extraction exceeds the automatic indexing size limit")

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + CHUNK_SIZE, len(normalized))
        if end < len(normalized):
            paragraph_break = normalized.rfind("\n\n", start + CHUNK_SIZE // 2, end)
            if paragraph_break > start:
                end = paragraph_break
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if len(chunks) > MAX_CHUNKS:
            raise ValueError("Xberg extraction produces too many index chunks")
        if end >= len(normalized):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def _parse_extraction(response_obj: Any) -> dict[str, Any]:
    for item in response_obj.mcp_tool_call_response:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
    raise ValueError("Xberg extract returned no JSON text result")


def _presidio_post(base_url: str, path: str, payload: dict[str, Any]) -> Any:
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=PRESIDIO_TIMEOUT_SECONDS) as response:
        return json.load(response)


async def _redact_content(content: str) -> str:
    analyzer_base = os.environ["PRESIDIO_ANALYZER_API_BASE"]
    anonymizer_base = os.environ["PRESIDIO_ANONYMIZER_API_BASE"]
    analyzer_results = await asyncio.to_thread(
        _presidio_post,
        analyzer_base,
        "/analyze",
        {"text": content, "language": "en"},
    )
    if not isinstance(analyzer_results, list):
        raise ValueError("Presidio analyzer returned an invalid result")
    anonymized = await asyncio.to_thread(
        _presidio_post,
        anonymizer_base,
        "/anonymize",
        {"text": content, "analyzer_results": analyzer_results},
    )
    if not isinstance(anonymized, dict) or not isinstance(anonymized.get("text"), str):
        raise ValueError("Presidio anonymizer returned an invalid result")
    return anonymized["text"]


async def _redact_chunks(chunks: list[str]) -> list[str]:
    semaphore = asyncio.Semaphore(PRESIDIO_CONCURRENCY)

    async def redact(chunk: str) -> str:
        async with semaphore:
            return await _redact_content(chunk)

    return await asyncio.gather(*(redact(chunk) for chunk in chunks))


def _trusted_principal_id(kwargs: dict[str, Any]) -> str | None:
    for metadata_key in ("litellm_metadata", "metadata"):
        metadata = kwargs.get(metadata_key)
        if not isinstance(metadata, dict):
            continue
        api_key_hash = metadata.get("user_api_key_hash")
        if isinstance(api_key_hash, str) and api_key_hash:
            pseudonymous_id = hashlib.sha256(api_key_hash.encode("utf-8")).hexdigest()
            return f"litellm-api-key-{pseudonymous_id}"
    return None


async def _embed_chunks(chunks: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    model = os.environ["XBERG_INDEX_EMBEDDING_MODEL"]
    api_base = os.environ["XBERG_INDEX_EMBEDDING_API_BASE"]
    api_key = os.environ.get("XBERG_INDEX_EMBEDDING_API_KEY") or None
    for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        response = await litellm.aembedding(
            model=model,
            api_base=api_base,
            api_key=api_key,
            input=chunks[start : start + EMBEDDING_BATCH_SIZE],
            timeout=60,
        )
        batch = sorted(response.data, key=lambda item: item["index"])
        vectors.extend(item["embedding"] for item in batch)
    if len(vectors) != len(chunks) or any(len(vector) != 1024 for vector in vectors):
        raise ValueError("bge-m3 returned an unexpected embedding count or dimension")
    return vectors


async def _replace_source_chunks(
    request_id: str,
    request_source: str,
    rows: list[tuple[str, str, str, str, str]],
) -> None:
    database_url = os.environ["VECTOR_DATABASE_URL"]
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=10) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute("SET LOCAL statement_timeout = '30s'")
            await cursor.execute("SET LOCAL lock_timeout = '10s'")
            await cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (request_id,),
            )
            await cursor.execute(
                """
                DELETE FROM embeddings
                WHERE vector_store_id = %s
                  AND (
                      metadata->>'request_id' = %s
                      OR (
                          NOT metadata ? 'request_id'
                          AND metadata->>'request_source' = %s
                      )
                  )
                """,
                (STORE_ID, request_id, request_source),
            )
            await cursor.executemany(
                """
                INSERT INTO embeddings (id, vector_store_id, content, embedding, metadata)
                VALUES (%s, %s, %s, %s::vector, %s::jsonb)
                """,
                rows,
            )
            await cursor.execute(
                """
                UPDATE vector_stores
                SET file_counts = jsonb_build_object(
                        'in_progress', 0,
                        'completed', (
                            SELECT COUNT(*)
                            FROM embeddings
                            WHERE vector_store_id = %s
                        ),
                        'failed', 0,
                        'cancelled', 0,
                        'total', (
                            SELECT COUNT(*)
                            FROM embeddings
                            WHERE vector_store_id = %s
                        )
                    ),
                    usage_bytes = COALESCE((
                        SELECT SUM(octet_length(content))
                        FROM embeddings
                        WHERE vector_store_id = %s
                    ), 0),
                    last_active_at = NOW()
                WHERE id = %s
                """,
                (STORE_ID, STORE_ID, STORE_ID, STORE_ID),
            )


async def _index_extraction(
    arguments: dict[str, Any],
    extraction: dict[str, Any],
    principal_id: str,
) -> None:
    input_data = arguments.get("input")
    if not isinstance(input_data, dict):
        raise ValueError("Xberg extract callback received no input object")

    results = extraction.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("Xberg extract returned no documents to index")

    request_source = _safe_source_label(input_data)
    request_id = hashlib.sha256(request_source.encode("utf-8")).hexdigest()
    document_inputs: list[tuple[dict[str, Any], str, str]] = []
    label_counts: dict[str, int] = {}
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("Xberg extract returned an invalid document result")
        result_metadata = result.get("metadata")
        if not isinstance(result_metadata, dict):
            result_metadata = {}
        additional = result_metadata.get("additional")
        if not isinstance(additional, dict):
            additional = {}
        result_source = additional.get("final_uri") or additional.get("source_uri")
        source_input = {"kind": "uri", "uri": result_source} if isinstance(result_source, str) else input_data
        source_label = _safe_source_label(source_input)
        label_counts[source_label] = label_counts.get(source_label, 0) + 1
        document_inputs.append((result, source_label, request_source))

    prepared: list[tuple[str, str, list[str], dict[str, Any]]] = []
    total_characters = 0
    total_chunks = 0
    for result_index, (result, source_label, _) in enumerate(document_inputs):
        content = result.get("content")
        if not isinstance(content, str):
            raise ValueError("Xberg extract returned a document without text content")
        result_metadata = result.get("metadata")
        if not isinstance(result_metadata, dict):
            result_metadata = {}
        source_key = source_label if label_counts[source_label] == 1 else f"{source_label}\n{result_index}"
        source_id = hashlib.sha256(source_key.encode("utf-8")).hexdigest()
        raw_chunks = _chunk_text(content)
        chunks = await _redact_chunks(raw_chunks)
        content_hash = hashlib.sha256("\n".join(chunks).encode("utf-8")).hexdigest()
        total_characters += len(content)
        total_chunks += len(chunks)
        if total_characters > MAX_INDEXED_CHARACTERS or total_chunks > MAX_CHUNKS:
            raise ValueError("Xberg extraction exceeds automatic indexing limits")
        prepared.append((source_id, content_hash, chunks, {
            "request_id": request_id,
            "source_id": source_id,
            "source": source_label,
            "request_source": request_source,
            "indexed_by_principal": principal_id,
            "content_hash": content_hash,
            "mime_type": result.get("mime_type"),
            "title": result_metadata.get("title"),
            "indexed_by": "xberg-auto-index",
        }))

    all_chunks = [chunk for _, _, chunks, _ in prepared for chunk in chunks]
    vectors = await _embed_chunks(all_chunks)
    rows: list[tuple[str, str, str, str, str]] = []
    vector_index = 0
    for source_id, content_hash, chunks, base_metadata in prepared:
        for chunk_index, chunk in enumerate(chunks):
            row_metadata = {
                **base_metadata,
                "chunk_index": chunk_index,
                "chunk_count": len(chunks),
            }
            row_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_id}:{content_hash}:{chunk_index}"))
            rows.append((
                row_id,
                STORE_ID,
                chunk,
                json.dumps(vectors[vector_index], separators=(",", ":")),
                json.dumps(row_metadata, separators=(",", ":"), ensure_ascii=True),
            ))
            vector_index += 1

    await _replace_source_chunks(request_id, request_source, rows)
    LOGGER.info(
        "Indexed Xberg extraction into %s: documents=%d chunks=%d",
        STORE_ID,
        len(prepared),
        len(rows),
    )


def _log_background_result(task: asyncio.Task[None]) -> None:
    _BACKGROUND_TASKS.discard(task)
    if task.cancelled():
        LOGGER.warning("Xberg automatic indexing task was cancelled")
        return
    exception = task.exception()
    if exception is not None:
        LOGGER.error(
            "Xberg automatic indexing failed: %s",
            exception,
            exc_info=(type(exception), exception, exception.__traceback__),
        )


class XbergAutoIndexCallback(CustomLogger):
    async def async_post_mcp_tool_call_hook(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        metadata = kwargs.get("mcp_tool_call_metadata")
        if not isinstance(metadata, dict):
            return
        if metadata.get("mcp_server_name") != "xberg" or metadata.get("name") != "extract":
            return
        if os.environ.get("XBERG_AUTO_INDEX_ENABLED", "").lower() != "true":
            return

        principal_id = _trusted_principal_id(kwargs)
        if principal_id is None:
            LOGGER.warning(
                "Skipped Xberg automatic indexing because LiteLLM supplied no API-key hash"
            )
            return

        arguments = metadata.get("arguments")
        if not isinstance(arguments, dict):
            raise ValueError("Xberg extract callback received no tool arguments")
        extraction = _parse_extraction(response_obj)
        task = asyncio.create_task(
            asyncio.wait_for(
                _index_extraction(arguments, extraction, principal_id),
                timeout=INDEX_TIMEOUT_SECONDS,
            ),
            name="xberg-auto-index",
        )
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_log_background_result)


proxy_handler_instance = XbergAutoIndexCallback()
