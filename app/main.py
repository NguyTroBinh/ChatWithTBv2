from pathlib import Path
import shutil
from typing import Literal

# ponytail: deep mode routes to LocalChatPipeline for now; upgrade to DeepSearchPipeline when ready

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.pipeline.ingest import IngestPipeline
from app.pipeline.naive_chat import NaiveChatPipeline
from app.pipeline.local_chat import LocalChatPipeline


ROOT_DIR = Path(__file__).resolve().parents[1]
RAW_PDF_DIR = ROOT_DIR / "data" / "raw" / "pdf"
WEB_DIR = ROOT_DIR / "web"

app = FastAPI(title="Chat With TB")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

_ingest_pipeline: IngestPipeline | None = None
_naive_pipeline: NaiveChatPipeline | None = None
_local_pipeline: LocalChatPipeline | None = None


class ChatRequest(BaseModel):
    query: str
    top_k: int = 5
    document_ids: list[str] = Field(default_factory=list)
    mode: Literal["fast", "balanced", "deep"] = "fast"


def get_ingest_pipeline() -> IngestPipeline:
    global _ingest_pipeline
    if _ingest_pipeline is None:
        _ingest_pipeline = IngestPipeline.from_config()
    return _ingest_pipeline


def get_naive_pipeline() -> NaiveChatPipeline:
    global _naive_pipeline
    if _naive_pipeline is None:
        _naive_pipeline = NaiveChatPipeline.from_config()
    return _naive_pipeline


def get_local_pipeline() -> LocalChatPipeline:
    global _local_pipeline
    if _local_pipeline is None:
        _local_pipeline = LocalChatPipeline.from_config()
    return _local_pipeline


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "app": "Chat With TB"}


@app.post("/api/upload")
def upload_pdf(
    files: list[UploadFile] = File(...),
):
    if not files:
        raise HTTPException(status_code=400, detail="No PDF files uploaded.")

    pipeline = get_ingest_pipeline()

    RAW_PDF_DIR.mkdir(parents=True, exist_ok=True)
    documents = []
    for file in files:
        try:
            file_name = Path(file.filename or "").name
            if not file_name or Path(file_name).suffix.lower() != ".pdf":
                raise HTTPException(status_code=400, detail="Only PDF files are supported.")

            raw_path = RAW_PDF_DIR / file_name
            with raw_path.open("wb") as output:
                shutil.copyfileobj(file.file, output)

            documents.append(pipeline.ingest_pdf(raw_path))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            file.file.close()

    return {"documents": documents}


@app.post("/api/chat")
def chat(request: ChatRequest):
    document_ids = [did.strip() for did in request.document_ids if did.strip()]
    if not document_ids:
        raise HTTPException(status_code=400, detail="Upload at least one PDF before asking.")

    pipeline = get_local_pipeline() if request.mode == "balanced" else get_naive_pipeline()

    try:
        return pipeline.chat(request.query, top_k=request.top_k, document_ids=document_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.on_event("shutdown")
def shutdown():
    if _ingest_pipeline is not None:
        _ingest_pipeline.close()
    if _naive_pipeline is not None:
        _naive_pipeline.close()
    if _local_pipeline is not None:
        _local_pipeline.close()
