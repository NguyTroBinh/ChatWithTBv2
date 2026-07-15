from __future__ import annotations
import re

from app.core.config import (
    CHUNK_FULLTEXT_INDEX,
    ENTITY_FULLTEXT_INDEX,
    ENTITY_VECTOR_INDEX,
)
from app.graph.neo4j_store import Neo4jGraphStore
from app.providers.embedding import EmbeddingService
from app.retrieval.naive import NaiveRetrievalService


class LocalRetrievalService:
    def __init__(
        self,
        graph_store: Neo4jGraphStore,
        embedding_service: EmbeddingService,
        entity_vector_index: str = ENTITY_VECTOR_INDEX,
        entity_fulltext_index: str = ENTITY_FULLTEXT_INDEX,
        chunk_fulltext_index: str = CHUNK_FULLTEXT_INDEX,
        top_k: int = 5,
        entity_top_k: int = 10,
        context_window: int = 1,
    ):
        self.graph_store = graph_store
        self.embedding_service = embedding_service
        self.entity_vector_index = entity_vector_index
        self.entity_fulltext_index = entity_fulltext_index
        self.chunk_fulltext_index = chunk_fulltext_index
        self.top_k = top_k
        self.entity_top_k = entity_top_k
        self.context_window = context_window

    @classmethod
    def from_config(cls) -> "LocalRetrievalService":
        return cls(
            graph_store=Neo4jGraphStore.from_config(),
            embedding_service=EmbeddingService(),
        )

    def close(self) -> None:
        self.graph_store.close()

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        document_ids: list[str] | None = None,
    ) -> list[dict]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")

        k = top_k if top_k is not None else self.top_k

        # Step 1: extract keywords từ query
        keywords = self._extract_keywords(query)

        # Step 2: tìm entity bằng vector + fulltext → RRF merge, scoped theo document_ids
        query_embedding = self.embedding_service.embed_query(query)
        with self.graph_store._session() as session:
            vec_entities = session.execute_read(
                self._entity_vector_search,
                self.entity_vector_index,
                query_embedding,
                self.entity_top_k,
                document_ids,
            )
            ft_entities = session.execute_read(
                self._entity_fulltext_search,
                self.entity_fulltext_index,
                " ".join(keywords),
                self.entity_top_k,
                document_ids,
            ) if keywords else []

        entities = self._rrf_merge_entities(
            [("vector", vec_entities), ("fulltext", ft_entities)],
            self.entity_top_k,
        )
        if not entities:
            return []

        entity_ids = [e["id"] for e in entities]

        with self.graph_store._session() as session:
            # Step 3: lấy chunk mention entity
            mention_chunks = session.execute_read(
                self._chunks_mentioning_entities,
                entity_ids,
                document_ids,
                k * 2,
            )

            # Step 4: lấy relationship lân cận entity
            relationships = session.execute_read(
                self._entity_relationships,
                entity_ids,
            )

            # Step 5: lấy community chứa entity
            communities = session.execute_read(
                self._entity_communities,
                entity_ids,
            )

            # Step 6: context expansion — lấy prev/next chunk
            mention_chunk_ids = [c["id"] for c in mention_chunks]
            neighbour_chunks = session.execute_read(
                self._neighbour_chunks,
                mention_chunk_ids,
                document_ids,
                self.context_window,
            ) if mention_chunk_ids else []

        # Merge mention chunks + neighbour chunks, dedup theo id
        seen: set[str] = set()
        all_chunks: list[dict] = []
        for chunk in mention_chunks + neighbour_chunks:
            if chunk["id"] not in seen:
                seen.add(chunk["id"])
                all_chunks.append(chunk)

        # Gắn relationship và community context vào metadata để answer_generator dùng
        rel_context = self._format_relationships(relationships)
        community_context = self._format_communities(communities)
        for chunk in all_chunks:
            chunk["metadata"]["relationship_context"] = rel_context
            chunk["metadata"]["community_context"] = community_context
            chunk["metadata"]["matched_entities"] = [e["canonicalName"] for e in entities[:5]]

        return all_chunks[:k * 2]  # reranker sẽ chọn top_k cuối

    # ── Neo4j reads ───────────────────────────────────────────────────────────

    @staticmethod
    def _entity_vector_search(
        tx,
        index_name: str,
        query_embedding: list[float],
        top_k: int,
        document_ids: list[str] | None,
    ) -> list[dict]:
        doc_filter = "MATCH (c:Chunk)-[:MENTIONS]->(e) WHERE c.documentId IN $document_ids" if document_ids else ""
        result = tx.run(
            f"""
CALL db.index.vector.queryNodes($index_name, $top_k, $query_embedding)
YIELD node AS e, score
{doc_filter}
RETURN DISTINCT e.id AS id, e.canonicalName AS canonicalName,
       e.type AS type, e.description AS description, score
ORDER BY score DESC
LIMIT $top_k
""",
            index_name=index_name,
            top_k=top_k,
            query_embedding=[float(v) for v in query_embedding],
            document_ids=document_ids or [],
        )
        return [dict(r) for r in result]

    @staticmethod
    def _entity_fulltext_search(
        tx,
        index_name: str,
        query: str,
        top_k: int,
        document_ids: list[str] | None,
    ) -> list[dict]:
        doc_filter = "MATCH (c:Chunk)-[:MENTIONS]->(e) WHERE c.documentId IN $document_ids" if document_ids else ""
        result = tx.run(
            f"""
CALL db.index.fulltext.queryNodes($index_name, $query)
YIELD node AS e, score
{doc_filter}
RETURN DISTINCT e.id AS id, e.canonicalName AS canonicalName,
       e.type AS type, e.description AS description, score
ORDER BY score DESC
LIMIT $top_k
""",
            index_name=index_name,
            query=query,
            top_k=top_k,
            document_ids=document_ids or [],
        )
        return [dict(r) for r in result]

    @staticmethod
    def _chunks_mentioning_entities(
        tx,
        entity_ids: list[str],
        document_ids: list[str] | None,
        limit: int,
    ) -> list[dict]:
        where = "WHERE e.id IN $entity_ids"
        if document_ids:
            where += " AND c.documentId IN $document_ids"
        result = tx.run(
            f"""
MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
{where}
WITH c, count(distinct e) AS hit_count
RETURN
  c.id AS chunk_id,
  c.documentId AS document_id,
  c.fileName AS file_name,
  c.text AS content,
  c.position AS position,
  c.tokenCount AS token_count,
  c.pageStart AS page_start,
  c.pageEnd AS page_end,
  c.charStart AS char_start,
  c.charEnd AS char_end,
  c.chunkHash AS chunk_hash,
  c.sectionPath AS section_path,
  toFloat(hit_count) AS score
ORDER BY hit_count DESC
LIMIT $limit
""",
            entity_ids=entity_ids,
            document_ids=document_ids or [],
            limit=limit,
        )
        return [NaiveRetrievalService._record_to_chunk(r) for r in result]

    @staticmethod
    def _entity_relationships(tx, entity_ids: list[str]) -> list[dict]:
        # ponytail: 1-hop only; upgrade to multi-hop traversal for deeper graph reasoning.
        result = tx.run(
            """
MATCH (a:Entity)-[r:RELATED]->(b:Entity)
WHERE a.id IN $entity_ids OR b.id IN $entity_ids
RETURN a.canonicalName AS source, r.type AS type,
       r.description AS description, b.canonicalName AS target,
       r.confidence AS confidence
ORDER BY r.confidence DESC
LIMIT 20
""",
            entity_ids=entity_ids,
        )
        return [dict(r) for r in result]

    @staticmethod
    def _entity_communities(tx, entity_ids: list[str]) -> list[dict]:
        result = tx.run(
            """
MATCH (e:Entity)-[:IN_COMMUNITY]->(cm:Community)
WHERE e.id IN $entity_ids
RETURN DISTINCT cm.id AS id, cm.summary AS summary,
       cm.rank AS rank
ORDER BY cm.rank DESC
LIMIT 3
""",
            entity_ids=entity_ids,
        )
        return [dict(r) for r in result]

    @staticmethod
    def _neighbour_chunks(
        tx,
        chunk_ids: list[str],
        document_ids: list[str] | None,
        window: int,
    ) -> list[dict]:
        # ponytail: fixed window=1 covers prev+next; increase window for wider context at cost of noise.
        doc_filter = "AND neighbour.documentId IN $document_ids" if document_ids else ""
        result = tx.run(
            f"""
MATCH (seed:Chunk)
WHERE seed.id IN $chunk_ids
MATCH (neighbour:Chunk)
WHERE neighbour.documentId = seed.documentId
  AND abs(neighbour.position - seed.position) <= $window
  AND neighbour.id <> seed.id
  {doc_filter}
RETURN DISTINCT
  neighbour.id AS chunk_id,
  neighbour.documentId AS document_id,
  neighbour.fileName AS file_name,
  neighbour.text AS content,
  neighbour.position AS position,
  neighbour.tokenCount AS token_count,
  neighbour.pageStart AS page_start,
  neighbour.pageEnd AS page_end,
  neighbour.charStart AS char_start,
  neighbour.charEnd AS char_end,
  neighbour.chunkHash AS chunk_hash,
  neighbour.sectionPath AS section_path,
  0.0 AS score
""",
            chunk_ids=chunk_ids,
            window=window,
            document_ids=document_ids or [],
        )
        return [NaiveRetrievalService._record_to_chunk(r) for r in result]

    # ── formatting ────────────────────────────────────────────────────────────

    @staticmethod
    def _format_relationships(relationships: list[dict]) -> str:
        if not relationships:
            return ""
        lines = [f"- {r['source']} --[{r['type']}]--> {r['target']}: {r['description']}" for r in relationships]
        return "\n".join(lines)

    @staticmethod
    def _format_communities(communities: list[dict]) -> str:
        if not communities:
            return ""
        return "\n\n".join(c["summary"] for c in communities if c.get("summary"))

    # ── keyword extract ───────────────────────────────────────────────────────

    @staticmethod
    def _extract_keywords(query: str) -> list[str]:
        # ponytail: regex tokenize; upgrade to LLM-based keyword/NER extraction for higher recall on complex queries.
        return [t.lower() for t in re.findall(r"\w+", query, flags=re.UNICODE) if len(t) > 1]

    @staticmethod
    def _rrf_merge_entities(
        result_sets: list[tuple[str, list[dict]]],
        top_k: int,
        rrf_k: int = 60,
    ) -> list[dict]:
        merged: dict[str, dict] = {}
        for _, entities in result_sets:
            for rank, entity in enumerate(entities, start=1):
                eid = entity.get("id")
                if not eid:
                    continue
                if eid not in merged:
                    merged[eid] = {**entity, "score": 0.0}
                merged[eid]["score"] += 1 / (rrf_k + rank)
        return sorted(merged.values(), key=lambda e: e["score"], reverse=True)[:top_k]
