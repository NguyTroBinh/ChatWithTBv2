# ChatWithTB v2

An upgrade of [ChatWithTB](https://github.com/NguyTroBinh/ChatWithTB) — a RAG chatbot system for PDF documents. Version 2 focuses on **optimizing retrieval** by combining multiple techniques: hybrid chunking, knowledge graph, retrieval (naive + local + global + deep search), and mandatory citation in every answer.

![Chat UI](docs/chat-ui.png)

## Project Structure

```
app/
├── main.py                  # FastAPI app, API endpoints (/api/upload, /api/chat)
├── core/
│   └── config.py            # Environment variables, model/Neo4j/LLM configuration
├── ingestion/
│   └── pdf_loader.py        # PDF → Markdown
├── chunking/
│   └── chunker.py           # Hybrid chunking pipeline (5 steps)
├── providers/
│   ├── embedding.py         # Embedding service (Vietnamese_Embedding)
│   ├── reranking.py         # Cross-encoder reranker (Vietnamese_Reranker)
│   └── litellm_client.py    # LLM adapter via LiteLLM (switch provider)
├── extraction/
│   ├── entity_extractor.py  # LLM-based entity & relationship extraction
│   └── community_builder.py # Graph community detection + summary
├── graph/
│   └── neo4j_store.py       # Neo4j driver, schema, vector/fulltext index
├── retrieval/
│   ├── naive.py             # Vector + Fulltext search, RRF merge
│   └── local.py             # Graph-aware retrieval (entity → chunk → context)
├── reasoning/
│   └── answer_generator.py  # LLM generates answers with citation
├── pipeline/
│   ├── ingest.py            # Ingest pipeline: PDF → chunk → embed → Neo4j → graph
│   ├── naive_chat.py        # Retrieve → Rerank → Generate (fast mode)
│   ├── local_chat.py        # Graph-aware retrieve → Rerank → Generate (balanced mode)
│   └── graph_builder.py     # Entity extraction + community building
web/                         # Frontend chat UI (HTML/CSS/JS)
prompts/                     # Vietnamese prompt templates (answer, entity, community)
data/
├── raw/pdf/                 # Original PDFs
└── processed/               # Converted Markdown
```

## Techniques

### Hybrid Chunking (5 steps)

1. **Table isolation** — Detect Markdown tables, isolate with `\n\n` to prevent splitting mid-table.
2. **Markdown header split** — Split by heading structure (H1/H2/H3), preserve section metadata.
3. **Structure split** — Chunks > 1200 tokens are further split by natural separators (paragraph, numbered list).
4. **Smart merge** — Chunks < 200 tokens are force-merged; chunks < 700 tokens are merged if same topic.
5. **Semantic split** — Remaining large chunks are split by SemanticChunker (gradient breakpoint) based on embedding similarity.

### Knowledge Graph (Neo4j)

- **Entity & Relationship extraction** — LLM extracts entities (name, type, description) and relationships from each chunk.
- **Community detection** — Weakly Connected Components on the graph, LLM generates a summary for each community.
- **Graph schema**: `Document → Chunk → Entity → Community`, with vector + fulltext indexes on all 3 node types.

### Retrieval

#### Naive Search — direct, simple questions

> *"What is the penalty for forestry violations?"*

```
Query → Embed → Vector search (Chunk)
                 Fulltext search (Chunk)
                   ↓
                 RRF merge → Rerank → Generate + Citation
```

Finds chunks directly via semantic + lexical search, merges with Reciprocal Rank Fusion, reranks with cross-encoder.

#### Local Search — questions about specific entities, clauses, or concepts

> *"What does Decree 156/2018 stipulate about forest fire forecast classification?"*

```
Query → Extract keywords/entities
           ↓
         Entity search (vector + fulltext) → RRF merge
           ↓
         Chunk mentions entity → Neighbor chunks (prev/next)
           ↓
         Community context (if available)
           ↓
         Rerank → Generate + Citation
```

Starts from entities in the knowledge graph → traces back to source chunks containing that entity → expands context with neighboring chunks.

#### Global Search — overview, summary questions *(planned)*

> *"Summarize the project's implementation phases?"*

```
Query → Extract high-level intent
           ↓
         Search Community summaries
           ↓
         Entities in community → Representative chunks
           ↓
         Generate synthesis + Citation
```

Uses community summaries for orientation, but citations still point back to source chunks/documents.

#### Deep Search — complex, multi-part, comparative questions *(planned)*

> *"Compare the technical requirements between the fire detection module and the deforestation detection module?"*

```
Query → Planner decomposes into sub-questions
           ↓
         Each sub-question → select mode (naive/local/global)
           ↓
         Retrieve evidence → Critic checks sufficiency
           ↓
         Additional rounds if insufficient (max 2-3 rounds)
           ↓
         Synthesizer aggregates → Citation validator
```

Multi-round reasoning: decompose question → retrieve across multiple rounds → verify evidence before synthesizing.

---

### Provider-switchable LLM

Uses LiteLLM as adapter — supports Ollama, OpenAI, NVIDIA NIM, Gemini, vLLM, ...

## Installation and Usage

### Requirements

- Python 3.12+
- Docker (for Neo4j)

### Setup

```bash
git clone https://github.com/NguyTroBinh/ChatWithTBv2.git
cd ChatWithTBv2

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit LLM_API_KEY and other config in .env
```

### Start Neo4j

```bash
docker compose up -d
```

### Run server

```bash
uvicorn app.main:app --reload
```

Open browser at `http://localhost:8000` to use the chat UI.

## Future Plans

- **Memory** — Integrate conversation memory from ChatWithTB v1, enabling multi-turn Q&A with context.
- **Cache** — Cache embeddings, retrieval results, and answers by version to avoid recomputation.
- **Deep Search** — Multi-round reasoning: decompose complex questions into sub-questions, retrieve across multiple rounds, critic verifies evidence sufficiency before synthesizing.
