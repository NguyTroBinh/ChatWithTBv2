from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

from app.memory.config import (
    MEMORY_RECALL_EXPANSION_LIMIT,
    MEMORY_RECALL_MIN_SCORE,
    MEMORY_RECALL_RELATION_LIMIT,
    MEMORY_SEARCH_WEIGHT_CONFIDENCE,
    MEMORY_SEARCH_WEIGHT_EXACT,
    MEMORY_SEARCH_WEIGHT_IMPORTANCE,
    MEMORY_SEARCH_WEIGHT_KEYWORD,
    MEMORY_SEARCH_WEIGHT_RECENCY,
    MEMORY_SEARCH_WEIGHT_RELATION,
    MEMORY_SEARCH_WEIGHT_TAG,
    MEMORY_SEARCH_WEIGHT_VECTOR,
)


DEFAULT_EXPAND_RELATIONS = {
    "RELATES_TO",
    "LEADS_TO",
    "OCCURRED_BEFORE",
    "PREFERS_OVER",
    "EXEMPLIFIES",
    "CONTRADICTS",
    "REINFORCES",
    "INVALIDATED_BY",
    "EVOLVED_INTO",
    "DERIVED_FROM",
    "PART_OF",
}

SEARCH_STOPWORDS = {
    "ai",
    "anh",
    "bạn",
    "bằng",
    "bị",
    "bởi",
    "cái",
    "các",
    "cần",
    "cho",
    "chúng",
    "có",
    "của",
    "cùng",
    "cũng",
    "đang",
    "đây",
    "đã",
    "để",
    "đến",
    "đó",
    "được",
    "dưới",
    "gì",
    "giữa",
    "hay",
    "hãy",
    "hoặc",
    "hơn",
    "khi",
    "không",
    "là",
    "lại",
    "lên",
    "liệu",
    "lòng",
    "mà",
    "mình",
    "một",
    "nào",
    "này",
    "nên",
    "nếu",
    "người",
    "như",
    "những",
    "nữa",
    "ra",
    "rằng",
    "rất",
    "sau",
    "sẽ",
    "tại",
    "ta",
    "tài",
    "theo",
    "thì",
    "trên",
    "trong",
    "trước",
    "từ",
    "tôi",
    "và",
    "vào",
    "về",
    "vì",
    "với",
    "vui",
    "xuống",
}


