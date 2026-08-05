from __future__ import annotations
from typing import Any
from app.core.config import (
    CHUNK_FULLTEXT_INDEX,
    CHUNK_VECTOR_INDEX,
    COMMUNITY_FULLTEXT_INDEX,
    COMMUNITY_VECTOR_INDEX,
    EMBEDDING_DIMENSION,
    ENTITY_FULLTEXT_INDEX,
    ENTITY_VECTOR_INDEX,
    NEO4J_DATABASE,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
)

class Neo4jGraphStore:
    def __init__(
        self,
        driver: Any,
        database: str = NEO4J_DATABASE,
        embedding_dimension: int = EMBEDDING_DIMENSION,
        vector_index_name: str = CHUNK_VECTOR_INDEX,
        fulltext_index_name: str = CHUNK_FULLTEXT_INDEX,
    ):
        self.driver = driver
        self.database = database
        self.embedding_dimension = embedding_dimension
        self.vector_index_name = vector_index_name
        self.fulltext_index_name = fulltext_index_name

    @classmethod
    def from_config(cls) -> "Neo4jGraphStore":
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
            session.execute_write(self._create_vector_index, self.embedding_dimension, self.vector_index_name, "Chunk")
            session.execute_write(self._create_fulltext_index, self.fulltext_index_name)
            session.execute_write(self._create_vector_index, self.embedding_dimension, ENTITY_VECTOR_INDEX, "Entity")
            session.execute_write(self._create_entity_fulltext_index)
            session.execute_write(self._create_vector_index, self.embedding_dimension, COMMUNITY_VECTOR_INDEX, "Community")
            session.execute_write(self._create_community_fulltext_index)

    def save_document_chunks(
        self,
        file_name: str,
        chunks: list[dict],
        embeddings: list[list[float]] | None = None,
        document_type: str = "pdf",
    ) -> dict:
        payload = self._build_document_payload(file_name, chunks, embeddings, document_type)
        with self._session() as session:
            session.execute_write(self._replace_document_chunks, payload)
        return {"document_id": payload["document_id"], "chunk_count": len(payload["chunks"])}

    def list_documents(self, search: str = "", limit: int = 50) -> list[dict]:
        search = (search or "").strip().lower()
        limit = max(1, min(int(limit), 100))
        with self._session() as session:
            return session.execute_read(self._list_documents, search, limit)

    def get_documents_by_ids(self, document_ids: list[str]) -> list[dict]:
        ids = list(dict.fromkeys(str(document_id).strip() for document_id in document_ids if str(document_id).strip()))
        if not ids:
            return []
        with self._session() as session:
            return session.execute_read(self._get_documents_by_ids, ids)

    def _session(self):
        if self.database:
            return self.driver.session(database=self.database)
        return self.driver.session()

    @staticmethod
    def _create_constraints(tx) -> None:
        tx.run("CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE")
        tx.run("CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE")
        tx.run("CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE")
        tx.run("CREATE CONSTRAINT community_id IF NOT EXISTS FOR (cm:Community) REQUIRE cm.id IS UNIQUE")

    @staticmethod
    def _create_indexes(tx) -> None:
        tx.run("CREATE INDEX chunk_document_id IF NOT EXISTS FOR (c:Chunk) ON (c.documentId)")
        tx.run("CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)")

    @staticmethod
    def _list_documents(tx, search: str, limit: int) -> list[dict]:
        result = tx.run(
            """
MATCH (d:Document)
OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
WITH d, count(c) AS chunk_count
WHERE chunk_count > 0
  AND ($search = "" OR toLower(coalesce(d.fileName, "")) CONTAINS $search)
RETURN
  d.id AS document_id,
  d.fileName AS file_name,
  toString(d.updatedAt) AS updated_at
ORDER BY d.updatedAt DESC, d.fileName ASC
LIMIT $limit
""",
            search=search,
            limit=limit,
        )
        return [dict(record) for record in result]

    @staticmethod
    def _get_documents_by_ids(tx, document_ids: list[str]) -> list[dict]:
        result = tx.run(
            """
UNWIND $document_ids AS document_id
MATCH (d:Document {id: document_id})
RETURN
  d.id AS document_id,
  d.fileName AS file_name,
  toString(d.updatedAt) AS updated_at
""",
            document_ids=document_ids,
        )
        rows = {record["document_id"]: dict(record) for record in result}
        return [rows[document_id] for document_id in document_ids if document_id in rows]

    @classmethod
    def _create_vector_index(
        cls,
        tx,
        embedding_dimension: int,
        index_name: str,
        label: str = "Chunk",
        property_name: str = "embedding",
    ) -> None:
        tx.run(cls._vector_index_query(embedding_dimension, index_name, label, property_name))

    @classmethod
    def _create_fulltext_index(cls, tx, index_name: str) -> None:
        tx.run(cls._fulltext_index_query(index_name))

    @staticmethod
    def _vector_index_query(
        embedding_dimension: int,
        index_name: str = CHUNK_VECTOR_INDEX,
        label: str = "Chunk",
        property_name: str = "embedding",
    ) -> str:
        if embedding_dimension <= 0:
            raise ValueError("embedding_dimension must be greater than 0")
        if not index_name.replace("_", "").isalnum():
            raise ValueError(f"Invalid vector index name: {index_name}")
        if not label.replace("_", "").isalnum():
            raise ValueError(f"Invalid vector index label: {label}")
        if not property_name.replace("_", "").isalnum():
            raise ValueError(f"Invalid vector index property: {property_name}")

        return f"""
CREATE VECTOR INDEX {index_name} IF NOT EXISTS
FOR (n:{label}) ON (n.{property_name})
OPTIONS {{indexConfig: {{
  `vector.dimensions`: {int(embedding_dimension)},
  `vector.similarity_function`: 'cosine'
}}}}
""".strip()

    @staticmethod
    def _fulltext_index_query(index_name: str = CHUNK_FULLTEXT_INDEX) -> str:
        if not index_name.replace("_", "").isalnum():
            raise ValueError(f"Invalid fulltext index name: {index_name}")

        return f"""
CREATE FULLTEXT INDEX {index_name} IF NOT EXISTS
FOR (c:Chunk) ON EACH [c.text]
""".strip()

    @staticmethod
    def _create_entity_fulltext_index(tx) -> None:
        tx.run(f"""
CREATE FULLTEXT INDEX {ENTITY_FULLTEXT_INDEX} IF NOT EXISTS
FOR (e:Entity) ON EACH [e.canonicalName, e.aliases, e.description]
""".strip())

    @staticmethod
    def _create_community_fulltext_index(tx) -> None:
        tx.run(f"""
CREATE FULLTEXT INDEX {COMMUNITY_FULLTEXT_INDEX} IF NOT EXISTS
FOR (cm:Community) ON EACH [cm.summary]
""".strip())

    def save_entities(self, entities: list[dict], chunk_id: str) -> None:
        """Upsert entities and MENTIONS relationships from a chunk."""
        with self._session() as session:
            session.execute_write(self._upsert_entities, entities, chunk_id)

    def save_relationships(self, relationships: list[dict]) -> None:
        """Upsert RELATED relationships between entities."""
        with self._session() as session:
            session.execute_write(self._upsert_relationships, relationships)

    def save_community(self, community: dict, entity_ids: list[str]) -> None:
        """Upsert a Community node and IN_COMMUNITY relationships."""
        with self._session() as session:
            session.execute_write(self._upsert_community, community, entity_ids)

    @staticmethod
    def _upsert_entities(tx, entities: list[dict], chunk_id: str) -> None:
        tx.run(
            """
UNWIND $entities AS row
MERGE (e:Entity {id: row.id})
ON CREATE SET e.createdAt = datetime()
SET e.canonicalName = row.canonicalName,
    e.aliases       = row.aliases,
    e.type          = row.type,
    e.description   = row.description,
    e.confidence    = row.confidence,
    e.embedding     = row.embedding,
    e.updatedAt     = datetime()
WITH e, row
MATCH (c:Chunk {id: $chunk_id})
MERGE (c)-[:MENTIONS {confidence: row.confidence}]->(e)
""",
            entities=entities,
            chunk_id=chunk_id,
        )

    @staticmethod
    def _upsert_relationships(tx, relationships: list[dict]) -> None:
        tx.run(
            """
UNWIND $rels AS row
MATCH (a:Entity {id: row.source_id})
MATCH (b:Entity {id: row.target_id})
MERGE (a)-[r:RELATED {type: row.type}]->(b)
ON CREATE SET r.createdAt = datetime()
SET r.description   = row.description,
    r.confidence    = row.confidence,
    r.weight        = row.weight,
    r.sourceChunkIds = reduce(
        ids = coalesce(r.sourceChunkIds, []),
        chunk_id IN row.sourceChunkIds |
        CASE WHEN chunk_id IN ids THEN ids ELSE ids + chunk_id END
    ),
    r.updatedAt     = datetime()
""",
            rels=relationships,
        )

    @staticmethod
    def _upsert_community(tx, community: dict, entity_ids: list[str]) -> None:
        tx.run(
            """
MERGE (cm:Community {id: $id})
ON CREATE SET cm.createdAt = datetime()
SET cm.level       = $level,
    cm.algorithm   = $algorithm,
    cm.summary     = $summary,
    cm.fullContent = $fullContent,
    cm.rank        = $rank,
    cm.embedding   = $embedding,
    cm.documentIds = reduce(
        ids = coalesce(cm.documentIds, []),
        document_id IN $documentIds |
        CASE WHEN document_id IN ids THEN ids ELSE ids + document_id END
    ),
    cm.updatedAt   = datetime()
WITH cm
UNWIND $entity_ids AS eid
MATCH (e:Entity {id: eid})
MERGE (e)-[:IN_COMMUNITY]->(cm)
""",
            id=community["id"],
            level=community.get("level", 0),
            algorithm=community.get("algorithm", ""),
            summary=community.get("summary", ""),
            fullContent=community.get("fullContent", ""),
            rank=community.get("rank", 0),
            embedding=community.get("embedding"),
            documentIds=community.get("documentIds", []),
            entity_ids=entity_ids,
        )

    @staticmethod
    def _replace_document_chunks(tx, payload: dict) -> None:
        old_entity_ids = Neo4jGraphStore._delete_document_graph(tx, payload["document_id"])
        tx.run(
            """
MERGE (d:Document {id: $document_id})
ON CREATE SET d.createdAt = datetime()
SET d.fileName = $file_name,
    d.type = $document_type,
    d.updatedAt = datetime()
""",
            document_id=payload["document_id"],
            file_name=payload["file_name"],
            document_type=payload["document_type"],
        )
        tx.run(
            """
MATCH (d:Document {id: $document_id})-[:HAS_CHUNK]->(old:Chunk)
DETACH DELETE old
""",
            document_id=payload["document_id"],
        )
        Neo4jGraphStore._delete_orphan_entities(tx, old_entity_ids)
        tx.run(
            """
UNWIND $chunks AS row
MATCH (d:Document {id: $document_id})
CREATE (c:Chunk)
SET c = row.properties,
    c.createdAt = datetime(),
    c.updatedAt = datetime()
CREATE (d)-[:HAS_CHUNK]->(c)
CREATE (c)-[:PART_OF]->(d)
""",
            document_id=payload["document_id"],
            chunks=payload["chunks"],
        )
        tx.run(
            """
MATCH (d:Document {id: $document_id})
MATCH (first:Chunk {id: $first_chunk_id})
MERGE (d)-[:FIRST_CHUNK]->(first)
""",
            document_id=payload["document_id"],
            first_chunk_id=payload["chunks"][0]["id"],
        )
        tx.run(
            """
UNWIND $links AS link
MATCH (from:Chunk {id: link.from})
MATCH (to:Chunk {id: link.to})
MERGE (from)-[:NEXT_CHUNK]->(to)
""",
            links=payload["links"],
        )

    @staticmethod
    def _delete_document_graph(tx, document_id: str) -> list[str]:
        old_graph = tx.run(
            """
MATCH (d:Document {id: $document_id})-[:HAS_CHUNK]->(old:Chunk)
OPTIONAL MATCH (old)-[:MENTIONS]->(e:Entity)
RETURN collect(DISTINCT old.id) AS chunk_ids,
       collect(DISTINCT e.id) AS entity_ids
""",
            document_id=document_id,
        ).single()
        if not old_graph:
            return []

        chunk_ids = [chunk_id for chunk_id in old_graph["chunk_ids"] if chunk_id]
        entity_ids = [entity_id for entity_id in old_graph["entity_ids"] if entity_id]
        if not chunk_ids and not entity_ids:
            return []

        tx.run(
            """
MATCH ()-[r:RELATED]->()
WHERE any(chunk_id IN coalesce(r.sourceChunkIds, []) WHERE chunk_id IN $chunk_ids)
WITH r, [chunk_id IN coalesce(r.sourceChunkIds, []) WHERE NOT chunk_id IN $chunk_ids] AS remaining
FOREACH (_ IN CASE WHEN size(remaining) = 0 THEN [1] ELSE [] END | DELETE r)
FOREACH (_ IN CASE WHEN size(remaining) > 0 THEN [1] ELSE [] END | SET r.sourceChunkIds = remaining)
""",
            chunk_ids=chunk_ids,
        )
        tx.run(
            """
MATCH (cm:Community)
WHERE $document_id IN coalesce(cm.documentIds, [])
WITH cm, [doc_id IN coalesce(cm.documentIds, []) WHERE doc_id <> $document_id] AS remaining
FOREACH (_ IN CASE WHEN size(remaining) = 0 THEN [1] ELSE [] END | DETACH DELETE cm)
FOREACH (_ IN CASE WHEN size(remaining) > 0 THEN [1] ELSE [] END | SET cm.documentIds = remaining)
""",
            document_id=document_id,
        )
        tx.run(
            """
MATCH (e:Entity)-[:IN_COMMUNITY]->(cm:Community)
WHERE e.id IN $entity_ids AND cm.documentIds IS NULL
WITH DISTINCT cm
DETACH DELETE cm
""",
            entity_ids=entity_ids,
        )
        return entity_ids

    @staticmethod
    def _delete_orphan_entities(tx, entity_ids: list[str]) -> None:
        if not entity_ids:
            return
        tx.run(
            """
UNWIND $entity_ids AS entity_id
MATCH (e:Entity {id: entity_id})
WHERE NOT EXISTS {
  MATCH (:Chunk)-[:MENTIONS]->(e)
}
DETACH DELETE e
""",
            entity_ids=entity_ids,
        )

    @classmethod
    def _build_document_payload(
        cls,
        file_name: str,
        chunks: list[dict],
        embeddings: list[list[float]] | None,
        document_type: str,
    ) -> dict:
        if not file_name:
            raise ValueError("file_name is required")
        if not chunks:
            raise ValueError("chunks cannot be empty")
        if embeddings is not None and len(embeddings) != len(chunks):
            raise ValueError("embeddings length must match chunks length")

        document_id = cls._document_id_from_chunks(chunks)
        normalized_chunks = []
        for index, chunk in enumerate(chunks):
            metadata = dict(chunk.get("metadata") or {})
            chunk_id = chunk.get("id") or metadata.get("chunk_id")
            content = chunk.get("content")
            if not chunk_id:
                raise ValueError(f"chunk at index {index} is missing chunk_id")
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"chunk at index {index} is missing content")
            if metadata.get("document_id") != document_id:
                raise ValueError(f"chunk at index {index} belongs to another document")

            properties = {
                "id": chunk_id,
                "documentId": document_id,
                "fileName": file_name,
                "text": content,
                "position": metadata.get("position", index),
                "tokenCount": metadata.get("token_count"),
                "pageStart": metadata.get("page_start"),
                "pageEnd": metadata.get("page_end"),
                "charStart": metadata.get("char_start"),
                "charEnd": metadata.get("char_end"),
                "chunkHash": metadata.get("chunk_hash"),
                "sectionPath": metadata.get("section_path"),
            }
            if embeddings is not None:
                properties["embedding"] = [float(value) for value in embeddings[index]]

            normalized_chunks.append({"id": chunk_id, "properties": properties})

        links = [
            {"from": normalized_chunks[index]["id"], "to": normalized_chunks[index + 1]["id"]}
            for index in range(len(normalized_chunks) - 1)
        ]
        return {
            "document_id": document_id,
            "file_name": file_name,
            "document_type": document_type,
            "chunks": normalized_chunks,
            "links": links,
        }

    @staticmethod
    def _document_id_from_chunks(chunks: list[dict]) -> str:
        document_id = (chunks[0].get("metadata") or {}).get("document_id")
        if not document_id:
            raise ValueError("first chunk is missing document_id")
        return document_id
