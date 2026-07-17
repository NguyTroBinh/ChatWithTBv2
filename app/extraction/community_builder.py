from __future__ import annotations
import hashlib
from pathlib import Path

from app.graph.neo4j_store import Neo4jGraphStore
from app.providers.embedding import EmbeddingService
from app.providers.litellm_client import LiteLLMClient

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "community_summary.vi.md"


class CommunityBuilder:
    def __init__(
        self,
        graph_store: Neo4jGraphStore,
        embedding_service: EmbeddingService,
        llm_client: LiteLLMClient,
    ):
        self.graph_store = graph_store
        self.embedding_service = embedding_service
        self.llm_client = llm_client
        self.system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    @classmethod
    def from_config(cls) -> "CommunityBuilder":
        return cls(
            graph_store=Neo4jGraphStore.from_config(),
            embedding_service=EmbeddingService(),
            llm_client=LiteLLMClient(),
        )

    def build(self, document_ids: list[str] | None = None) -> int:
        """
        Detect communities bằng WCC, generate summary, lưu vào Neo4j.
        Trả về số community đã tạo.
        """
        components = self._detect_components(document_ids)
        if not components:
            return 0

        count = 0
        for component_id, entity_ids in components.items():
            entities, relationships = self._load_component_data(entity_ids)
            if not entities:
                continue

            summary = self._generate_summary(entities, relationships)
            full_content = self._format_full_content(entities, relationships)
            embedding = self.embedding_service.embed_query(summary)

            community = {
                "id": component_id,
                "documentIds": document_ids or [],
                "level": 0,
                "algorithm": "wcc",
                "summary": summary,
                "fullContent": full_content,
                "rank": len(entity_ids),
                "embedding": embedding,
            }
            self.graph_store.save_community(community, entity_ids)
            count += 1

        return count

    # ── WCC detection ─────────────────────────────────────────────────────────

    def _detect_components(self, document_ids: list[str] | None) -> dict[str, list[str]]:
        """
        Trả về {component_id: [entity_id, ...]} bằng cách traverse RELATED relationships.
        """
        with self.graph_store._session() as session:
            edges = session.execute_read(self._load_entity_edges, document_ids)
            all_entity_ids = session.execute_read(self._load_entity_ids, document_ids)

        # Build adjacency set
        adjacency: dict[str, set[str]] = {eid: set() for eid in all_entity_ids}
        for src, tgt in edges:
            if src in adjacency and tgt in adjacency:
                adjacency[src].add(tgt)
                adjacency[tgt].add(src)

        # BFS để tìm connected components
        visited: set[str] = set()
        components: dict[str, list[str]] = {}

        for start in all_entity_ids:
            if start in visited:
                continue
            component: list[str] = []
            queue = [start]
            while queue:
                node = queue.pop()
                if node in visited:
                    continue
                visited.add(node)
                component.append(node)
                queue.extend(adjacency[node] - visited)

            if len(component) >= 2:  # bỏ qua entity đơn lẻ không có quan hệ
                component_id = self._component_id(sorted(component))
                components[component_id] = component

        return components

    @staticmethod
    def _load_entity_edges(tx, document_ids: list[str] | None) -> list[tuple[str, str]]:
        doc_filter = (
            "MATCH (c:Chunk)-[:MENTIONS]->(a) WHERE c.documentId IN $document_ids "
            "MATCH (c2:Chunk)-[:MENTIONS]->(b) WHERE c2.documentId IN $document_ids"
            if document_ids else ""
        )
        # ponytail: khi có document_ids, chỉ lấy entity thuộc document đó
        if document_ids:
            result = tx.run(
                """
MATCH (a:Entity)-[:RELATED]->(b:Entity)
WHERE EXISTS {
    MATCH (c:Chunk)-[:MENTIONS]->(a) WHERE c.documentId IN $document_ids
}
AND EXISTS {
    MATCH (c2:Chunk)-[:MENTIONS]->(b) WHERE c2.documentId IN $document_ids
}
RETURN a.id AS src, b.id AS tgt
""",
                document_ids=document_ids,
            )
        else:
            result = tx.run("MATCH (a:Entity)-[:RELATED]->(b:Entity) RETURN a.id AS src, b.id AS tgt")
        return [(r["src"], r["tgt"]) for r in result]

    @staticmethod
    def _load_entity_ids(tx, document_ids: list[str] | None) -> list[str]:
        if document_ids:
            result = tx.run(
                """
MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
WHERE c.documentId IN $document_ids
RETURN DISTINCT e.id AS id
""",
                document_ids=document_ids,
            )
        else:
            result = tx.run("MATCH (e:Entity) RETURN e.id AS id")
        return [r["id"] for r in result]

    # ── data loading ──────────────────────────────────────────────────────────

    def _load_component_data(
        self, entity_ids: list[str]
    ) -> tuple[list[dict], list[dict]]:
        with self.graph_store._session() as session:
            entities = session.execute_read(self._fetch_entities, entity_ids)
            relationships = session.execute_read(self._fetch_relationships, entity_ids)
        return entities, relationships

    @staticmethod
    def _fetch_entities(tx, entity_ids: list[str]) -> list[dict]:
        result = tx.run(
            """
MATCH (e:Entity) WHERE e.id IN $entity_ids
RETURN e.id AS id, e.canonicalName AS canonicalName,
       e.type AS type, e.description AS description
""",
            entity_ids=entity_ids,
        )
        return [dict(r) for r in result]

    @staticmethod
    def _fetch_relationships(tx, entity_ids: list[str]) -> list[dict]:
        result = tx.run(
            """
MATCH (a:Entity)-[r:RELATED]->(b:Entity)
WHERE a.id IN $entity_ids AND b.id IN $entity_ids
RETURN a.canonicalName AS source, r.type AS type,
       r.description AS description, b.canonicalName AS target
""",
            entity_ids=entity_ids,
        )
        return [dict(r) for r in result]

    # ── summary generation ────────────────────────────────────────────────────

    def _generate_summary(self, entities: list[dict], relationships: list[dict]) -> str:
        user_content = self._format_full_content(entities, relationships)
        return self.llm_client.generate([
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ])

    @staticmethod
    def _format_full_content(entities: list[dict], relationships: list[dict]) -> str:
        entity_lines = "\n".join(
            f"- {e['canonicalName']} ({e['type']}): {e['description']}"
            for e in entities
        )
        rel_lines = "\n".join(
            f"- {r['source']} --[{r['type']}]--> {r['target']}: {r['description']}"
            for r in relationships
        )
        parts = [f"Thực thể:\n{entity_lines}"]
        if rel_lines:
            parts.append(f"Quan hệ:\n{rel_lines}")
        return "\n\n".join(parts)

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _component_id(sorted_entity_ids: list[str]) -> str:
        return hashlib.sha256("|".join(sorted_entity_ids).encode()).hexdigest()[:16]