class LongTermRecallService:
    def __init__(
        self,
        store,
        embedding_service,
        default_limit: int = 5,
        relation_limit: int = MEMORY_RECALL_RELATION_LIMIT,
        expansion_limit: int = MEMORY_RECALL_EXPANSION_LIMIT,
        min_score: float = MEMORY_RECALL_MIN_SCORE,
    ):
        self.store = store
        self.embedding_service = embedding_service
        self.default_limit = default_limit
        self.relation_limit = relation_limit
        self.expansion_limit = expansion_limit
        self.min_score = min_score

    def recall(
        self,
        query: str,
        *,
        limit: int | None = None,
        session_id: str | None = None,
        expand_relations: bool = True,
    ) -> list[dict]:
        query = (query or "").strip()
        if not query:
            raise ValueError("query is required")
        session_id = (session_id or "").strip()
        if not session_id:
            raise ValueError("session_id is required for long-term recall")

        limit = max(1, int(limit or self.default_limit))
        seen_ids: set[str] = set()

        query_embedding = self.embedding_service.embed_query(query)
        vector_matches = self._vector_matches(query_embedding, limit, session_id, seen_ids)

        results = list(vector_matches)
        remaining = max(0, limit - len(results))
        if remaining:
            results.extend(self._keyword_matches(query, remaining, session_id, seen_ids))

        query_tokens = extract_keywords(query)
        self._score_results(results, query, query_tokens)
        results, dedup_removed = dedupe_results(results)
        results = self._sort_and_filter(results)

        expansion_results: list[dict] = []
        if expand_relations and self.expansion_limit > 0:
            expansion_results = self._expand_related(results, seen_ids, query, query_tokens, session_id)
            results = self._sort_and_filter(results + expansion_results)

        for result in results:
            result["_query"] = query
        if dedup_removed:
            for result in results:
                result.setdefault("recall", {})["dedup_removed"] = dedup_removed
        returned_results = results[: limit + (len(expansion_results) if expand_relations else 0)]
        self.store.touch_memories(
            [result["id"] for result in returned_results if result.get("id")],
            session_id=session_id,
        )
        return returned_results

    def _vector_matches(
        self,
        query_embedding: list[float],
        limit: int,
        session_id: str | None,
        seen_ids: set[str],
    ) -> list[dict]:
        matches = []
        for memory in self.store.vector_search(query_embedding, limit=limit, session_id=session_id):
            result = self._format_result(memory, "vector", "neo4j_vector", session_id, seen_ids)
            if result:
                matches.append(result)
        return matches

    def _keyword_matches(
        self,
        query: str,
        limit: int,
        session_id: str | None,
        seen_ids: set[str],
    ) -> list[dict]:
        keywords = extract_keywords(query)
        matches = []
        for memory in self.store.keyword_search(query.lower(), keywords, limit=limit, session_id=session_id):
            result = self._format_result(memory, "keyword", "neo4j_keyword", session_id, seen_ids)
            if result:
                matches.append(result)
        return matches

    def _format_result(
        self,
        memory: dict,
        match_type: str,
        source: str,
        session_id: str,
        seen_ids: set[str],
    ) -> dict | None:
        memory_id = str(memory.get("id") or "").strip()
        if not memory_id or memory_id in seen_ids:
            return None
        seen_ids.add(memory_id)
        score = safe_float(memory.get("score"))
        return {
            "id": memory_id,
            "score": score,
            "match_score": score,
            "match_type": match_type,
            "source": source,
            "memory": memory,
            "relations": self.store.fetch_relations(memory_id, limit=self.relation_limit, session_id=session_id),
        }

    def _score_results(self, results: list[dict], query: str, query_tokens: list[str]) -> None:
        for result in results:
            final_score, components = compute_metadata_score(result, query, query_tokens)
            result["score_components"] = components
            result["final_score"] = final_score
            result["original_score"] = result.get("score", 0.0)
            result["score"] = final_score

    def _expand_related(
        self,
        seed_results: list[dict],
        seen_ids: set[str],
        query: str,
        query_tokens: list[str],
        session_id: str | None,
    ) -> list[dict]:
        expansions = []
        total_added = 0
        relation_types = sorted(DEFAULT_EXPAND_RELATIONS)
        for seed_rank, seed in enumerate(seed_results):
            if total_added >= self.expansion_limit:
                break

            seed_id = str(seed.get("id") or "").strip()
            if not seed_id:
                continue
            seed_score = safe_float(seed.get("final_score") or seed.get("score"))

            # Memory-memory relations (from enrichment graph).
            related = self.store.expand_related(
                seed_id,
                limit=self.relation_limit,
                session_id=session_id,
                relation_types=relation_types,
            )
            for item in related:
                if total_added >= self.expansion_limit:
                    break
                expansion = self._expansion_result(
                    item["memory"],
                    seed_id,
                    seed_score,
                    seed_rank,
                    item.get("relationType") or "RELATES_TO",
                    safe_float(item.get("relationStrength")),
                    "neo4j_graph",
                    query,
                    query_tokens,
                )
                if expansion is None or expansion["id"] in seen_ids:
                    continue
                if item.get("relationKind"):
                    expansion["relations"][0]["kind"] = item["relationKind"]
                expansions.append(expansion)
                seen_ids.add(expansion["id"])
                total_added += 1

            # Shared-entity neighbors (memory-memory via common MemoryEntity).
            entity_neighbors = self.store.expand_via_entities(seed_id, limit=self.relation_limit, session_id=session_id)
            for item in entity_neighbors:
                if total_added >= self.expansion_limit:
                    break
                expansion = self._expansion_result(
                    item["memory"],
                    seed_id,
                    seed_score,
                    seed_rank,
                    item.get("relationType") or "SHARES_ENTITY",
                    safe_float(item.get("relationStrength")),
                    "neo4j_entity",
                    query,
                    query_tokens,
                )
                if expansion is None or expansion["id"] in seen_ids:
                    continue
                expansions.append(expansion)
                seen_ids.add(expansion["id"])
                total_added += 1

        return expansions

    @staticmethod
    def _expansion_result(
        memory: dict,
        seed_id: str,
        seed_score: float,
        seed_rank: int,
        relation_type: str,
        relation_strength: float,
        source: str,
        query: str,
        query_tokens: list[str],
    ) -> dict | None:
        memory_id = str(memory.get("id") or "").strip()
        if not memory_id:
            return None
        relation_score = relation_strength + max(seed_score, 0.0) * 0.25
        edge = {
            "type": relation_type,
            "strength": relation_strength,
            "from": seed_id,
            "seed_rank": seed_rank,
            "seed_score": seed_score,
        }
        result = {
            "id": memory_id,
            "match_type": "relation",
            "source": source,
            "memory": memory,
            "relations": [edge],
            "related_to": [edge],
            "relation_score": relation_score,
            "match_score": relation_score,
        }
        final_score, components = compute_metadata_score(result, query, query_tokens)
        components["relation"] = relation_score
        result["score_components"] = components
        result["final_score"] = final_score
        result["score"] = final_score
        return result

    def _sort_and_filter(self, results: list[dict]) -> list[dict]:
        if self.min_score > 0:
            results = [result for result in results if safe_float(result.get("final_score")) >= self.min_score]
        return sorted(
            results,
            key=lambda result: (
                -safe_float(result.get("final_score")),
                result.get("source") != "neo4j_vector",
                -safe_float(result.get("original_score")),
                -safe_float((result.get("memory") or {}).get("importance")),
            ),
        )


