from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.graph.neo4j_store import Neo4jGraphStore
    from app.providers.litellm_client import LiteLLMClient
    from app.providers.reranking import RerankerService
    from app.reasoning.answer_generator import AnswerGenerator
    from app.retrieval.naive import NaiveRetrievalService


DEFAULT_PLANNER_PROMPT_PATH = "prompts/deepsearch_planner.vi.md"


class DeepSearchPipeline:
    def __init__(
        self,
        graph_store: Neo4jGraphStore,
        retriever: NaiveRetrievalService,
        reranker: RerankerService,
        answer_generator: AnswerGenerator,
        planner_llm: LiteLLMClient,
        planner_prompt_path: str = DEFAULT_PLANNER_PROMPT_PATH,
        max_sub_queries: int = 3,
        max_rounds: int = 1,
        retrieval_top_k: int = 10,
    ):
        if max_sub_queries <= 0:
            raise ValueError("max_sub_queries must be greater than 0")
        if max_rounds <= 0:
            raise ValueError("max_rounds must be greater than 0")
        if retrieval_top_k <= 0:
            raise ValueError("retrieval_top_k must be greater than 0")

        self.graph_store = graph_store
        self.retriever = retriever
        self.reranker = reranker
        self.answer_generator = answer_generator
        self.planner_llm = planner_llm
        self.planner_prompt_template = Path(planner_prompt_path).read_text(encoding="utf-8")
        self.max_sub_queries = max_sub_queries
        self.max_rounds = min(max_rounds, 1)
        self.retrieval_top_k = retrieval_top_k

    @classmethod
    def from_config(cls) -> "DeepSearchPipeline":
        from app.chunking.chunker import ChunkingService
        from app.graph.neo4j_store import Neo4jGraphStore
        from app.providers.litellm_client import LiteLLMClient
        from app.providers.reranking import RerankerService
        from app.reasoning.answer_generator import AnswerGenerator
        from app.retrieval.naive import NaiveRetrievalService

        graph_store = Neo4jGraphStore.from_config()
        embedding_service = ChunkingService().embeddings
        return cls(
            graph_store=graph_store,
            retriever=NaiveRetrievalService(
                graph_store=graph_store,
                embedding_service=embedding_service,
            ),
            reranker=RerankerService(),
            answer_generator=AnswerGenerator.from_config(),
            planner_llm=LiteLLMClient(temperature=0, max_tokens=512),
        )

    def chat(
        self,
        query: str,
        top_k: int = 5,
        document_ids: list[str] | None = None,
        memory_context: dict | None = None,
    ) -> dict:
        query = self._clean_query(query)
        if top_k <= 0:
            raise ValueError("top_k must be greater than 0")
        if document_ids is not None:
            document_ids = [document_id.strip() for document_id in document_ids if document_id.strip()]
            if not document_ids:
                raise ValueError("document_ids cannot be empty")

        sub_queries, planner_fallback = self.plan(query)
        candidates, chunks, rounds = self._search_rounds(sub_queries, top_k, document_ids)
        retrieved_count = sum(item["retrievedCount"] for round_info in rounds for item in round_info["queries"])

        memory = memory_context or {}
        result = self.answer_generator.generate(
            query,
            chunks,
            chat_mode="naive",
            conversation_history=memory.get("conversation_history"),
            long_term_memories=memory.get("long_term_memories"),
        )
        result["mode"] = "deep"
        result["deepSearch"] = {
            "subQueries": sub_queries,
            "plannerFallback": planner_fallback,
            "rounds": rounds,
            "candidateCount": retrieved_count,
            "dedupedCount": len(chunks),
            "rerankedCount": len(candidates),
        }
        if planner_fallback:
            result["warnings"] = [*(result.get("warnings") or []), "deep_planner_fallback"]
        return result

    def plan(self, query: str) -> tuple[list[str], bool]:
        query = self._clean_query(query)
        messages = [
            {"role": "system", "content": self._planner_prompt()},
            {"role": "user", "content": query},
        ]
        try:
            raw = self.planner_llm.generate(messages)
            sub_queries = self._parse_sub_queries(raw)
        except Exception:
            return [query], True
        return sub_queries, False

    def close(self) -> None:
        if self.graph_store is not None:
            self.graph_store.close()

    def _search_rounds(
        self,
        sub_queries: list[str],
        per_query_top_k: int,
        document_ids: list[str] | None,
    ) -> tuple[list[dict], list[dict], list[dict]]:
        candidates = []
        rounds = []
        seen_queries = set()

        query_batches = [sub_queries]
        for round_index, batch in enumerate(query_batches[: self.max_rounds], start=1):
            round_queries = []
            for search_query in batch:
                key = self._query_key(search_query)
                if key in seen_queries:
                    continue
                seen_queries.add(key)
                chunks = self.retriever.retrieve(
                    search_query,
                    top_k=self.retrieval_top_k,
                    document_ids=document_ids,
                )
                reranked_chunks = self.reranker.rerank(search_query, chunks, top_k=per_query_top_k)
                candidates.extend(reranked_chunks)
                round_queries.append(
                    {
                        "query": search_query,
                        "retrievedCount": len(chunks),
                        "rerankedCount": len(reranked_chunks),
                    }
                )
            if round_queries:
                rounds.append({"round": round_index, "queries": round_queries})

        return candidates, self._dedup_chunks(candidates), rounds

    def _parse_sub_queries(self, raw: str) -> list[str]:
        payload = self._json_payload(raw)
        values: Any
        if isinstance(payload, dict):
            values = (
                payload.get("sub_queries")
                or payload.get("subQueries")
                or payload.get("queries")
                or payload.get("query")
            )
        else:
            values = payload

        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            raise ValueError("planner JSON does not contain a query list")

        queries = []
        seen = set()
        for item in values:
            text = self._query_text(item)
            if not text:
                continue
            key = self._query_key(text)
            if key in seen:
                continue
            seen.add(key)
            queries.append(text)
            if len(queries) >= self.max_sub_queries:
                break
        if not queries:
            raise ValueError("planner JSON does not contain usable queries")
        return queries

    def _planner_prompt(self) -> str:
        return self.planner_prompt_template.replace("{{max_sub_queries}}", str(self.max_sub_queries))

    @staticmethod
    def _json_payload(raw: str) -> Any:
        text = (raw or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        for opening, closing in (("{", "}"), ("[", "]")):
            start = text.find(opening)
            end = text.rfind(closing)
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
        raise ValueError("planner did not return valid JSON")

    @staticmethod
    def _query_text(item: Any) -> str:
        if isinstance(item, str):
            return item.strip()
        if isinstance(item, dict):
            for key in ("query", "sub_query", "subQuery", "text"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _dedup_chunks(chunks: list[dict]) -> list[dict]:
        deduped = {}
        for chunk in chunks:
            metadata = chunk.get("metadata") or {}
            chunk_id = chunk.get("id") or metadata.get("chunk_id")
            if not chunk_id:
                chunk_id = "|".join(
                    str(value)
                    for value in (
                        chunk.get("file_name"),
                        metadata.get("position"),
                        chunk.get("content"),
                    )
                )
            current = deduped.get(chunk_id)
            if current is None or DeepSearchPipeline._score(chunk) > DeepSearchPipeline._score(current):
                deduped[chunk_id] = chunk
        return list(deduped.values())

    @staticmethod
    def _score(chunk: dict) -> float:
        try:
            return float(chunk.get("score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _clean_query(query: str) -> str:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")
        return query.strip()

    @staticmethod
    def _query_key(query: str) -> str:
        return " ".join(query.casefold().split())
