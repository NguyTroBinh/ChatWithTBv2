from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import (
    EMBEDDING_DIMENSION,
    NEO4J_DATABASE,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
)
from app.graph.neo4j_store import Neo4jGraphStore
from app.memory.config import MEMORY_VECTOR_INDEX


_SAFE_RELATION_RE = re.compile(r"[A-Z][A-Z0-9_]{0,60}")


class Neo4jMemoryStore:
    def __init__(
        self,
        driver: Any,
        database: str = NEO4J_DATABASE,
        embedding_dimension: int = EMBEDDING_DIMENSION,
        vector_index_name: str = MEMORY_VECTOR_INDEX,
    ):
        self.driver = driver
        self.database = database
        self.embedding_dimension = embedding_dimension
        self.vector_index_name = vector_index_name

    @classmethod
    def from_config(cls) -> "Neo4jMemoryStore":
        try:
            from neo4j import GraphDatabase
        except ModuleNotFoundError as exc:
            raise RuntimeError("Missing dependency: install neo4j from requirements.txt.") from exc

        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        return cls(driver=driver)

    def close(self) -> None:
        self.driver.close()

    def ensure_schema(self) -> None:
        with self._session() as session:
            session.execute_write(self._create_constraints)
            session.execute_write(self._create_indexes)
            session.execute_write(
                Neo4jGraphStore._create_vector_index,
                self.embedding_dimension,
                self.vector_index_name,
                "Memory",
            )

    def save_memory(self, memory: dict) -> dict:
        with self._session() as session:
            return session.execute_write(self._upsert_memory, memory)

    def get_memory(self, memory_id: str) -> dict | None:
        with self._session() as session:
            return session.execute_read(self._get_memory, memory_id)

    def delete_memory(self, memory_id: str) -> bool:
        with self._session() as session:
            return session.execute_write(self._delete_memory, memory_id)

    def delete_session(self, session_id: str | None) -> dict:
        session_id = self._required_session_id(session_id)
        with self._session() as session:
            return session.execute_write(self._delete_session, session_id)

    def vector_search(
        self,
        query_embedding: list[float],
        limit: int = 5,
        session_id: str | None = None,
    ) -> list[dict]:
        session_id = self._required_session_id(session_id)
        limit = max(1, int(limit))
        embedding = [float(value) for value in query_embedding]
        with self._session() as session:
            return session.execute_read(self._scoped_vector_search, embedding, limit, session_id)

    def keyword_search(
        self,
        query: str,
        keywords: list[str],
        limit: int = 5,
        session_id: str | None = None,
    ) -> list[dict]:
        session_id = self._required_session_id(session_id)
        normalized = (query or "").strip().lower()
        limit = max(1, int(limit))
        with self._session() as session:
            return session.execute_read(
                self._keyword_search,
                normalized,
                keywords,
                limit,
                session_id,
            )

    def fetch_relations(
        self,
        memory_id: str,
        limit: int = 5,
        session_id: str | None = None,
    ) -> list[dict]:
        session_id = self._required_session_id(session_id)
        with self._session() as session:
            return session.execute_read(self._fetch_relations, memory_id, max(1, int(limit)), session_id)

    def expand_related(
        self,
        memory_id: str,
        *,
        limit: int = 5,
        session_id: str | None = None,
        relation_types: list[str] | None = None,
    ) -> list[dict]:
        session_id = self._required_session_id(session_id)
        with self._session() as session:
            return session.execute_read(
                self._expand_related,
                memory_id,
                max(1, int(limit)),
                session_id,
                relation_types or [],
            )

    def expand_via_entities(
        self,
        memory_id: str,
        *,
        limit: int = 5,
        session_id: str | None = None,
    ) -> list[dict]:
        session_id = self._required_session_id(session_id)
        with self._session() as session:
            return session.execute_read(
                self._expand_via_entities,
                memory_id,
                max(1, int(limit)),
                session_id,
            )

    def create_similarity_edges(
        self,
        memory_id: str,
        *,
        threshold: float = 0.8,
        limit: int = 5,
        session_id: str | None = None,
    ) -> list[dict]:
        session_id = self._required_session_id(session_id)
        with self._session() as session:
            return session.execute_write(
                self._create_similarity_edges,
                str(memory_id),
                float(threshold),
                max(1, int(limit)),
                session_id,
            )

    def list_recent(self, limit: int = 20, session_id: str | None = None) -> list[dict]:
        session_id = self._required_session_id(session_id)
        with self._session() as session:
            return session.execute_read(self._list_recent, max(1, int(limit)), session_id)

    def touch_memories(self, memory_ids: list[str], session_id: str | None = None) -> None:
        session_id = self._required_session_id(session_id)
        ids = list(dict.fromkeys(str(memory_id).strip() for memory_id in memory_ids if str(memory_id).strip()))
        if not ids:
            return
        with self._session() as session:
            session.execute_write(self._touch_memories, ids, session_id)

    def apply_enrichment(self, enrichment: dict) -> None:
        memory_id = str(enrichment.get("memory_id") or "").strip()
        session_id = self._required_session_id(enrichment.get("session_id"))
        if not memory_id:
            raise ValueError("memory_id is required for memory enrichment")
        # Split into independent transactions so a failure in entities/relations
        # never leaves the memory unmarked (processed=true is committed first).
        with self._session() as session:
            session.execute_write(self._apply_enrichment_memory, enrichment, memory_id, session_id)
        with self._session() as session:
            session.execute_write(self._apply_enrichment_entities, enrichment, memory_id, session_id)
        with self._session() as session:
            session.execute_write(self._apply_enrichment_relations, enrichment, memory_id, session_id)

    def _session(self):
        if self.database:
            return self.driver.session(database=self.database)
        return self.driver.session()

    @staticmethod
    def _required_session_id(session_id: str | None) -> str:
        value = (session_id or "").strip()
        if not value:
            raise ValueError("session_id is required for long-term memory search")
        return value

    @staticmethod
    def _create_constraints(tx) -> None:
        tx.run("CREATE CONSTRAINT memory_id IF NOT EXISTS FOR (m:Memory) REQUIRE m.id IS UNIQUE")
        tx.run("CREATE CONSTRAINT memory_entity_id IF NOT EXISTS FOR (e:MemoryEntity) REQUIRE e.id IS UNIQUE")

    @staticmethod
    def _create_indexes(tx) -> None:
        tx.run("CREATE INDEX memory_session_id IF NOT EXISTS FOR (m:Memory) ON (m.sessionId)")
        tx.run("CREATE INDEX memory_type IF NOT EXISTS FOR (m:Memory) ON (m.type)")
        tx.run("CREATE INDEX memory_updated_at IF NOT EXISTS FOR (m:Memory) ON (m.updatedAt)")
        tx.run("CREATE INDEX memory_entity_session_id IF NOT EXISTS FOR (e:MemoryEntity) ON (e.sessionId)")
        tx.run("CREATE INDEX memory_entity_type IF NOT EXISTS FOR (e:MemoryEntity) ON (e.type)")
        tx.run("CREATE INDEX memory_entity_name IF NOT EXISTS FOR (e:MemoryEntity) ON (e.name)")

    @staticmethod
    def _upsert_memory(tx, memory: dict) -> dict:
        result = tx.run(
            """
MERGE (m:Memory {id: $id})
ON CREATE SET m.createdAt = datetime()
SET m.content = $content,
    m.sessionId = $sessionId,
    m.type = $type,
    m.confidence = $confidence,
    m.importance = $importance,
    m.tags = $tags,
    m.tagPrefixes = $tagPrefixes,
    m.metadata = $metadata,
    m.embedding = $embedding,
    m.processed = $processed,
    m.updatedAt = datetime(),
    m.lastAccessed = coalesce(m.lastAccessed, datetime())
RETURN m, 1.0 AS score
""",
            **memory,
        )
        record = result.single()
        return Neo4jMemoryStore._record_to_memory(record) if record else {}

    @staticmethod
    def _get_memory(tx, memory_id: str) -> dict | None:
        record = tx.run(
            """
MATCH (m:Memory {id: $id})
RETURN m, null AS score
""",
            id=memory_id,
        ).single()
        return Neo4jMemoryStore._record_to_memory(record) if record else None

    @staticmethod
    def _delete_memory(tx, memory_id: str) -> bool:
        record = tx.run(
            """
MATCH (m:Memory {id: $id})
WITH m, count(m) AS found
DETACH DELETE m
RETURN found > 0 AS deleted
""",
            id=memory_id,
        ).single()
        return bool(record and record["deleted"])

    @staticmethod
    def _delete_session(tx, session_id: str) -> dict:
        record = tx.run(
            """
MATCH (m:Memory)
WHERE m.sessionId = $session_id
WITH collect(m) AS memories
FOREACH (m IN memories | DETACH DELETE m)
WITH size(memories) AS memory_count
MATCH (e:MemoryEntity)
WHERE e.sessionId = $session_id
WITH memory_count, collect(e) AS entities
FOREACH (e IN entities | DETACH DELETE e)
RETURN memory_count, size(entities) AS entity_count
""",
            session_id=session_id,
        ).single()
        return {
            "memory_count": int(record["memory_count"] or 0) if record else 0,
            "entity_count": int(record["entity_count"] or 0) if record else 0,
        }

    @staticmethod
    def _scoped_vector_search(
        tx,
        query_embedding: list[float],
        limit: int,
        session_id: str,
    ) -> list[dict]:
        # ponytail: scans one session before cosine scoring; upgrade to native pre-filtered vector search if memory grows.
        result = tx.run(
            """
MATCH (m:Memory)
WHERE m.sessionId = $session_id
  AND m.embedding IS NOT NULL
WITH m, vector.similarity.cosine(m.embedding, $query_embedding) AS score
RETURN m, score
ORDER BY score DESC
LIMIT $limit
""",
            query_embedding=query_embedding,
            limit=limit,
            session_id=session_id,
        )
        return [Neo4jMemoryStore._record_to_memory(record) for record in result]

    @staticmethod
    def _keyword_search(
        tx,
        query: str,
        keywords: list[str],
        limit: int,
        session_id: str,
    ) -> list[dict]:
        if not query and not keywords:
            result = tx.run(
                """
MATCH (m:Memory)
WHERE m.content IS NOT NULL
  AND coalesce(m.archived, false) = false
  AND m.sessionId = $session_id
RETURN m, coalesce(m.importance, 0.0) AS score
ORDER BY coalesce(m.importance, 0.0) DESC, m.updatedAt DESC
LIMIT $limit
""",
                session_id=session_id,
                limit=limit,
            )
            return [Neo4jMemoryStore._record_to_memory(record) for record in result]

        result = tx.run(
            """
MATCH (m:Memory)
WHERE m.content IS NOT NULL
  AND coalesce(m.archived, false) = false
  AND m.sessionId = $session_id
WITH m,
     toLower(m.content) AS content,
     [tag IN coalesce(m.tags, []) | toLower(tag)] AS tags
UNWIND $keywords AS kw
WITH m, content, tags, kw,
     CASE WHEN content CONTAINS kw THEN 2 ELSE 0 END +
     CASE WHEN any(tag IN tags WHERE tag CONTAINS kw) THEN 1 ELSE 0 END AS kw_score
WITH m, content, tags, SUM(kw_score) AS keyword_score
WITH m, keyword_score +
     CASE WHEN $query_text <> '' AND content CONTAINS $query_text THEN 2 ELSE 0 END +
     CASE WHEN $query_text <> '' AND any(tag IN tags WHERE tag CONTAINS $query_text) THEN 1 ELSE 0 END AS score
WHERE score > 0
RETURN m, score
ORDER BY score DESC, coalesce(m.importance, 0.0) DESC, m.updatedAt DESC
LIMIT $limit
""",
            query_text=query,
            keywords=keywords or [query],
            session_id=session_id,
            limit=limit,
        )
        return [Neo4jMemoryStore._record_to_memory(record) for record in result]

    @staticmethod
    def _touch_memories(tx, memory_ids: list[str], session_id: str) -> None:
        tx.run(
            """
UNWIND $memory_ids AS memory_id
MATCH (m:Memory {id: memory_id})
WHERE m.sessionId = $session_id
SET m.lastAccessed = datetime()
""",
            memory_ids=memory_ids,
            session_id=session_id,
        )

    @staticmethod
    def _create_similarity_edges(tx, memory_id: str, threshold: float, limit: int, session_id: str) -> list[dict]:
        result = tx.run(
            """
MATCH (m:Memory {id: $memory_id})
WHERE m.sessionId = $session_id
  AND m.embedding IS NOT NULL
MATCH (c:Memory)
WHERE c.id <> $memory_id
  AND c.sessionId = $session_id
  AND c.embedding IS NOT NULL
  AND coalesce(c.archived, false) = false
  AND coalesce(c.meta, false) = false
WITH c, vector.similarity.cosine(m.embedding, c.embedding) AS sim
WHERE sim >= $threshold
ORDER BY sim DESC
LIMIT $limit
MERGE (m)-[r1:SIMILAR_TO]->(c)
SET r1.score = sim, r1.updatedAt = datetime()
MERGE (c)-[r2:SIMILAR_TO]->(m)
SET r2.score = sim, r2.updatedAt = datetime()
RETURN c.id AS id, sim AS score
""",
            memory_id=memory_id,
            session_id=session_id,
            threshold=threshold,
            limit=limit,
        )
        return [{"id": record["id"], "score": float(record["score"])} for record in result]

    @staticmethod
    def _apply_enrichment_memory(tx, enrichment: dict, memory_id: str, session_id: str) -> None:
        tx.run(
            """
MATCH (m:Memory {id: $memory_id})
WHERE m.sessionId = $session_id
SET m.type = $memory_type,
    m.confidence = $confidence,
    m.tags = $tags,
    m.tagPrefixes = $tag_prefixes,
    m.metadata = $metadata,
    m.processed = true,
    m.enriched = true,
    m.enrichedAt = datetime(),
    m.updatedAt = datetime()
""",
            memory_id=memory_id,
            session_id=session_id,
            memory_type=enrichment.get("type") or "Context",
            confidence=float(enrichment.get("confidence") or 0.0),
            tags=enrichment.get("tags") or [],
            tag_prefixes=enrichment.get("tag_prefixes") or [],
            metadata=enrichment.get("metadata") or "{}",
        )

    @staticmethod
    def _apply_enrichment_entities(tx, enrichment: dict, memory_id: str, session_id: str) -> None:
        tx.run(
            """
UNWIND $entities AS row
MATCH (m:Memory {id: $memory_id})
WHERE m.sessionId = $session_id
MERGE (e:MemoryEntity {id: row.id})
ON CREATE SET e.createdAt = datetime()
SET e.sessionId = $session_id,
    e.name = row.name,
    e.type = row.type,
    e.description = row.description,
    e.confidence = row.confidence,
    e.updatedAt = datetime()
MERGE (m)-[r:MENTIONS]->(e)
SET r.confidence = row.confidence,
    r.updatedAt = datetime()
""",
            memory_id=memory_id,
            session_id=session_id,
            entities=enrichment.get("entities") or [],
        )

    @staticmethod
    def _apply_enrichment_relations(tx, enrichment: dict, memory_id: str, session_id: str) -> None:
        for relation in enrichment.get("entity_relationships") or []:
            relation_type = Neo4jMemoryStore._safe_relation_type(relation.get("type"))
            tx.run(
                f"""
MATCH (a:MemoryEntity {{id: $source_id}})
WHERE a.sessionId = $session_id
MATCH (b:MemoryEntity {{id: $target_id}})
WHERE b.sessionId = $session_id
MERGE (a)-[r:{relation_type}]->(b)
SET r.description = $description,
    r.confidence = $confidence,
    r.sourceMemoryIds = CASE
        WHEN $memory_id IN coalesce(r.sourceMemoryIds, []) THEN coalesce(r.sourceMemoryIds, [])
        ELSE coalesce(r.sourceMemoryIds, []) + [$memory_id]
    END,
    r.updatedAt = datetime()
""",
                source_id=relation.get("source_id"),
                target_id=relation.get("target_id"),
                session_id=session_id,
                memory_id=memory_id,
                description=relation.get("description") or "",
                confidence=float(relation.get("confidence") or 0.0),
            )
        for relation in enrichment.get("memory_relationships") or []:
            relation_type = Neo4jMemoryStore._safe_relation_type(relation.get("type"))
            tx.run(
                f"""
MATCH (a:Memory {{id: $source_id}})
WHERE a.sessionId = $session_id
MATCH (b:Memory {{id: $target_id}})
WHERE b.sessionId = $session_id
MERGE (a)-[r:{relation_type}]->(b)
SET r.strength = $strength,
    r.description = $description,
    r.updatedAt = datetime()
""",
                source_id=memory_id,
                target_id=relation.get("target_id"),
                session_id=session_id,
                strength=float(relation.get("strength") or 0.0),
                description=relation.get("description") or "",
            )

    @staticmethod
    def _safe_relation_type(value: Any) -> str:
        relation_type = str(value or "").strip().upper()
        if not _SAFE_RELATION_RE.fullmatch(relation_type):
            raise ValueError(f"Invalid memory relation type: {value}")
        return relation_type

    @staticmethod
    def _fetch_relations(tx, memory_id: str, limit: int, session_id: str) -> list[dict]:
        result = tx.run(
            """
MATCH (m:Memory {id: $id})-[r]->(related:Memory)
WHERE m.sessionId = $session_id
  AND related.sessionId = $session_id
RETURN type(r) AS relation_type,
       coalesce(r.strength, r.score, r.confidence, r.similarity, toFloat(r.count), 0.0) AS strength,
       r.kind AS relation_kind,
       related
ORDER BY coalesce(r.updatedAt, r.updated_at, related.updatedAt, related.createdAt) DESC
LIMIT $limit
""",
            id=memory_id,
            limit=limit,
            session_id=session_id,
        )
        return [
            Neo4jMemoryStore._record_to_relation(record)
            for record in result
        ]

    @staticmethod
    def _expand_related(
        tx,
        memory_id: str,
        limit: int,
        session_id: str,
        relation_types: list[str],
    ) -> list[dict]:
        result = tx.run(
            """
MATCH (m:Memory {id: $id})-[r]-(related:Memory)
WHERE m.id <> related.id
  AND m.sessionId = $session_id
  AND related.sessionId = $session_id
  AND (size($relation_types) = 0 OR type(r) IN $relation_types)
RETURN type(r) AS relation_type,
       coalesce(r.strength, r.score, r.confidence, r.similarity, toFloat(r.count), 0.0) AS strength,
       r.kind AS relation_kind,
       related AS m
ORDER BY coalesce(r.updatedAt, r.updated_at, m.updatedAt, m.createdAt) DESC
LIMIT $limit
""",
            id=memory_id,
            limit=limit,
            session_id=session_id,
            relation_types=relation_types,
        )
        rows = []
        for record in result:
            memory = Neo4jMemoryStore._record_to_memory(record)
            rows.append(
                {
                    "memory": memory,
                    "relationType": record["relation_type"],
                    "relationStrength": float(record["strength"] or 0.0),
                    "relationKind": record["relation_kind"],
                }
            )
        return rows

    @staticmethod
    def _expand_via_entities(tx, memory_id: str, limit: int, session_id: str) -> list[dict]:
        result = tx.run(
            """
MATCH (m:Memory {id: $id})
WHERE m.sessionId = $session_id
MATCH (m)-[:MENTIONS]->(e:MemoryEntity)
WHERE e.sessionId = $session_id
MATCH (candidate:Memory)-[:MENTIONS]->(e)
WHERE candidate.id <> m.id
  AND candidate.sessionId = $session_id
  AND coalesce(candidate.archived, false) = false
WITH candidate, count(DISTINCT e) AS shared_count
RETURN candidate AS m, toFloat(shared_count) AS strength, "SHARES_ENTITY" AS relation_type
ORDER BY shared_count DESC
LIMIT $limit
""",
            id=memory_id,
            limit=limit,
            session_id=session_id,
        )
        rows = []
        for record in result:
            memory = Neo4jMemoryStore._record_to_memory(record)
            rows.append(
                {
                    "memory": memory,
                    "relationType": record["relation_type"],
                    "relationStrength": float(record["strength"] or 0.0),
                }
            )
        return rows

    @staticmethod
    def _list_recent(tx, limit: int, session_id: str) -> list[dict]:
        result = tx.run(
            """
MATCH (m:Memory)
WHERE m.sessionId = $session_id
RETURN m, null AS score
ORDER BY m.updatedAt DESC
LIMIT $limit
""",
            limit=limit,
            session_id=session_id,
        )
        return [Neo4jMemoryStore._record_to_memory(record) for record in result]

    @staticmethod
    def _record_to_memory(record) -> dict:
        node = record["m"]
        data = dict(node)
        metadata = data.get("metadata")
        if isinstance(metadata, str) and metadata:
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}

        return {
            "id": data.get("id"),
            "content": data.get("content", ""),
            "sessionId": data.get("sessionId"),
            "type": data.get("type", "Context"),
            "confidence": data.get("confidence", 0.0),
            "importance": data.get("importance", 0.0),
            "tags": data.get("tags") or [],
            "tagPrefixes": data.get("tagPrefixes") or [],
            "metadata": metadata or {},
            "processed": bool(data.get("processed", False)),
            "score": record.get("score"),
            "createdAt": str(data.get("createdAt")) if data.get("createdAt") else None,
            "updatedAt": str(data.get("updatedAt")) if data.get("updatedAt") else None,
            "lastAccessed": str(data.get("lastAccessed")) if data.get("lastAccessed") else None,
        }

    @staticmethod
    def _record_to_relation(record) -> dict:
        memory = Neo4jMemoryStore._record_to_memory({"m": record["related"], "score": None})
        relation = {
            "type": record["relation_type"],
            "strength": float(record["strength"] or 0.0),
            "memory": {
                "id": memory["id"],
                "content": memory["content"],
                "type": memory["type"],
                "tags": memory["tags"],
                "importance": memory["importance"],
            },
        }
        if record["relation_kind"]:
            relation["kind"] = record["relation_kind"]
        return relation
