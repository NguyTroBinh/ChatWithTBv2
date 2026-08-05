from __future__ import annotations

import json
import re
import uuid
from typing import Any

from app.memory.config import MEMORY_CONSOLIDATION_ENABLED, MEMORY_DEFAULT_IMPORTANCE, MEMORY_RECALL_LIMIT
from app.memory.longterm.enrichment import MemoryEnrichmentService
from app.memory.longterm.recall import LongTermRecallService
from app.memory.longterm.neo4j_memory_store import Neo4jMemoryStore
from app.memory.longterm.scheduler import ConsolidationScheduler
from app.providers.embedding import EmbeddingService, get_embedding_service


class MemoryCore:
    def __init__(
        self,
        store: Neo4jMemoryStore,
        embedding_service: EmbeddingService,
        enrichment_service: MemoryEnrichmentService | None = None,
        consolidation_scheduler: ConsolidationScheduler | None = None,
        default_recall_limit: int = MEMORY_RECALL_LIMIT,
        default_importance: float = MEMORY_DEFAULT_IMPORTANCE,
    ):
        self.store = store
        self.embedding_service = embedding_service
        self.enrichment_service = enrichment_service
        self.consolidation_scheduler = consolidation_scheduler
        self.default_recall_limit = default_recall_limit
        self.default_importance = default_importance
        self.recall_service = LongTermRecallService(
            store=store,
            embedding_service=embedding_service,
            default_limit=default_recall_limit,
        )
        self._initialized = False

    @classmethod
    def from_config(cls) -> "MemoryCore":
        store = Neo4jMemoryStore.from_config()
        embedding_service = get_embedding_service()
        enrichment_service = MemoryEnrichmentService.from_config(store)
        return cls(
            store=store,
            embedding_service=embedding_service,
            enrichment_service=enrichment_service,
            consolidation_scheduler=(
                ConsolidationScheduler(
                    store,
                    enrichment_service=enrichment_service,
                    embedding_service=embedding_service,
                )
                if MEMORY_CONSOLIDATION_ENABLED
                else None
            ),
        )

    def initialize(self) -> None:
        if self._initialized:
            return
        self.store.ensure_schema()
        if self.enrichment_service:
            self.enrichment_service.start()
        if self.consolidation_scheduler:
            self.consolidation_scheduler.start()
        self._initialized = True

    def add_memory(
        self,
        content: str,
        *,
        session_id: str | None = None,
        tags: list[str] | str | None = None,
        memory_type: str = "Context",
        confidence: float = 0.9,
        importance: float | None = None,
        metadata: dict[str, Any] | None = None,
        processed: bool = False,
    ) -> dict:
        self.initialize()
        content = self._clean_content(content)
        memory = {
            "id": str(uuid.uuid4()),
            "content": content,
            "sessionId": self._clean_optional_text(session_id),
            "type": self._clean_optional_text(memory_type) or "Context",
            "confidence": self._bounded_float(confidence, 0.0, 1.0),
            "importance": self._bounded_float(
                self.default_importance if importance is None else importance,
                0.0,
                1.0,
            ),
            "tags": self._normalize_tags(tags),
            "metadata": json.dumps(metadata or {}, ensure_ascii=False, default=str),
            "embedding": [float(value) for value in self.embedding_service.embed_query(content)],
            "processed": bool(processed),
        }
        memory["tagPrefixes"] = self._tag_prefixes(memory["tags"])
        saved = self.store.save_memory(memory)
        if self.enrichment_service and not processed:
            self.enrichment_service.enqueue(saved.get("id") or memory["id"])
        return saved

    def add_exchange(
        self,
        query: str,
        answer: str,
        *,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        return self.add_memory(
            f"User: {self._clean_content(query)}\nAssistant: {self._clean_content(answer)}",
            session_id=session_id,
            tags=["chat", "qa"],
            memory_type="Context",
            metadata=metadata,
        )

    def recall(
        self,
        query: str,
        limit: int | None = None,
        session_id: str | None = None,
        expand_relations: bool = True,
    ) -> list[dict]:
        self.initialize()
        query = self._clean_content(query)
        return self.recall_service.recall(
            query,
            limit=limit or self.default_recall_limit,
            session_id=self._clean_optional_text(session_id),
            expand_relations=expand_relations,
        )

    def get_memory(self, memory_id: str) -> dict | None:
        return self.store.get_memory(memory_id)

    def delete_memory(self, memory_id: str) -> bool:
        return self.store.delete_memory(memory_id)

    def delete_session(self, session_id: str) -> dict:
        return self.store.delete_session(self._clean_optional_text(session_id))

    def close(self) -> None:
        if self.consolidation_scheduler:
            self.consolidation_scheduler.stop()
        if self.enrichment_service:
            self.enrichment_service.stop()
        self.store.close()

    @staticmethod
    def _clean_content(content: str) -> str:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content is required")
        return content.strip()

    @staticmethod
    def _clean_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _bounded_float(value: Any, minimum: float, maximum: float) -> float:
        return max(minimum, min(float(value), maximum))

    @staticmethod
    def _normalize_tags(raw: list[str] | str | None) -> list[str]:
        if raw is None:
            return []
        values = raw.split(",") if isinstance(raw, str) else raw
        tags = []
        seen = set()
        for value in values:
            if not isinstance(value, str):
                continue
            tag = re.sub(r"\s+", " ", value.strip().lower())
            if not tag or tag in seen:
                continue
            seen.add(tag)
            tags.append(tag)
        return tags

    @staticmethod
    def _tag_prefixes(tags: list[str]) -> list[str]:
        prefixes = []
        seen = set()
        for tag in tags:
            parts = [part for part in re.split(r"[:/]", tag) if part]
            for index in range(1, len(parts) + 1):
                prefix = ":".join(parts[:index])
                if prefix not in seen:
                    seen.add(prefix)
                    prefixes.append(prefix)
        return prefixes
