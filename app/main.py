from pathlib import Path
import re
import shutil
import uuid
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.graph.neo4j_store import Neo4jGraphStore
from app.memory.longterm.core import MemoryCore
from app.memory.longterm.neo4j_memory_store import Neo4jMemoryStore
from app.memory.shortterm import ShortTermMemoryService
from app.pipeline.ingest import IngestPipeline
from app.pipeline.naive_chat import NaiveChatPipeline
from app.pipeline.local_chat import LocalChatPipeline
from app.reasoning.deepsearch import DeepSearchPipeline


ROOT_DIR = Path(__file__).resolve().parents[1]
RAW_PDF_DIR = ROOT_DIR / "data" / "raw" / "pdf"
WEB_DIR = ROOT_DIR / "web"

app = FastAPI(title="Chat With TB")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

_ingest_pipeline: IngestPipeline | None = None
_naive_pipeline: NaiveChatPipeline | None = None
_local_pipeline: LocalChatPipeline | None = None
_deep_pipeline: DeepSearchPipeline | None = None
_document_store: Neo4jGraphStore | None = None
_short_memory: ShortTermMemoryService | None = None
_long_memory: MemoryCore | None = None


class ChatRequest(BaseModel):
    query: str
    session_id: str = Field(..., min_length=1, max_length=128)
    top_k: int = 5
    document_ids: list[str] = Field(default_factory=list)
    mode: Literal["fast", "balanced", "deep"] = "fast"


class ConversationCreateRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=160)
    document_ids: list[str] = Field(default_factory=list)


class ConversationUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    document_ids: list[str] | None = None


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


def get_deep_pipeline() -> DeepSearchPipeline:
    global _deep_pipeline
    if _deep_pipeline is None:
        _deep_pipeline = DeepSearchPipeline.from_config()
    return _deep_pipeline


def get_document_store() -> Neo4jGraphStore:
    global _document_store
    if _document_store is None:
        _document_store = Neo4jGraphStore.from_config()
    return _document_store


def get_short_memory() -> ShortTermMemoryService:
    global _short_memory
    if _short_memory is None:
        _short_memory = ShortTermMemoryService()
    return _short_memory


def get_long_memory() -> MemoryCore:
    global _long_memory
    if _long_memory is None:
        _long_memory = MemoryCore.from_config()
        _long_memory.initialize()
    return _long_memory


def clean_session_id(session_id: str) -> str:
    value = session_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise HTTPException(status_code=400, detail="session_id is invalid.")
    return value


def clean_document_ids(document_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(did.strip() for did in document_ids if did.strip()))


def clean_conversation_title(title: str | None, session_id: str) -> str:
    value = " ".join((title or "").strip().split())
    return value[:120] or f"TB {session_id}"


def conversation_response(conversation: dict, messages: list[dict] | None = None) -> dict:
    document_ids = clean_document_ids(conversation.get("documentIds") or [])
    documents = get_document_store().get_documents_by_ids(document_ids) if document_ids else []
    return {
        "conversation": conversation,
        "messages": messages if messages is not None else get_short_memory().get_history(conversation["sessionId"]),
        "documents": documents,
    }


def delete_long_memory_session(session_id: str) -> dict:
    if _long_memory is not None:
        return _long_memory.delete_session(session_id)
    store = Neo4jMemoryStore.from_config()
    try:
        return store.delete_session(session_id)
    finally:
        store.close()


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "app": "Chat With TB"}


@app.get("/api/documents")
def list_documents(
    q: str = "",
    limit: int = Query(default=50, ge=1, le=100),
):
    try:
        documents = get_document_store().list_documents(search=q, limit=limit)
        return {"documents": documents}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/conversations")
def list_conversations(limit: int = Query(default=50, ge=1, le=100)):
    try:
        return {"conversations": get_short_memory().list_conversations(limit=limit)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/conversations")
def create_conversation(request: ConversationCreateRequest):
    session_id = clean_session_id(request.session_id or str(uuid.uuid4()))
    title = clean_conversation_title(request.title, session_id)
    try:
        conversation = get_short_memory().save_conversation(
            session_id,
            title=title,
            document_ids=clean_document_ids(request.document_ids),
        )
        return conversation_response(conversation, messages=[])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/conversations/{session_id}")
def get_conversation(session_id: str):
    session_id = clean_session_id(session_id)
    try:
        conversation = get_short_memory().get_conversation(session_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return conversation_response(conversation)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/conversations/{session_id}/open")
def open_conversation(session_id: str):
    session_id = clean_session_id(session_id)
    try:
        short_memory = get_short_memory()
        conversation = short_memory.get_conversation(session_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        conversation = short_memory.touch_conversation(session_id)
        return conversation_response(conversation)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.patch("/api/conversations/{session_id}")
def update_conversation(session_id: str, request: ConversationUpdateRequest):
    session_id = clean_session_id(session_id)
    try:
        short_memory = get_short_memory()
        conversation = short_memory.get_conversation(session_id)
        if not conversation:
            conversation = short_memory.save_conversation(session_id)
        if request.title is not None:
            conversation = short_memory.rename_conversation(
                session_id,
                clean_conversation_title(request.title, session_id),
            )
        if request.document_ids is not None:
            conversation = short_memory.update_conversation_documents(
                session_id,
                clean_document_ids(request.document_ids),
            )
        return conversation_response(conversation)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.delete("/api/conversations/{session_id}")
def delete_conversation(session_id: str):
    session_id = clean_session_id(session_id)
    try:
        long_term_deleted = delete_long_memory_session(session_id)
        get_short_memory().delete_conversation(session_id)
        return {"deleted": True, "sessionId": session_id, "longTerm": long_term_deleted}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
    session_id = clean_session_id(request.session_id)

    if request.mode == "deep":
        pipeline = get_deep_pipeline()
    elif request.mode == "balanced":
        pipeline = get_local_pipeline()
    else:
        pipeline = get_naive_pipeline()

    memory_context = None
    try:
        memory_context = {
            "conversation_history": get_short_memory().get_history(session_id),
            "long_term_memories": get_long_memory().recall(request.query, session_id=session_id),
        }
    except Exception:
        # ponytail: memory is supporting context; degrade to no-memory chat instead of failing.
        memory_context = None

    try:
        result = pipeline.chat(
            request.query,
            top_k=request.top_k,
            document_ids=document_ids,
            memory_context=memory_context,
        )
        result["sessionId"] = session_id
        try:
            short_memory = get_short_memory()
            result["conversation"] = short_memory.touch_conversation(session_id, document_ids=document_ids)
            short_memory.add_msg(session_id, "user", request.query)
            short_memory.add_msg(session_id, "assistant", result.get("answer", ""))
        except Exception:
            # ponytail: best-effort save; answer should not fail because Redis history is unavailable.
            result["warnings"] = [*(result.get("warnings") or []), "short_memory_save_unavailable"]
        try:
            get_long_memory().add_exchange(request.query, result.get("answer", ""), session_id=session_id)
        except Exception:
            # ponytail: best-effort save; recall/prompt already served, do not fail the turn.
            result["warnings"] = [*(result.get("warnings") or []), "long_memory_save_unavailable"]
        return result
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
    if _deep_pipeline is not None:
        _deep_pipeline.close()
    if _document_store is not None:
        _document_store.close()
    if _long_memory is not None:
        _long_memory.close()
