# ChatWithTB v2

Bản nâng cấp của [ChatWithTB](https://github.com/NguyTroBinh/ChatWithTB) — hệ thống RAG chatbot cho tài liệu PDF. Version 2 tập trung vào **tối ưu truy vấn** bằng cách kết hợp nhiều kỹ thuật: hybrid chunking, knowledge graph, retrieval (naive + local + global + deep search), và citation bắt buộc trong mọi câu trả lời.

![Chat UI](docs/chat-ui.png)

## Cấu trúc dự án

```
app/
├── main.py                  # FastAPI app, API endpoints (/api/upload, /api/chat)
├── core/
│   └── config.py            # Biến môi trường, cấu hình model/Neo4j/LLM
├── ingestion/
│   └── pdf_loader.py        # PDF → Markdown
├── chunking/
│   └── chunker.py           # Hybrid chunking pipeline (5 bước)
├── providers/
│   ├── embedding.py         # Embedding service (Vietnamese_Embedding)
│   ├── reranking.py         # Cross-encoder reranker (Vietnamese_Reranker)
│   └── litellm_client.py    # LLM adapter qua LiteLLM (switch provider)
├── extraction/
│   ├── entity_extractor.py  # LLM-based entity & relationship extraction
│   └── community_builder.py # Graph community detection + summary
├── graph/
│   └── neo4j_store.py       # Neo4j driver, schema, vector/fulltext index
├── retrieval/
│   ├── naive.py             # Vector + Fulltext search, RRF merge
│   └── local.py             # Graph-aware retrieval (entity → chunk → context)
├── reasoning/
│   └── answer_generator.py  # LLM sinh câu trả lời có citation
├── pipeline/
│   ├── ingest.py            # Pipeline ingest: PDF → chunk → embed → Neo4j → graph
│   ├── naive_chat.py        # Retrieve → Rerank → Generate (mode fast)
│   ├── local_chat.py        # Graph-aware retrieve → Rerank → Generate (mode balanced)
│   └── graph_builder.py     # Entity extraction + community building
web/                         # Frontend chat UI (HTML/CSS/JS)
prompts/                     # Prompt templates tiếng Việt (answer, entity, community)
data/
├── raw/pdf/                 # PDF gốc
└── processed/               # Markdown đã convert
```

## Kỹ thuật áp dụng

### Hybrid Chunking (5 bước)

1. **Table isolation** — Phát hiện bảng Markdown, cô lập bằng `\n\n` tránh bị cắt đôi.
2. **Markdown header split** — Tách theo cấu trúc heading (H1/H2/H3), giữ metadata section.
3. **Structure split** — Chunk lớn > 1200 tokens được chia tiếp theo separator tự nhiên (paragraph, numbered list).
4. **Smart merge** — Chunk nhỏ < 200 tokens bắt buộc gộp; chunk < 700 tokens gộp nếu cùng topic.
5. **Semantic split** — Chunk vẫn lớn được chia bằng SemanticChunker (gradient breakpoint) dựa trên embedding similarity.

### Knowledge Graph (Neo4j)

- **Entity & Relationship extraction** — LLM trích xuất entity (tên, loại, mô tả) và relationship từ mỗi chunk.
- **Community detection** — Weakly Connected Components trên graph, LLM tạo summary cho mỗi community.
- **Graph schema**: `Document → Chunk → Entity → Community`, với vector + fulltext index trên cả 3 node type.

### Retrieval

#### Naive Search — câu hỏi trực tiếp, đơn giản

> *"Mức phạt vi phạm lâm nghiệp là bao nhiêu?"*

```
Query → Embed → Vector search (Chunk)
                 Fulltext search (Chunk)
                   ↓
                 RRF merge → Rerank → Generate + Citation
```

Tìm chunk trực tiếp bằng semantic + lexical, merge bằng Reciprocal Rank Fusion, rerank bằng cross-encoder.

#### Local Search — câu hỏi về entity, điều khoản, khái niệm cụ thể

> *"Nghị định 156/2018 quy định gì về phân cấp dự báo cháy rừng?"*

```
Query → Extract keywords/entities
           ↓
         Entity search (vector + fulltext) → RRF merge
           ↓
         Chunk mentions entity → Neighbor chunks (prev/next)
           ↓
         Community context (nếu có)
           ↓
         Rerank → Generate + Citation
```

Đi từ entity trong knowledge graph → quay về chunk gốc chứa entity đó → mở rộng ngữ cảnh bằng chunk lân cận.

#### Global Search — câu hỏi tổng quan, tóm tắt *(planned)*

> *"Tóm tắt các giai đoạn triển khai của dự án?"*

```
Query → Extract high-level intent
           ↓
         Search Community summaries
           ↓
         Entity thuộc community → Chunk đại diện
           ↓
         Generate tổng hợp + Citation
```

Dùng community summary để định hướng, nhưng citation vẫn trỏ về chunk/document gốc.

#### Deep Search — câu hỏi phức tạp, nhiều ý, so sánh *(planned)*

> *"So sánh yêu cầu kỹ thuật giữa module phát hiện cháy và module phát hiện mất rừng?"*

```
Query → Planner tách sub-questions
           ↓
         Mỗi sub-question → chọn mode (naive/local/global)
           ↓
         Retrieve evidence → Critic kiểm tra đủ/thiếu
           ↓
         Lặp thêm nếu thiếu (max 2-3 vòng)
           ↓
         Synthesizer tổng hợp → Citation validator
```

Multi-round reasoning: tách câu hỏi → retrieve nhiều vòng → kiểm tra evidence trước khi tổng hợp.

---

### Provider-switchable LLM

Sử dụng LiteLLM làm adapter — hỗ trợ Ollama, OpenAI, NVIDIA NIM, Gemini, vLLM, ...

## Cài đặt và chạy

### Yêu cầu

- Python 3.12+
- Docker (cho Neo4j)

### Cài đặt

```bash
git clone https://github.com/NguyTroBinh/ChatWithTBv2.git
cd ChatWithTBv2

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Chỉnh LLM_API_KEY và các config trong .env
```

### Khởi động Neo4j

```bash
docker compose up -d
```

### Chạy server

```bash
uvicorn app.main:app --reload
```

Mở trình duyệt tại `http://localhost:8000` để sử dụng chat UI.

## Hướng phát triển

- **Memory** — Tích hợp conversation memory từ ChatWithTB v1, cho phép hỏi đáp nhiều lượt có ngữ cảnh.
- **Cache** — Cache embedding, retrieval result và answer theo version để tránh tính toán lại.
- **Deep Search** — Multi-round reasoning: tách câu hỏi phức tạp thành sub-questions, retrieve nhiều vòng, critic kiểm tra thiếu evidence trước khi tổng hợp.
