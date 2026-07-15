from app.graph.neo4j_store import Neo4jGraphStore
from app.providers.reranking import RerankerService
from app.reasoning.answer_generator import AnswerGenerator
from app.retrieval.naive import NaiveRetrievalService


class NaiveChatPipeline:
    def __init__(
        self,
        graph_store: Neo4jGraphStore,
        retriever: NaiveRetrievalService,
        reranker: RerankerService,
        answer_generator: AnswerGenerator,
    ):
        self.graph_store = graph_store
        self.retriever = retriever
        self.reranker = reranker
        self.answer_generator = answer_generator

    @classmethod
    def from_config(cls) -> "NaiveChatPipeline":
        from app.chunking.chunker import ChunkingService
        graph_store = Neo4jGraphStore.from_config()
        embedding_service = ChunkingService().embeddings
        return cls(
            graph_store=graph_store,
            retriever=NaiveRetrievalService(
                graph_store=graph_store,
                embedding_service=embedding_service,
            ),
            reranker=RerankerService(),
            answer_generator=AnswerGenerator.from_config(),
        )

    def chat(self, query: str, top_k: int = 5, document_ids: list[str] | None = None) -> dict:
        chunks = self.retriever.retrieve(query, top_k=top_k * 2, document_ids=document_ids)
        chunks = self.reranker.rerank(query, chunks, top_k=top_k)
        result = self.answer_generator.generate(query, chunks)
        result["mode"] = "naive"
        return result

    def close(self) -> None:
        self.graph_store.close()
