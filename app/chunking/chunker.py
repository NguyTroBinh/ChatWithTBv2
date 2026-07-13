import hashlib
import re 
from typing import List, Dict

# CONFIG TOKEN BASED
MIN_TOKENS = 200       
IDEAL_TOKENS = 700     
MAX_TOKENS = 1200      
HARD_CAP = 1500  
TOKEN_COUNT_WINDOW_CHARS = 5000 

class ChunkingService:
    def __init__(self):
        from app.providers.embedding import EmbeddingService
        from langchain_experimental.text_splitter import SemanticChunker
        from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
        from transformers import AutoTokenizer

        # Load model embedding
        self.embeddings = EmbeddingService()

        # Load tokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(self.embeddings.get_model_name())

        # Markdown Splitter
        self.header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[
                ("#", "H1"),
                ("##", "H2"),
                ("###", "H3"),
            ],
            strip_headers=False
        )

        # Semantic Splitter
        self.semantic_splitter = SemanticChunker(
            self.embeddings,
            breakpoint_threshold_type="gradient"
        )

        # Structure Splitter
        self.structure_splitter = RecursiveCharacterTextSplitter(
            chunk_size=2000, 
            chunk_overlap=0,
            separators=[
                "\n\n", "\n", 
                "\n1. ", "\n2. ", "\n3. ", "\n4. ", "\n5. ", 
                "\na) ", "\nb) ", "\nc) ", "\nd) ",
                ". ",
            ]
        )

        # Fallback Splitter
        self.fallback_splitter = RecursiveCharacterTextSplitter(
            chunk_size=HARD_CAP,
            chunk_overlap=100,
            length_function=self._count_tokens,
        )

    def _count_tokens(self, text: str) -> int:
        if not text:
            return 0
        
        return sum(
            len(
                self.tokenizer.encode(
                    text[start:start + TOKEN_COUNT_WINDOW_CHARS],
                    add_special_tokens=False,
                )
            )
            for start in range(0, len(text), TOKEN_COUNT_WINDOW_CHARS)
        )

    def _preprocess_tables(self, text: str) -> str:
        """
        Tìm bảng Markdown và cô lập bằng \n\n để tránh bị cắt đôi.
        """
        table_block_pattern = r'((?:^\|.*\|$\n?)+)'
        
        def isolate_table(match):
            table_content = match.group(1).strip()
            
            return f"\n\n{table_content}\n\n"

        return re.sub(table_block_pattern, isolate_table, text, flags=re.MULTILINE)
    
    def _smart_merge_sections(self, splits):
        """
        Logic Merge:
        - Ưu tiên 1: Nếu < MIN_TOKENS -> BẮT BUỘC GỘP.
        - Ưu tiên 2: Nếu < IDEAL_TOKENS -> Gộp để tối ưu context.
        """
        if not splits: return []
        
        merged = []
        current_doc = splits[0]
        
        for next_doc in splits[1:]:
            curr_tokens = self._count_tokens(current_doc.page_content)
            next_tokens = self._count_tokens(next_doc.page_content)
            
            # Điều kiện kiểm tra Topic (H1): Không gộp các section khác header (H1) để tránh hallucination
            curr_h1 = current_doc.metadata.get("H1")
            next_h1 = next_doc.metadata.get("H1")
            curr_h2 = current_doc.metadata.get("H2")
            next_h2 = next_doc.metadata.get("H2")
            same_topic = (curr_h1 == next_h1 and curr_h2 == next_h2)

            # Điều kiện an toàn kích thước
            total_size = curr_tokens + next_tokens
            is_safe_size = total_size < HARD_CAP

            should_merge = False

            if same_topic and is_safe_size:
                # 1. Ép buộc merge nếu đang ít token (< 200)
                if curr_tokens < MIN_TOKENS:
                    should_merge = True
                # 2. Merge tự nguyện nếu tổng kích thước vẫn đẹp (< 700)
                elif total_size < IDEAL_TOKENS:
                    should_merge = True

            if should_merge:
                current_doc.page_content += "\n\n" + next_doc.page_content
            else:
                merged.append(current_doc)
                current_doc = next_doc
        
        merged.append(current_doc)
        return merged

    def _apply_semantic_split(self, chunks_list, content, headers, file_name):
        try:
            sub_docs = self.semantic_splitter.create_documents([content])
            
            for sub in sub_docs:
                t_count = self._count_tokens(sub.page_content)
                
                if t_count > HARD_CAP:
                    hard_splits = self.fallback_splitter.split_text(sub.page_content)
                    for hard_txt in hard_splits:
                        self._add_chunk(chunks_list, hard_txt, headers, file_name)
                else:
                    self._add_chunk(chunks_list, sub.page_content, headers, file_name)
                    
        except Exception:
            hard_splits = self.fallback_splitter.split_text(content)
            for hard_txt in hard_splits:
                self._add_chunk(chunks_list, hard_txt, headers, file_name)

    def process_hybrid_splitting(self, text: str, file_name: str) -> List[Dict]:
        """
        1. Pre-process Tables -> 2. MD Split -> 3. Structure Split -> 4. Smart Merge -> 5. Semantic/Hard Cap
        """
        # 1: Processing Tables
        text_safe_tables = self._preprocess_tables(text)

        # 2: Markdown Split
        raw_splits = self.header_splitter.split_text(text_safe_tables)
        
        # 3: Structure Split
        refined_splits = []
        for doc in raw_splits:
            if self._count_tokens(doc.page_content) > MAX_TOKENS:
                sub_docs = self.structure_splitter.split_documents([doc])
                refined_splits.extend(sub_docs)
            else:
                refined_splits.append(doc)

        # 4: Smart Merge 
        merged_splits = self._smart_merge_sections(refined_splits)
        
        final_chunks = []

        # 5: Final Processing
        for doc in merged_splits:
            content = doc.page_content
            headers = doc.metadata
            token_count = self._count_tokens(content)

            # Case A: Chunk > MAX_TOKENS -> Semantic Split
            if token_count > MAX_TOKENS:
                self._apply_semantic_split(final_chunks, content, headers, file_name)
            
            # Case B: Chunk <= MAX_TOKENS -> Save
            else:
                self._add_chunk(final_chunks, content, headers, file_name)

        return self._finalize_chunks(final_chunks)

    def _add_chunk(self, chunks_list, content, headers, file_name):
        enriched_content = self._inject_header_context(content, headers)
        page_start, page_end = self._extract_page_range(headers, enriched_content)
        enriched_content = self._strip_page_markers(enriched_content)
        token_count = self._count_tokens(enriched_content)
        chunk_hash = self._hash_text(enriched_content)
        document_id = self._document_id(file_name)
        
        flat_metadata = {
            "chunk_id": "",
            "document_id": document_id,
            "position": -1,
            "token_count": token_count,
            "page_start": page_start,
            "page_end": page_end,
            "chunk_hash": chunk_hash,
            "section_path": self._section_path(headers),
            "prev_chunk_id": None,
            "next_chunk_id": None,
        }

        chunks_list.append({
            "id": "",
            "file_name": file_name,
            "content": enriched_content,
            "metadata": flat_metadata
        })

    def _finalize_chunks(self, chunks: List[Dict]) -> List[Dict]:
        for index, chunk in enumerate(chunks):
            metadata = chunk["metadata"]
            chunk_id = self._chunk_id(metadata["document_id"], index, metadata["chunk_hash"])
            metadata["chunk_id"] = chunk_id
            metadata["position"] = index
            chunk["id"] = chunk_id

        for index, chunk in enumerate(chunks):
            metadata = chunk["metadata"]
            metadata["prev_chunk_id"] = chunks[index - 1]["id"] if index > 0 else None
            metadata["next_chunk_id"] = chunks[index + 1]["id"] if index + 1 < len(chunks) else None

        return chunks

    def _inject_header_context(self, content: str, metadata: Dict) -> str:
        headers = [
            metadata[header]
            for header in ["H1", "H2", "H3"]
            if header in metadata and not self._is_page_header(metadata[header])
        ]
        if not headers: return content
        context_str = " > ".join(headers)
        if context_str not in content[:300]: 
            return f"**Bối cảnh: {context_str}**\n\n{content}"
        return content

    @staticmethod
    def _extract_page_range(headers: Dict, content: str) -> tuple[int | None, int | None]:
        pages = []
        for value in headers.values():
            match = re.fullmatch(r"(?:Page|Trang)\s+(\d+)", str(value).strip(), flags=re.IGNORECASE)
            if match:
                pages.append(int(match.group(1)))

        for match in re.finditer(r"^#{1,6}\s*(?:Page|Trang)\s+(\d+)\s*$", content, flags=re.MULTILINE | re.IGNORECASE):
            pages.append(int(match.group(1)))

        if not pages:
            return None, None
        return min(pages), max(pages)

    @staticmethod
    def _section_path(headers: Dict) -> str:
        return " > ".join(
            str(headers[key]).strip()
            for key in ["H1", "H2", "H3"]
            if headers.get(key) and not ChunkingService._is_page_header(headers[key])
        )

    @staticmethod
    def _is_page_header(value: object) -> bool:
        return bool(re.fullmatch(r"(?:Page|Trang)\s+\d+", str(value).strip(), flags=re.IGNORECASE))

    @staticmethod
    def _strip_page_markers(content: str) -> str:
        return re.sub(
            r"^#{1,6}\s*(?:Page|Trang)\s+\d+\s*\n?",
            "",
            content,
            flags=re.MULTILINE | re.IGNORECASE,
        ).strip()

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def _document_id(cls, document_key: str) -> str:
        return cls._hash_text(document_key)[:16]

    @classmethod
    def _chunk_id(cls, document_id: str, position: int, chunk_hash: str) -> str:
        return cls._hash_text(f"{document_id}:{position}:{chunk_hash}")[:24]
