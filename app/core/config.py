import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# EMBEDDING_MODEL
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "AITeamVN/Vietnamese_Embedding")
MODEL_CACHE_DIR = os.getenv("MODEL_CACHE_DIR", "models_cache")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "32"))

# NEO4J
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
CHUNK_VECTOR_INDEX = os.getenv("CHUNK_VECTOR_INDEX", "chunk_embedding")

# RERANKER_MODEL
RERANKER_MODEL_NAME = os.getenv("RERANKER_MODEL_NAME", "AITeamVN/Vietnamese_Reranker")

# LLM
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
LLM_API_BASE = os.getenv("LLM_API_BASE", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "1"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))
LLM_THINK = os.getenv("LLM_THINK", "false").strip().lower() in {"1", "true", "yes", "on"}
