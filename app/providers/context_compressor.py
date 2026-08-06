from app.core.config import (
    CONTEXT_COMPRESSION_COMPRESS_CHUNKS,
    CONTEXT_COMPRESSION_COMPRESS_HISTORY,
    CONTEXT_COMPRESSION_COMPRESS_LONG_MEMORY,
    CONTEXT_COMPRESSION_ENABLED,
    CONTEXT_COMPRESSION_MIN_TOKENS,
    CONTEXT_COMPRESSION_TARGET_RATIO,
)


class ContextCompressor:
    def __init__(
        self,
        enabled: bool = CONTEXT_COMPRESSION_ENABLED,
        min_tokens: int = CONTEXT_COMPRESSION_MIN_TOKENS,
        target_ratio: float = CONTEXT_COMPRESSION_TARGET_RATIO,
        compress_history: bool = CONTEXT_COMPRESSION_COMPRESS_HISTORY,
        compress_long_memory: bool = CONTEXT_COMPRESSION_COMPRESS_LONG_MEMORY,
        compress_chunks: bool = CONTEXT_COMPRESSION_COMPRESS_CHUNKS,
    ):
        self.enabled = enabled
        self.min_tokens = max(1, int(min_tokens))
        self.target_ratio = min(1.0, max(0.05, float(target_ratio)))
        self.compress_history = compress_history
        self.compress_long_memory = compress_long_memory
        self.compress_chunks = compress_chunks
        self._crusher = None

    @classmethod
    def from_config(cls) -> "ContextCompressor":
        return cls()

    def new_report(self) -> dict:
        return {
            "enabled": self.enabled,
            "attempted": 0,
            "compressed": 0,
            "tokensBefore": 0,
            "tokensAfter": 0,
            "tokensSaved": 0,
            "errors": [],
        }

    def compress_text(self, text: str, query: str, section: str, report: dict | None = None) -> str:
        if not self.enabled or not text or self._estimate_tokens(text) < self.min_tokens:
            return text

        if report is not None:
            report["attempted"] += 1

        try:
            result = self._get_crusher().compress(
                text,
                context=query,
                target_ratio=self.target_ratio,
            )
        except Exception as exc:
            if report is not None:
                report["errors"].append({"section": section, "error": type(exc).__name__})
            return text

        before = int(getattr(result, "original_tokens", 0) or 0)
        after = int(getattr(result, "compressed_tokens", 0) or 0)
        compressed = (getattr(result, "compressed", "") or "").strip()

        if report is not None:
            report["tokensBefore"] += before
            report["tokensAfter"] += after or before

        if not compressed or not before or not after or after >= before:
            return text

        if report is not None:
            report["compressed"] += 1
            report["tokensSaved"] += before - after
        return compressed

    def _get_crusher(self):
        if self._crusher is None:
            from headroom.transforms.text_crusher import TextCrusher, TextCrusherConfig

            self._crusher = TextCrusher(TextCrusherConfig(target_ratio=self.target_ratio))
        return self._crusher

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text.split()))
