import json
import time
from datetime import datetime, timezone

from app.memory.config import REDIS_DB, REDIS_HOST, REDIS_MAX_MESSAGES, REDIS_PASSWORD, REDIS_PORT, LIMIT_MESSAGES


class ShortTermMemoryService:
    def __init__(
        self,
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        max_message=REDIS_MAX_MESSAGES,
        limit_message=LIMIT_MESSAGES,
    ):
        try:
            import redis
        except ModuleNotFoundError as exc:
            raise RuntimeError("Missing dependency: install redis from requirements.txt.") from exc

        self.redis_client = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
        self.max_msg = max_message
        self.limit_msg = limit_message

    def _generate_key(self, session_id):
        return f"chat_history:{session_id}"

    def _conversation_key(self, session_id):
        return f"chat_conversation:{session_id}"

    def _conversation_index_key(self):
        return "chat_conversations"

    def add_msg(self, session_id, role, content):
        key = self._generate_key(session_id)
        msg = json.dumps({"role": role, "content": content}, ensure_ascii=False)
        self.redis_client.rpush(key, msg)
        if self.redis_client.llen(key) > self.max_msg:
            self.redis_client.ltrim(key, -self.max_msg, -1)

    def get_history(self, session_id, limit=None):
        limit = self.limit_msg if limit is None else int(limit)
        key = self._generate_key(session_id)
        raw_history = self.redis_client.lrange(key, -limit, -1)
        return [json.loads(msg) for msg in raw_history]

    def clear_history(self, session_id):
        key = self._generate_key(session_id)
        self.redis_client.delete(key)

    def save_conversation(self, session_id, title=None, document_ids=None, touch=True):
        existing = self.get_conversation(session_id) or {}
        now = _now_iso()
        conversation = {
            "sessionId": session_id,
            "title": _clean_title(title) or existing.get("title") or f"TB {session_id}",
            "documentIds": _clean_ids(document_ids) if document_ids is not None else existing.get("documentIds", []),
            "createdAt": existing.get("createdAt") or now,
            "updatedAt": now if touch else existing.get("updatedAt") or now,
        }
        self.redis_client.set(
            self._conversation_key(session_id),
            json.dumps(conversation, ensure_ascii=False),
        )
        self.redis_client.zadd(self._conversation_index_key(), {session_id: time.time()})
        return conversation

    def get_conversation(self, session_id):
        raw = self.redis_client.get(self._conversation_key(session_id))
        if not raw:
            return None
        data = json.loads(raw)
        data["documentIds"] = _clean_ids(data.get("documentIds"))
        return data

    def list_conversations(self, limit=50):
        session_ids = self.redis_client.zrevrange(self._conversation_index_key(), 0, max(0, int(limit) - 1))
        conversations = []
        for session_id in session_ids:
            conversation = self.get_conversation(session_id)
            if conversation:
                conversations.append(conversation)
            else:
                self.redis_client.zrem(self._conversation_index_key(), session_id)
        return conversations

    def rename_conversation(self, session_id, title):
        return self.save_conversation(session_id, title=title, touch=True)

    def update_conversation_documents(self, session_id, document_ids):
        return self.save_conversation(session_id, document_ids=document_ids, touch=True)

    def touch_conversation(self, session_id, document_ids=None):
        return self.save_conversation(session_id, document_ids=document_ids, touch=True)

    def delete_conversation(self, session_id):
        self.redis_client.delete(self._generate_key(session_id))
        self.redis_client.delete(self._conversation_key(session_id))
        self.redis_client.zrem(self._conversation_index_key(), session_id)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _clean_title(title):
    return " ".join(str(title or "").strip().split())[:120]


def _clean_ids(values):
    if not values:
        return []
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
