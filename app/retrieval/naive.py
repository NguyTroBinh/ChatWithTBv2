from __future__ import annotations
from app.core.config import CHUNK_VECTOR_INDEX
from app.graph.neo4j_store import Neo4jGraphStore
from app.providers.embedding import EmbeddingService

class NaiveRetrievalService:
    def __init__(
        self,
        graph_store: Neo4jGraphStore,
        embedding_service: EmbeddingService,
        vector_index_name: str = CHUNK_VECTOR_INDEX,
        top_k: int = 5,
    ):
        self.graph_store = graph_store
        self.embedding_service = embedding_service
        self.vector_index_name = vector_index_name
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

        k = top_k or self.top_k
        if k <= 0:
            raise ValueError("top_k must be greater than 0")

        query_embedding = self.embedding_service.embed_query(query)
        with self.graph_store._session() as session:
            if document_ids is None:
                records = session.execute_read(
                    self._vector_search,
                    self.vector_index_name,
                    query_embedding,
                    k,
                )
            else:
                records = session.execute_read(
                    self._scoped_vector_search,
                    query_embedding,
                    k,
                    document_ids,
                )
        return [self._record_to_chunk(record) for record in records]

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