def extract_keywords(text: str) -> list[str]:
    words = re.findall(r"[\w\-]+", (text or "").lower(), flags=re.UNICODE)
    keywords = []
    seen = set()
    for word in words:
        cleaned = word.strip("-_")
        if len(cleaned) < 2 or cleaned in SEARCH_STOPWORDS or cleaned in seen:
            continue
        seen.add(cleaned)
        keywords.append(cleaned)
    return keywords


def dedupe_results(results: list[dict]) -> tuple[list[dict], int]:
    buckets = {}
    fp_to_key = {}
    removed = 0

    for result in results:
        memory = result.get("memory") or {}
        memory_id = result.get("id") or memory.get("id")
        fingerprint = _fingerprint_content(memory.get("content") or "")
        key = f"id:{memory_id}" if memory_id else (f"fp:{fingerprint}" if fingerprint else None)
        if key is None:
            buckets[f"raw:{len(buckets)}"] = {"item": result, "sources": []}
            continue

        existing_key = key if key in buckets else fp_to_key.get(fingerprint)
        if existing_key:
            existing = buckets[existing_key]["item"]
            removed += 1
            if safe_float(result.get("final_score") or result.get("score")) > safe_float(
                existing.get("final_score") or existing.get("score")
            ):
                buckets[existing_key]["item"] = result
            buckets[existing_key]["sources"].append(memory_id or fingerprint or "unknown")
        else:
            buckets[key] = {"item": result, "sources": [memory_id or fingerprint or "unknown"]}
            if fingerprint:
                fp_to_key[fingerprint] = key

    deduped = []
    for entry in buckets.values():
        item = entry["item"]
        if len(entry["sources"]) > 1:
            item["deduped_from"] = sorted(set(entry["sources"]))
        deduped.append(item)
    return deduped, removed


def compute_metadata_score(result: dict, query: str, tokens: list[str]) -> tuple[float, dict[str, float]]:
    memory = result.get("memory") or {}
    metadata = parse_metadata(memory.get("metadata"))
    metadata_terms = collect_metadata_terms(metadata) if isinstance(metadata, dict) else set()
    tag_terms = {str(tag).lower() for tag in memory.get("tags") or [] if isinstance(tag, str)}

    token_hits = sum(1 for token in tokens if token in tag_terms or token in metadata_terms)
    normalized_query = (query or "").lower().strip()
    exact_match = 1.0 if normalized_query and normalized_query in metadata_terms else 0.0
    importance = safe_float(memory.get("importance"))
    confidence = safe_float(memory.get("confidence"))
    recency = compute_recency_score(memory.get("updatedAt") or memory.get("createdAt"))
    tag_score = token_hits / max(len(tokens), 1) if tokens else 0.0
    vector = safe_float(result.get("match_score")) if result.get("match_type") == "vector" else 0.0
    keyword = safe_float(result.get("match_score")) if result.get("match_type") in {"keyword", "trending"} else 0.0
    relation = safe_float(
        result.get("relation_score", result.get("match_score") if result.get("match_type") == "relation" else 0.0)
    )

    final = (
        MEMORY_SEARCH_WEIGHT_VECTOR * vector
        + MEMORY_SEARCH_WEIGHT_KEYWORD * keyword
        + MEMORY_SEARCH_WEIGHT_RELATION * relation
        + MEMORY_SEARCH_WEIGHT_TAG * tag_score
        + MEMORY_SEARCH_WEIGHT_IMPORTANCE * importance
        + MEMORY_SEARCH_WEIGHT_CONFIDENCE * confidence
        + MEMORY_SEARCH_WEIGHT_RECENCY * recency
        + MEMORY_SEARCH_WEIGHT_EXACT * exact_match
    )
    components = {
        "vector": vector,
        "keyword": keyword,
        "relation": relation,
        "tag": tag_score,
        "importance": importance,
        "confidence": confidence,
        "recency": recency,
        "exact": exact_match,
    }
    return final, components


def parse_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else value
        except json.JSONDecodeError:
            return value
    return value


def collect_metadata_terms(metadata: dict[str, Any]) -> set[str]:
    terms = set()

    def visit(item: Any) -> None:
        if isinstance(item, str):
            trimmed = item.strip()
            if not trimmed:
                return
            if len(trimmed) <= 256:
                lower = trimmed.lower()
                terms.add(lower)
                terms.update(re.findall(r"[a-z0-9_\-]+", lower))
        elif isinstance(item, (list, tuple, set)):
            for sub in item:
                visit(sub)
        elif isinstance(item, dict):
            for sub in item.values():
                visit(sub)

    visit(metadata)
    return terms


def compute_recency_score(value: Any) -> float:
    if not value:
        return 0.0
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age_days = max((datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0, 0.0)
    return max(0.0, 1.0 - age_days / 180.0)


def safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _fingerprint_content(content: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", (content or "").strip().lower())
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:320] or None
