from __future__ import annotations

from app.extraction.community_builder import CommunityBuilder
from app.extraction.entity_extractor import EntityExtractor
from app.graph.neo4j_store import Neo4jGraphStore
from app.providers.embedding import EmbeddingService


class GraphBuilder:
    def __init__(
        self,
        graph_store: Neo4jGraphStore,
        entity_extractor: EntityExtractor,
        community_builder: CommunityBuilder,
        embedding_service: EmbeddingService,
    ):
        self.graph_store = graph_store
        self.entity_extractor = entity_extractor
        self.community_builder = community_builder
        self.embedding_service = embedding_service

    @classmethod
    def from_config(cls) -> "GraphBuilder":
        from app.providers.litellm_client import LiteLLMClient
        graph_store = Neo4jGraphStore.from_config()
        embedding_service = EmbeddingService()
        llm_client = LiteLLMClient()
        return cls(
            graph_store=graph_store,
            entity_extractor=EntityExtractor(llm_client=llm_client),
            community_builder=CommunityBuilder(
                graph_store=graph_store,
                embedding_service=embedding_service,
                llm_client=llm_client,
            ),
            embedding_service=embedding_service,
        )

    def build_from_chunks(
        self,
        chunks: list[dict],
        document_id: str,
    ) -> dict:
        """
        Nhận chunks đã ingest của một document:
        1. Extract entities + relationships từ mỗi chunk
        2. Embed entity descriptions
        3. Lưu entities + relationships vào Neo4j
        4. Build communities từ entity graph của document
        Trả về stats.
        """
        total_entities: set[str] = set()
        total_relationships = 0

        for chunk in chunks:
            chunk_id = chunk.get("id") or (chunk.get("metadata") or {}).get("chunk_id")
            text = chunk.get("content", "")
            if not chunk_id or not text.strip():
                continue

            extracted = self.entity_extractor.extract(chunk_id, text)
            entities = extracted.get("entities", [])
            relationships = extracted.get("relationships", [])

            if not entities:
                continue

            # Embed description của từng entity
            descriptions = [e["description"] or e["canonicalName"] for e in entities]
            embeddings = self.embedding_service.embed_documents(descriptions)
            for entity, emb in zip(entities, embeddings):
                entity["embedding"] = emb

            self.graph_store.save_entities(entities, chunk_id)

            if relationships:
                self.graph_store.save_relationships(relationships)
                total_relationships += len(relationships)

            total_entities.update(e["id"] for e in entities)

        community_count = self.community_builder.build(document_ids=[document_id])

        return {
            "document_id": document_id,
            "entity_count": len(total_entities),
            "relationship_count": total_relationships,
            "community_count": community_count,
        }

    def close(self) -> None:
        self.graph_store.close()
