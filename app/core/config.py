import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# EMBEDDING_MODEL
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "AITeamVN/Vietnamese_Embedding")
MODEL_CACHE_DIR = os.getenv("MODEL_CACHE_DIR", "models_cache")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "32"))

