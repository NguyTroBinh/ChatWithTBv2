from app.core.config import BATCH_SIZE, EMBEDDING_MODEL_NAME, MODEL_CACHE_DIR
from threading import Lock

_embedding_service = None
_embedding_lock = Lock()

class EmbeddingService:
    def __init__(self, model_name=EMBEDDING_MODEL_NAME, cache_folder=MODEL_CACHE_DIR):
        import torch
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = SentenceTransformer(
            self.model_name, 
            device=self.device,
            cache_folder=cache_folder
        )

    # Get model name
    def get_model_name(self):
        return self.model_name

    # Get model
    def get_model(self):
        return self.model

    # Processing query input
    def embed_query(self, query: str):
        embedding = self.model.encode(query, normalize_embeddings=True)

        return embedding.tolist()

    # Processing docs
    def embed_documents(self, texts: list[str]): 
        embeddings = self.model.encode(
            texts,
            batch_size=BATCH_SIZE,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False
        )

        return embeddings.tolist()


def get_embedding_service() -> EmbeddingService:
    global _embedding_service
    if _embedding_service is None:
        with _embedding_lock:
            if _embedding_service is None:
                _embedding_service = EmbeddingService()
    return _embedding_service
