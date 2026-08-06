import os
from dotenv import load_dotenv

load_dotenv()

def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

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
CHUNK_FULLTEXT_INDEX = os.getenv("CHUNK_FULLTEXT_INDEX", "chunk_text")
ENTITY_VECTOR_INDEX = os.getenv("ENTITY_VECTOR_INDEX", "entity_embedding")
ENTITY_FULLTEXT_INDEX = os.getenv("ENTITY_FULLTEXT_INDEX", "entity_text")
COMMUNITY_VECTOR_INDEX = os.getenv("COMMUNITY_VECTOR_INDEX", "community_embedding")
COMMUNITY_FULLTEXT_INDEX = os.getenv("COMMUNITY_FULLTEXT_INDEX", "community_text")

# RERANKER_MODEL
RERANKER_MODEL_NAME = os.getenv("RERANKER_MODEL_NAME", "AITeamVN/Vietnamese_Reranker")

# LLM
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
LLM_API_BASE = os.getenv("LLM_API_BASE", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "1"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))
LLM_THINK = _env_bool("LLM_THINK")
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "").strip().lower()

# Context compression
CONTEXT_COMPRESSION_ENABLED = _env_bool("CONTEXT_COMPRESSION_ENABLED")
CONTEXT_COMPRESSION_MIN_TOKENS = int(os.getenv("CONTEXT_COMPRESSION_MIN_TOKENS", "800"))
CONTEXT_COMPRESSION_TARGET_RATIO = float(os.getenv("CONTEXT_COMPRESSION_TARGET_RATIO", "0.65"))
CONTEXT_COMPRESSION_COMPRESS_HISTORY = _env_bool("CONTEXT_COMPRESSION_COMPRESS_HISTORY", "true")
CONTEXT_COMPRESSION_COMPRESS_LONG_MEMORY = _env_bool("CONTEXT_COMPRESSION_COMPRESS_LONG_MEMORY", "true")
CONTEXT_COMPRESSION_COMPRESS_CHUNKS = _env_bool("CONTEXT_COMPRESSION_COMPRESS_CHUNKS", "true")
