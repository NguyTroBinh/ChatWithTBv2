from pathlib import Path
import shutil

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.pipeline.naive_rag import NaiveRAGPipeline


ROOT_DIR = Path(__file__).resolve().parents[1]
RAW_PDF_DIR = ROOT_DIR / "data" / "raw" / "pdf"
WEB_DIR = ROOT_DIR / "web"

app = FastAPI(title="Chat With TB")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

_pipeline: NaiveRAGPipeline | None = None


class ChatRequest(BaseModel):
    query: str
    top_k: int = 5
    document_ids: list[str] = Field(default_factory=list)


def get_pipeline() -> NaiveRAGPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = NaiveRAGPipeline.from_config()
    return _pipeline


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "app": "Chat With TB"}


@app.post("/api/upload")
def upload_pdf(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No PDF files uploaded.")

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

            documents.append(get_pipeline().ingest_pdf(raw_path))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            file.file.close()

    return {"documents": documents}


@app.post("/api/chat")
def chat(request: ChatRequest):
    document_ids = [document_id.strip() for document_id in request.document_ids if document_id.strip()]
    if not document_ids:
        raise HTTPException(status_code=400, detail="Upload at least one PDF before asking.")

    try:
        return get_pipeline().chat(request.query, top_k=request.top_k, document_ids=document_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.on_event("shutdown")
def shutdown():
    if _pipeline is not None:
        _pipeline.close()
