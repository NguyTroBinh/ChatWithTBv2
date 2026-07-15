from pathlib import Path

from app.chunking.chunker import ChunkingService
from app.graph.neo4j_store import Neo4jGraphStore
from app.ingestion.pdf_loader import PDFLoader
from app.pipeline.graph_builder import GraphBuilder


class IngestPipeline:
    def __init__(
        self,
        graph_store: Neo4jGraphStore,
        pdf_loader: PDFLoader,
        chunker: ChunkingService,
        graph_builder: GraphBuilder,
        processed_dir: str | Path = "data/processed",
    ):
        self.graph_store = graph_store
        self.pdf_loader = pdf_loader
        self.chunker = chunker
        self.embedding_service = chunker.embeddings
        self.graph_builder = graph_builder
        self.processed_dir = Path(processed_dir)

    @classmethod
    def from_config(cls) -> "IngestPipeline":
        from app.extraction.community_builder import CommunityBuilder
        from app.extraction.entity_extractor import EntityExtractor
        from app.providers.litellm_client import LiteLLMClient

        graph_store = Neo4jGraphStore.from_config()
        chunker = ChunkingService()
        embedding_service = chunker.embeddings
        llm_client = LiteLLMClient()
        graph_store.ensure_schema()

        return cls(
            graph_store=graph_store,
            pdf_loader=PDFLoader(),
            chunker=chunker,
            graph_builder=GraphBuilder(
                graph_store=graph_store,
                entity_extractor=EntityExtractor(llm_client=llm_client),
                community_builder=CommunityBuilder(
                    graph_store=graph_store,
                    embedding_service=embedding_service,
                    llm_client=llm_client,
                ),
                embedding_service=embedding_service,
            ),
        )

    def ingest_pdf(self, pdf_path: str | Path) -> dict:
        pdf_path = Path(pdf_path)
        markdown = self.pdf_loader.processing(str(pdf_path))
        chunks = self.chunker.process_hybrid_splitting(markdown, file_name=pdf_path.name)
        embeddings = self.embedding_service.embed_documents([c["content"] for c in chunks])

        ingest_result = self.graph_store.save_document_chunks(
            file_name=pdf_path.name,
            chunks=chunks,
            embeddings=embeddings,
            document_type="pdf",
        )
        graph_result = self.graph_builder.build_from_chunks(
            chunks=chunks,
            document_id=ingest_result["document_id"],
        )

        return {
            **ingest_result,
            **graph_result,
            "file_name": pdf_path.name,
            "processed_path": str(self.processed_dir / f"{pdf_path.stem}.md"),
        }

    def close(self) -> None:
        self.graph_store.close()
