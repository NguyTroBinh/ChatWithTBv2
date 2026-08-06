from pathlib import Path
from app.providers.context_compressor import ContextCompressor
from app.providers.litellm_client import LiteLLMClient
from app.memory.config import LIMIT_MESSAGES

class AnswerGenerator:
    def __init__(
        self,
        llm_client: LiteLLMClient,
        prompt_path: str = "prompts/answer.vi.md",
        context_compressor: ContextCompressor | None = None,
    ):
        self.llm_client = llm_client
        self.system_prompt = Path(prompt_path).read_text(encoding="utf-8")
        self.context_compressor = context_compressor or ContextCompressor.from_config()

    @classmethod
    def from_config(cls) -> "AnswerGenerator":
        return cls(llm_client=LiteLLMClient())

    def generate(
        self,
        query: str,
        chunks: list[dict],
        chat_mode: str = "naive",
        conversation_history: list[dict] | None = None,
        long_term_memories: list[dict] | None = None,
    ) -> dict:
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
        compression_report = self.context_compressor.new_report()
        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": self._user_content(
                    query,
                    chat_mode,
                    chunks,
                    conversation_history,
                    long_term_memories,
                    compression_report,
                ),
            },
        ]
        warnings = self._warnings(evidence)
        if compression_report["errors"]:
            warnings.append("context_compression_error")

        result = {
            "answer": self.llm_client.generate(messages),
            "evidence": evidence,
            "warnings": warnings,
        }
        if compression_report["enabled"]:
            result["contextCompression"] = compression_report
        return result

    def _user_content(
        self,
        query: str,
        chat_mode: str,
        chunks: list[dict],
        conversation_history: list[dict] | None,
        long_term_memories: list[dict] | None,
        compression_report: dict,
    ) -> str:
        parts = [f"CÂU HỎI:\n{query.strip()}", f"CHẾ ĐỘ CHAT:\n{chat_mode}"]
        history_section = self._history_section(conversation_history, query, compression_report)
        if history_section:
            parts.append(history_section)
        memory_section = self._memory_section(long_term_memories, query, compression_report)
        if memory_section:
            parts.append(memory_section)
        parts.append(f"CONTEXT:\n{self._context(chunks, query, chat_mode, compression_report)}")
        return "\n\n".join(parts)

    def _history_section(
        self,
        conversation_history: list[dict] | None,
        query: str,
        compression_report: dict,
    ) -> str | None:
        if not conversation_history:
            return None
        lines = []
        limit_msg = LIMIT_MESSAGES if LIMIT_MESSAGES else 20
        for message in conversation_history[-limit_msg:]:
            role = message.get("role")
            content = (message.get("content") or "").strip()
            if not content:
                continue
            if self.context_compressor.compress_history:
                content = self.context_compressor.compress_text(
                    content,
                    query,
                    "short_history",
                    compression_report,
                )
            label = "Người dùng" if role == "user" else "Trợ lý"
            lines.append(f"- {label}: {content}")
        if not lines:
            return None
        return "LỊCH SỬ HỘI THOẠI:\n" + "\n".join(lines)

    def _memory_section(
        self,
        long_term_memories: list[dict] | None,
        query: str,
        compression_report: dict,
    ) -> str | None:
        if not long_term_memories:
            return None
        lines = []
        for result in long_term_memories:
            memory = result.get("memory") if isinstance(result, dict) else None
            memory = memory or (result if isinstance(result, dict) else None)
            content = (memory.get("content") or "").strip() if memory else ""
            if not content:
                continue
            if self.context_compressor.compress_long_memory:
                content = self.context_compressor.compress_text(
                    content,
                    query,
                    "long_memory",
                    compression_report,
                )
            kind = (memory.get("type") or "Context").strip().capitalize()
            lines.append(f"- [{kind}] {content}")
        if not lines:
            return None
        return "KÝ ỨC DÀI HẠN:\n" + "\n".join(lines)

    def _context(
        self,
        chunks: list[dict],
        query: str,
        chat_mode: str,
        compression_report: dict,
    ) -> str:
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
                    self._context_lines(
                        index,
                        source,
                        chunk,
                        metadata,
                        query,
                        chat_mode,
                        compression_report,
                    )
                )
            )
        return "\n\n".join(blocks)

    def _context_lines(
        self,
        index: int,
        source: str,
        chunk: dict,
        metadata: dict,
        query: str,
        chat_mode: str,
        compression_report: dict,
    ) -> list[str]:
        lines = [f"[Đoạn {index}] Nguồn: {source}"]
        if chat_mode == "local":
            matched_entities = metadata.get("matched_entities") or []
            relationship_context = (metadata.get("relationship_context") or "").strip()
            community_context = (metadata.get("community_context") or "").strip()
            if matched_entities:
                lines.append(f"Thực thể khớp: {', '.join(matched_entities)}")
            if relationship_context:
                if self.context_compressor.compress_chunks:
                    relationship_context = self.context_compressor.compress_text(
                        relationship_context,
                        query,
                        "relationship_context",
                        compression_report,
                    )
                lines.extend(["Ngữ cảnh quan hệ:", relationship_context])
            if community_context:
                if self.context_compressor.compress_chunks:
                    community_context = self.context_compressor.compress_text(
                        community_context,
                        query,
                        "community_context",
                        compression_report,
                    )
                lines.extend(["Ngữ cảnh cộng đồng:", community_context])
            lines.append("Nội dung đoạn:")
        content = chunk.get("content", "")
        if self.context_compressor.compress_chunks:
            content = self.context_compressor.compress_text(
                content,
                query,
                "chunk",
                compression_report,
            )
        lines.append(content)
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
