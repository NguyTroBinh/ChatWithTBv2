from __future__ import annotations
from typing import Any
from app.core.config import (
    CHUNK_FULLTEXT_INDEX,
    CHUNK_VECTOR_INDEX,
    EMBEDDING_DIMENSION,
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
            session.execute_write(
                self._create_vector_index,
                self.embedding_dimension,
                self.vector_index_name,
            )
            session.execute_write(self._create_fulltext_index, self.fulltext_index_name)

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

    def _session(self):
        if self.database:
            return self.driver.session(database=self.database)
        return self.driver.session()

    @staticmethod
    def _create_constraints(tx) -> None:
        tx.run("CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE")
        tx.run("CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE")

    @staticmethod
    def _create_indexes(tx) -> None:
        tx.run("CREATE INDEX chunk_document_id IF NOT EXISTS FOR (c:Chunk) ON (c.documentId)")

    @classmethod
    def _create_vector_index(cls, tx, embedding_dimension: int, index_name: str) -> None:
        tx.run(cls._vector_index_query(embedding_dimension, index_name))

    @classmethod
    def _create_fulltext_index(cls, tx, index_name: str) -> None:
        tx.run(cls._fulltext_index_query(index_name))

    @staticmethod
    def _vector_index_query(embedding_dimension: int, index_name: str = CHUNK_VECTOR_INDEX) -> str:
        if embedding_dimension <= 0:
            raise ValueError("embedding_dimension must be greater than 0")
        if not index_name.replace("_", "").isalnum():
            raise ValueError(f"Invalid vector index name: {index_name}")

        return f"""
CREATE VECTOR INDEX {index_name} IF NOT EXISTS
FOR (c:Chunk) ON (c.embedding)
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
    def _replace_document_chunks(tx, payload: dict) -> None:
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
