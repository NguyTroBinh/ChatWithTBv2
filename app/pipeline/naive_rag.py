from pathlib import Path

from app.chunking.chunker import ChunkingService
from app.graph.neo4j_store import Neo4jGraphStore
from app.ingestion.pdf_loader import PDFLoader
from app.providers.reranking import RerankerService
from app.reasoning.answer_generator import AnswerGenerator
from app.retrieval.naive import NaiveRetrievalService

class NaiveRAGPipeline:
    def __init__(
        self,
        graph_store: Neo4jGraphStore,
        pdf_loader: PDFLoader,
        chunker: ChunkingService,
        retriever: NaiveRetrievalService,
        reranker: RerankerService,
        answer_generator: AnswerGenerator,
        raw_dir: str | Path = "data/raw/pdf",
        processed_dir: str | Path = "data/processed",
    ):
        self.graph_store = graph_store
        self.pdf_loader = pdf_loader
        self.chunker = chunker
        self.embedding_service = chunker.embeddings
        self.retriever = retriever
        self.reranker = reranker
        self.answer_generator = answer_generator
        self.raw_dir = Path(raw_dir)
        self.processed_dir = Path(processed_dir)

    @classmethod
    def from_config(cls) -> "NaiveRAGPipeline":
        graph_store = Neo4jGraphStore.from_config()
        chunker = ChunkingService()
        graph_store.ensure_schema()
        return cls(
            graph_store=graph_store,
            pdf_loader=PDFLoader(),
            chunker=chunker,
            retriever=NaiveRetrievalService(
                graph_store=graph_store,
                embedding_service=chunker.embeddings,
            ),
            reranker=RerankerService(),
            answer_generator=AnswerGenerator.from_config(),
        )

    def ingest_pdf(self, pdf_path: str | Path) -> dict:
        pdf_path = Path(pdf_path)
        markdown = self.pdf_loader.processing(str(pdf_path))
        chunks = self.chunker.process_hybrid_splitting(markdown, file_name=pdf_path.name)
        embeddings = self.embedding_service.embed_documents([chunk["content"] for chunk in chunks])

        self.graph_store.ensure_schema()
        result = self.graph_store.save_document_chunks(
            file_name=pdf_path.name,
            chunks=chunks,
            embeddings=embeddings,
            document_type="pdf",
        )
        return {
            **result,
            "file_name": pdf_path.name,
            "raw_path": str(pdf_path),
            "processed_path": str(self.processed_dir / f"{pdf_path.stem}.md"),
        }

    def chat(self, query: str, top_k: int = 5, document_ids: list[str] | None = None) -> dict:
        chunks = self.retriever.retrieve(query, top_k=10, document_ids=document_ids)
        chunks = self.reranker.rerank(query, chunks, top_k=top_k)
        result = self.answer_generator.generate(query, chunks)
        result["mode"] = "naive"
        return result

    def close(self) -> None:
        self.graph_store.close()
