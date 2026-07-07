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
                "citations": [],
                "usedEvidence": [],
                "warnings": ["no_context"],
            }

        citations = self._citations(chunks)
        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": f"CÂU HỎI:\n{query.strip()}\n\nCONTEXT:\n{self._context(chunks)}",
            },
        ]
        return {
            "answer": self.llm_client.generate(messages),
            "citations": citations,
            "usedEvidence": chunks,
            "warnings": [],
        }

    @staticmethod
    def _context(chunks: list[dict]) -> str:
        blocks = []
        for index, chunk in enumerate(chunks, start=1):
            metadata = chunk.get("metadata") or {}
            page_start = metadata.get("page_start")
            page_end = metadata.get("page_end")
            if page_start and page_end and page_start != page_end:
                pages = f"trang {page_start}-{page_end}"
            elif page_start:
                pages = f"trang {page_start}"
            else:
                pages = "không rõ trang"

            blocks.append(
                "\n".join(
                    [
                        f"[C{index}] {chunk.get('file_name', '')}, {pages}, chunk_id={metadata.get('chunk_id', chunk.get('id', ''))}",
                        chunk.get("content", ""),
                    ]
                )
            )
        return "\n\n".join(blocks)

    @staticmethod
    def _citations(chunks: list[dict]) -> list[dict]:
        citations = []
        for index, chunk in enumerate(chunks, start=1):
            metadata = chunk.get("metadata") or {}
            citations.append(
                {
                    "id": f"C{index}",
                    "fileName": chunk.get("file_name", ""),
                    "pageStart": metadata.get("page_start"),
                    "pageEnd": metadata.get("page_end"),
                    "chunkId": metadata.get("chunk_id", chunk.get("id", "")),
                }
            )
        return citations
