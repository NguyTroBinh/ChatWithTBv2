from __future__ import annotations
import re

from app.core.config import CHUNK_FULLTEXT_INDEX, CHUNK_VECTOR_INDEX
from app.graph.neo4j_store import Neo4jGraphStore
from app.providers.embedding import EmbeddingService

class NaiveRetrievalService:
    def __init__(
        self,
        graph_store: Neo4jGraphStore,
        embedding_service: EmbeddingService,
        vector_index_name: str = CHUNK_VECTOR_INDEX,
        fulltext_index_name: str = CHUNK_FULLTEXT_INDEX,
        top_k: int = 5,
    ):
        self.graph_store = graph_store
        self.embedding_service = embedding_service
        self.vector_index_name = vector_index_name
        self.fulltext_index_name = fulltext_index_name
        self.top_k = top_k

    @classmethod
    def from_config(cls) -> "NaiveRetrievalService":
        return cls(
            graph_store=Neo4jGraphStore.from_config(),
            embedding_service=EmbeddingService(),
        )

    def close(self) -> None:
        self.graph_store.close()

    def retrieve(self, query: str, top_k: int | None = None, document_ids: list[str] | None = None) -> list[dict]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")
        if document_ids is not None:
            document_ids = [document_id.strip() for document_id in document_ids if document_id.strip()]
            if not document_ids:
                raise ValueError("document_ids cannot be empty")

        k = self.top_k if top_k is None else top_k
        if k <= 0:
            raise ValueError("top_k must be greater than 0")

        query_embedding = self.embedding_service.embed_query(query)
        fulltext_query = self._fulltext_query(query)
        fulltext_terms = self._fulltext_terms(query)
        with self.graph_store._session() as session:
            if document_ids is None:
                vector_records = session.execute_read(
                    self._vector_search,
                    self.vector_index_name,
                    query_embedding,
                    k,
                )
                fulltext_records = session.execute_read(
                    self._fulltext_search,
                    self.fulltext_index_name,
                    fulltext_query,
                    k,
                ) if fulltext_query else []
            else:
                vector_records = session.execute_read(
                    self._scoped_vector_search,
                    query_embedding,
                    k,
                    document_ids,
                )
                fulltext_records = session.execute_read(
                    self._scoped_fulltext_search,
                    fulltext_terms,
                    k,
                    document_ids,
                ) if fulltext_terms else []
        return self._rrf_merge(
            [
                ("vector", [self._record_to_chunk(record) for record in vector_records]),
                ("fulltext", [self._record_to_chunk(record) for record in fulltext_records]),
            ],
            k,
        )

    @staticmethod
    def _vector_search(tx, vector_index_name: str, query_embedding: list[float], top_k: int):
        result = tx.run(
            """
CALL db.index.vector.queryNodes($index_name, $top_k, $query_embedding)
YIELD node, score
RETURN
  node.id AS chunk_id,
  node.documentId AS document_id,
  node.fileName AS file_name,
  node.text AS content,
  node.position AS position,
  node.tokenCount AS token_count,
  node.pageStart AS page_start,
  node.pageEnd AS page_end,
  node.charStart AS char_start,
  node.charEnd AS char_end,
  node.chunkHash AS chunk_hash,
  node.sectionPath AS section_path,
  score
ORDER BY score DESC
""",
            index_name=vector_index_name,
            top_k=top_k,
            query_embedding=[float(value) for value in query_embedding],
        )
        return list(result)

    @staticmethod
    def _scoped_vector_search(tx, query_embedding: list[float], top_k: int, document_ids: list[str]):
        # ponytail: scans chunks in selected docs; upgrade to native pre-filtered vector search when corpus grows.
        result = tx.run(
            """
MATCH (node:Chunk)
WHERE node.documentId IN $document_ids
  AND node.embedding IS NOT NULL
WITH node, vector.similarity.cosine(node.embedding, $query_embedding) AS score
RETURN
  node.id AS chunk_id,
  node.documentId AS document_id,
  node.fileName AS file_name,
  node.text AS content,
  node.position AS position,
  node.tokenCount AS token_count,
  node.pageStart AS page_start,
  node.pageEnd AS page_end,
  node.charStart AS char_start,
  node.charEnd AS char_end,
  node.chunkHash AS chunk_hash,
  node.sectionPath AS section_path,
  score
ORDER BY score DESC
LIMIT $top_k
""",
            document_ids=document_ids,
            top_k=top_k,
            query_embedding=[float(value) for value in query_embedding],
        )
        return list(result)

    @staticmethod
    def _fulltext_search(tx, fulltext_index_name: str, query: str, top_k: int):
        result = tx.run(
            """
CALL db.index.fulltext.queryNodes($index_name, $query)
YIELD node, score
RETURN
  node.id AS chunk_id,
  node.documentId AS document_id,
  node.fileName AS file_name,
  node.text AS content,
  node.position AS position,
  node.tokenCount AS token_count,
  node.pageStart AS page_start,
  node.pageEnd AS page_end,
  node.charStart AS char_start,
  node.charEnd AS char_end,
  node.chunkHash AS chunk_hash,
  node.sectionPath AS section_path,
  score
ORDER BY score DESC
LIMIT $top_k
""",
            index_name=fulltext_index_name,
            query=query,
            top_k=top_k,
        )
        return list(result)

    @staticmethod
    def _scoped_fulltext_search(tx, terms: list[str], top_k: int, document_ids: list[str]):
        # ponytail: filters selected docs first, then scans their chunks; upgrade to per-document fulltext indexes if scope grows large.
        result = tx.run(
            """
MATCH (node:Chunk)
WHERE node.documentId IN $document_ids
WITH node, toLower(node.text) AS text
WITH node, [term IN $terms WHERE text CONTAINS term] AS matched_terms
WITH node, size(matched_terms) AS hit_count
WHERE hit_count > 0
WITH node, toFloat(hit_count) / size($terms) AS score
RETURN
  node.id AS chunk_id,
  node.documentId AS document_id,
  node.fileName AS file_name,
  node.text AS content,
  node.position AS position,
  node.tokenCount AS token_count,
  node.pageStart AS page_start,
  node.pageEnd AS page_end,
  node.charStart AS char_start,
  node.charEnd AS char_end,
  node.chunkHash AS chunk_hash,
  node.sectionPath AS section_path,
  score
ORDER BY score DESC, node.position ASC
LIMIT $top_k
""",
            terms=terms,
            top_k=top_k,
            document_ids=document_ids,
        )
        return list(result)

    @staticmethod
    def _record_to_chunk(record) -> dict:
        return {
            "id": record["chunk_id"],
            "file_name": record["file_name"],
            "content": record["content"],
            "score": record["score"],
            "metadata": {
                "chunk_id": record["chunk_id"],
                "document_id": record["document_id"],
                "position": record["position"],
                "token_count": record["token_count"],
                "page_start": record["page_start"],
                "page_end": record["page_end"],
                "char_start": record["char_start"],
                "char_end": record["char_end"],
                "chunk_hash": record["chunk_hash"],
                "section_path": record["section_path"],
            },
        }

    @staticmethod
    def _fulltext_query(query: str) -> str:
        # ponytail: plain term query avoids Lucene escaping edge cases; upgrade to phrase/boolean parsing for expert search.
        return " ".join(re.findall(r"\w+", query, flags=re.UNICODE))

    @staticmethod
    def _fulltext_terms(query: str) -> list[str]:
        return [term.lower() for term in re.findall(r"\w+", query, flags=re.UNICODE)]

    @staticmethod
    def _rrf_merge(result_sets: list[tuple[str, list[dict]]], top_k: int, rrf_k: int = 60) -> list[dict]:
        merged = {}
        for source, chunks in result_sets:
            for rank, chunk in enumerate(chunks, start=1):
                chunk_id = chunk.get("id")
                if not chunk_id:
                    continue
                if chunk_id not in merged:
                    merged[chunk_id] = {
                        **chunk,
                        "metadata": dict(chunk.get("metadata") or {}),
                        "score": 0.0,
                    }
                item = merged[chunk_id]
                item["score"] += 1 / (rrf_k + rank)

        return sorted(
            merged.values(),
            key=lambda item: (
                item["score"],
                -((item.get("metadata") or {}).get("position") or 0),
            ),
            reverse=True,
        )[:top_k]
