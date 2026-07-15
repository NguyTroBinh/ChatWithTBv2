from typing import List

from app.core.config import MODEL_CACHE_DIR, RERANKER_MODEL_NAME

class RerankerService:
    def __init__(self, model_name = RERANKER_MODEL_NAME, cache_folder = MODEL_CACHE_DIR):
        import torch
        from transformers import AutoModelForSequenceClassification, XLMRobertaTokenizer

        self.torch = torch
        self.device = "cpu"
        self.tokenizer = XLMRobertaTokenizer.from_pretrained(
            model_name,
            cache_dir=cache_folder
        )
        self.model = AutoModelForSequenceClassification.from_pretrained(
            model_name,
            cache_dir=cache_folder
        ).to(self.device)
        self.model.eval()

    def rerank(self, query: str, documents: List[dict], top_k: int = 5) -> List[dict]:
        if not documents:
            return []

        pairs = []
        valid_docs = []

        for doc in documents:
            content = doc.get("content")
            if content:
                pairs.append([query, content])
                valid_docs.append(doc)
        
        if not pairs:
            return []

        with self.torch.no_grad():
            inputs = self.tokenizer(pairs, padding=True, truncation=True, return_tensors='pt', max_length=2304).to(self.device)
            scores = self.model(**inputs, return_dict=True).logits.view(-1, ).float()

        scores = scores.tolist()

        results = []
        for doc, score in zip(valid_docs, scores):
            reranked_doc = dict(doc)
            reranked_doc["score"] = score
            results.append(reranked_doc)

        results.sort(key=lambda x: x["score"], reverse=True)

        return results[:top_k]
