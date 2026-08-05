from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, Thread
from typing import Any

from app.memory.config import (
    MEMORY_ENRICHMENT_CANDIDATE_LIMIT,
    MEMORY_ENRICHMENT_ENABLED,
    MEMORY_ENRICHMENT_MAX_ATTEMPTS,
    MEMORY_ENRICHMENT_RETRY_BACKOFF_SECONDS,
    MEMORY_ENRICHMENT_SIMILARITY_LIMIT,
    MEMORY_ENRICHMENT_SIMILARITY_THRESHOLD,
)
from app.memory.longterm.recall import DEFAULT_EXPAND_RELATIONS
from app.providers.litellm_client import LiteLLMClient


_PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "memory_enrichment.vi.md"
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_SAFE_RELATION_RE = re.compile(r"[A-Z][A-Z0-9_]{0,60}")
_MEMORY_TYPES = {"Decision", "Pattern", "Preference", "Style", "Habit", "Insight", "Context"}
_LOGGER = logging.getLogger(__name__)


class MemoryEnrichmentService:
    def __init__(
        self,
        store,
        llm_client: LiteLLMClient,
        candidate_limit: int = MEMORY_ENRICHMENT_CANDIDATE_LIMIT,
        enabled: bool = MEMORY_ENRICHMENT_ENABLED,
        max_attempts: int = MEMORY_ENRICHMENT_MAX_ATTEMPTS,
        retry_backoff_seconds: float = MEMORY_ENRICHMENT_RETRY_BACKOFF_SECONDS,
        similarity_limit: int = MEMORY_ENRICHMENT_SIMILARITY_LIMIT,
        similarity_threshold: float = MEMORY_ENRICHMENT_SIMILARITY_THRESHOLD,
    ):
        self.store = store
        self.llm_client = llm_client
        self.candidate_limit = max(0, int(candidate_limit))
        self.enabled = enabled
        self.max_attempts = max(1, int(max_attempts))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.similarity_limit = max(1, int(similarity_limit))
        self.similarity_threshold = float(similarity_threshold)
        self.system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
        self.queue: Queue[tuple[str, int] | None] = Queue()
        self.stop_event = Event()
        self.thread: Thread | None = None
        self._pending: set[str] = set()
        self._inflight: set[str] = set()
        self._lock = Lock()

    @classmethod
    def from_config(cls, store) -> "MemoryEnrichmentService":
        return cls(store=store, llm_client=LiteLLMClient(temperature=0, max_tokens=1200))

    def start(self) -> None:
        if not self.enabled or (self.thread and self.thread.is_alive()):
            return
        self.stop_event.clear()
        self.thread = Thread(target=self._run, name="memory-enrichment", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.queue.put(None)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)

    def enqueue(self, memory_id: str) -> None:
        if not self.enabled or not memory_id:
            return
        with self._lock:
            if memory_id in self._pending or memory_id in self._inflight:
                return
            self._pending.add(memory_id)
        self.start()
        self.queue.put((memory_id, 0))

    def enrich_now(self, memory_id: str) -> dict | None:
        memory = self.store.get_memory(memory_id)
        if not memory:
            return None
        session_id = memory.get("sessionId")
        if not session_id:
            return None

        candidates = [
            candidate
            for candidate in self.store.list_recent(self.candidate_limit + 1, session_id=session_id)
            if candidate.get("id") != memory_id
        ][: self.candidate_limit]
        raw = self.llm_client.generate(
            [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": self._user_prompt(memory, candidates)},
            ]
        )
        enrichment = self._normalize(memory, candidates, self._parse(raw))
        self.store.apply_enrichment(enrichment)
        try:
            self.store.create_similarity_edges(
                memory_id,
                threshold=self.similarity_threshold,
                limit=self.similarity_limit,
                session_id=session_id,
            )
        except Exception:
            _LOGGER.warning("Auto SIMILAR_TO linking failed for memory %s", memory_id, exc_info=True)
        return enrichment

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                job = self.queue.get(timeout=0.2)
            except Empty:
                continue
            if job is None:
                return
            memory_id, attempt = job
            with self._lock:
                self._pending.discard(memory_id)
                self._inflight.add(memory_id)
            try:
                self.enrich_now(memory_id)
                _LOGGER.debug("Enriched memory %s (attempt %d)", memory_id, attempt + 1)
            except Exception as exc:
                _LOGGER.warning("Enrichment failed memory=%s attempt=%d: %s", memory_id, attempt + 1, exc)
                if attempt + 1 < self.max_attempts:
                    _LOGGER.info("Retrying enrichment for memory %s (attempt %d)", memory_id, attempt + 2)
                    if self.retry_backoff_seconds:
                        time.sleep(self.retry_backoff_seconds)
                    self.queue.put((memory_id, attempt + 1))
                else:
                    _LOGGER.error("Giving up on enrichment for memory %s after %d attempts", memory_id, self.max_attempts)
            finally:
                with self._lock:
                    self._inflight.discard(memory_id)
                self.queue.task_done()

    @staticmethod
    def _user_prompt(memory: dict, candidates: list[dict]) -> str:
        candidate_lines = [
            f"- id={candidate.get('id')}: {candidate.get('content', '')[:500]}"
            for candidate in candidates
        ]
        return (
            "MEMORY CẦN ENRICH:\n"
            f"id={memory.get('id')}\n"
            f"session_id={memory.get('sessionId')}\n"
            f"content={memory.get('content', '')}\n\n"
            "MEMORY ỨNG VIÊN ĐỂ TẠO QUAN HỆ MEMORY-MEMORY:\n"
            + ("\n".join(candidate_lines) if candidate_lines else "(không có)")
        )

    @staticmethod
    def _parse(raw: str) -> dict:
        match = _JSON_RE.search(raw or "")
        if not match:
            return {}
        try:
            data = json.loads(match.group())
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    @classmethod
    def _normalize(cls, memory: dict, candidates: list[dict], payload: dict) -> dict:
        session_id = memory["sessionId"]
        memory_id = memory["id"]
        candidate_ids = {candidate.get("id") for candidate in candidates}
        metadata = dict(memory.get("metadata") or {})

        memory_type = payload.get("type")
        if memory_type not in _MEMORY_TYPES:
            memory_type = memory.get("type") if memory.get("type") in _MEMORY_TYPES else "Context"
        confidence = _bounded_float(payload.get("confidence", memory.get("confidence", 0.9)))

        entities = []
        seen_names = set()
        for raw_entity in payload.get("entities", []) if isinstance(payload.get("entities"), list) else []:
            name = str(raw_entity.get("name") or raw_entity.get("canonicalName") or "").strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen_names:
                continue
            seen_names.add(key)
            entities.append(
                {
                    "id": cls._entity_id(session_id, name),
                    "sessionId": session_id,
                    "name": name,
                    "type": cls._safe_relation(raw_entity.get("type"), default="OTHER"),
                    "description": str(raw_entity.get("description") or "").strip(),
                    "confidence": _bounded_float(raw_entity.get("confidence", 0.0)),
                }
            )

        name_to_id = {entity["name"].casefold(): entity["id"] for entity in entities}
        entity_relationships = []
        for raw_relation in payload.get("entity_relationships", []) if isinstance(payload.get("entity_relationships"), list) else []:
            source_id = name_to_id.get(str(raw_relation.get("source") or "").strip().casefold())
            target_id = name_to_id.get(str(raw_relation.get("target") or "").strip().casefold())
            if not source_id or not target_id or source_id == target_id:
                continue
            entity_relationships.append(
                {
                    "source_id": source_id,
                    "target_id": target_id,
                    "type": cls._safe_relation(raw_relation.get("type"), default="RELATED_TO"),
                    "description": str(raw_relation.get("description") or "").strip(),
                    "confidence": _bounded_float(raw_relation.get("confidence", 0.0)),
                }
            )

        memory_relationships = []
        for raw_relation in payload.get("memory_relationships", []) if isinstance(payload.get("memory_relationships"), list) else []:
            target_id = str(raw_relation.get("target_memory_id") or raw_relation.get("targetId") or "").strip()
            if target_id not in candidate_ids or target_id == memory_id:
                continue
            relation_type = cls._safe_relation(raw_relation.get("type"), default="RELATES_TO")
            if relation_type not in DEFAULT_EXPAND_RELATIONS:
                relation_type = "RELATES_TO"
            memory_relationships.append(
                {
                    "target_id": target_id,
                    "type": relation_type,
                    "strength": _bounded_float(raw_relation.get("strength", raw_relation.get("confidence", 0.5))),
                    "description": str(raw_relation.get("description") or "").strip(),
                }
            )

        metadata["enrichment"] = {
            "entities": entities,
            "entityRelationships": entity_relationships,
            "memoryRelationships": memory_relationships,
        }
        tags = list(dict.fromkeys([*(memory.get("tags") or []), f"type:{memory_type.lower()}"]))

        return {
            "memory_id": memory_id,
            "session_id": session_id,
            "type": memory_type,
            "confidence": confidence,
            "tags": tags,
            "tag_prefixes": _tag_prefixes(tags),
            "metadata": json.dumps(metadata, ensure_ascii=False, default=str),
            "entities": entities,
            "entity_relationships": entity_relationships,
            "memory_relationships": memory_relationships,
        }

    @staticmethod
    def _entity_id(session_id: str, name: str) -> str:
        return hashlib.sha256(f"{session_id}:{name.casefold()}".encode()).hexdigest()[:24]

    @staticmethod
    def _safe_relation(value: Any, default: str) -> str:
        relation = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip().upper()).strip("_")
        return relation if _SAFE_RELATION_RE.fullmatch(relation) else default


def _bounded_float(value: Any, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        return max(minimum, min(float(value), maximum))
    except (TypeError, ValueError):
        return minimum


def _tag_prefixes(tags: list[str]) -> list[str]:
    prefixes = []
    seen = set()
    for tag in tags:
        parts = [part for part in re.split(r"[:/]", str(tag).strip().lower()) if part]
        for index in range(1, len(parts) + 1):
            prefix = ":".join(parts[:index])
            if prefix not in seen:
                seen.add(prefix)
                prefixes.append(prefix)
    return prefixes
