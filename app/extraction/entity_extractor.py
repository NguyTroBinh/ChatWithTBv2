from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

from app.providers.litellm_client import LiteLLMClient

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "entity_extraction.vi.md"
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class EntityExtractor:
    def __init__(self, llm_client: LiteLLMClient):
        self.llm_client = llm_client
        self.system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    @classmethod
    def from_config(cls) -> "EntityExtractor":
        return cls(llm_client=LiteLLMClient())

    def extract(self, chunk_id: str, text: str) -> dict:
        """Return {entities: [...], relationships: [...]} ready for Neo4jGraphStore."""
        raw = self.llm_client.generate([
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"VĂN BẢN:\n{text.strip()}"},
        ])
        parsed = self._parse(raw)
        entities = [self._normalize_entity(e) for e in parsed.get("entities", [])]
        relationships = [
            self._normalize_relationship(r, entities, chunk_id)
            for r in parsed.get("relationships", [])
            if self._relationship_valid(r, entities)
        ]
        return {"entities": entities, "relationships": relationships}

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse(raw: str) -> dict:
        match = _JSON_RE.search(raw)
        if not match:
            return {}
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _entity_id(canonical_name: str) -> str:
        return hashlib.sha256(canonical_name.strip().lower().encode()).hexdigest()[:16]

    @classmethod
    def _normalize_entity(cls, e: dict) -> dict:
        canonical = (e.get("canonicalName") or "").strip()
        return {
            "id": cls._entity_id(canonical),
            "canonicalName": canonical,
            "aliases": e.get("aliases") or [],
            "type": (e.get("type") or "OTHER").upper(),
            "description": (e.get("description") or "").strip(),
            "confidence": float(e.get("confidence") or 0.0),
        }

    @classmethod
    def _normalize_relationship(cls, r: dict, entities: list[dict], chunk_id: str) -> dict:
        name_to_id = {e["canonicalName"]: e["id"] for e in entities}
        return {
            "source_id": name_to_id[r["source"]],
            "target_id": name_to_id[r["target"]],
            "type": (r.get("type") or "RELATED").upper(),
            "description": (r.get("description") or "").strip(),
            "confidence": float(r.get("confidence") or 0.0),
            "weight": 1.0,
            "sourceChunkIds": [chunk_id],
        }

    @staticmethod
    def _relationship_valid(r: dict, entities: list[dict]) -> bool:
        names = {e["canonicalName"] for e in entities}
        return r.get("source") in names and r.get("target") in names
