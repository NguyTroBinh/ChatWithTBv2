from pathlib import Path
from app.providers.litellm_client import LiteLLMClient

class AnswerGenerator:
    def __init__(self, llm_client: LiteLLMClient, prompt_path: str = "prompts/answer.vi.md"):
        self.llm_client = llm_client
        self.system_prompt = Path(prompt_path).read_text(encoding="utf-8")

    @classmethod
    def from_config(cls) -> "AnswerGenerator":
        return cls(llm_client=LiteLLMClient())

    def generate(self, query: str, chunks: list[dict]) -> dict:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")
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
                "content": f"CÂU HỎI:\n{query.strip()}\n\nCONTEXT:\n{self._context(chunks)}",
            },
        ]
        return {
            "answer": self.llm_client.generate(messages),
            "evidence": evidence,
            "warnings": self._warnings(evidence),
        }

    @staticmethod
    def _context(chunks: list[dict]) -> str:
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
                    [
                        f"[Đoạn {index}] Nguồn: {source}",
                        chunk.get("content", ""),
                    ]
                )
            )
        return "\n\n".join(blocks)

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
