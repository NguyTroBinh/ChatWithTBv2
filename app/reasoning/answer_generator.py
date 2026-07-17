from pathlib import Path
from app.providers.litellm_client import LiteLLMClient

class AnswerGenerator:
    def __init__(self, llm_client: LiteLLMClient, prompt_path: str = "prompts/answer.vi.md"):
        self.llm_client = llm_client
        self.system_prompt = Path(prompt_path).read_text(encoding="utf-8")

    @classmethod
    def from_config(cls) -> "AnswerGenerator":
        return cls(llm_client=LiteLLMClient())

    def generate(self, query: str, chunks: list[dict], chat_mode: str = "naive") -> dict:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")
        if chat_mode not in {"naive", "local"}:
            raise ValueError("chat_mode must be naive or local")
        if not chunks:
            return {
                "answer": "Chưa đủ thông tin trong tài liệu để trả lời câu hỏi này.",
                "evidence": [],
                "warnings": ["no_context"],
            }

        evidence = self._evidence(chunks)
        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": f"CÂU HỎI:\n{query.strip()}\n\nCHẾ ĐỘ CHAT:\n{chat_mode}\n\nCONTEXT:\n{self._context(chunks, chat_mode)}",
            },
        ]
        return {
            "answer": self.llm_client.generate(messages),
            "evidence": evidence,
            "warnings": self._warnings(evidence),
        }

    @staticmethod
    def _context(chunks: list[dict], chat_mode: str = "naive") -> str:
        blocks = []
        for index, chunk in enumerate(chunks, start=1):
            metadata = chunk.get("metadata") or {}
            file_name = chunk.get("file_name", "")
            page_start = metadata.get("page_start")
            page_end = metadata.get("page_end")
            if page_start and page_end and page_start != page_end:
                source = f"{file_name}, trang {page_start}-{page_end}"
            elif page_start:
                source = f"{file_name}, trang {page_start}"
            else:
                source = file_name

            blocks.append(
                "\n".join(
                    AnswerGenerator._context_lines(index, source, chunk, metadata, chat_mode)
                )
            )
        return "\n\n".join(blocks)

    @staticmethod
    def _context_lines(index: int, source: str, chunk: dict, metadata: dict, chat_mode: str) -> list[str]:
        lines = [f"[Đoạn {index}] Nguồn: {source}"]
        if chat_mode == "local":
            matched_entities = metadata.get("matched_entities") or []
            relationship_context = (metadata.get("relationship_context") or "").strip()
            community_context = (metadata.get("community_context") or "").strip()
            if matched_entities:
                lines.append(f"Thực thể khớp: {', '.join(matched_entities)}")
            if relationship_context:
                lines.extend(["Ngữ cảnh quan hệ:", relationship_context])
            if community_context:
                lines.extend(["Ngữ cảnh cộng đồng:", community_context])
            lines.append("Nội dung đoạn:")
        lines.append(chunk.get("content", ""))
        return lines

    @staticmethod
    def _evidence(chunks: list[dict]) -> list[dict]:
        evidence = []
        for chunk in chunks:
            metadata = chunk.get("metadata") or {}
            evidence.append(
                {
                    "chunkId": chunk.get("id") or metadata.get("chunk_id"),
                    "documentId": metadata.get("document_id"),
                    "fileName": chunk.get("file_name"),
                    "pageStart": metadata.get("page_start"),
                    "pageEnd": metadata.get("page_end"),
                    "text": chunk.get("content", ""),
                    "score": chunk.get("score"),
                    "position": metadata.get("position"),
                    "sectionPath": metadata.get("section_path"),
                }
            )
        return evidence

    @staticmethod
    def _warnings(evidence: list[dict]) -> list[str]:
        if any(item.get("pageStart") is None or item.get("pageEnd") is None for item in evidence):
            return ["page_numbers_unavailable"]
        return []
