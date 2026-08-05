"""consolidation.py — MemoryConsolidator: học và cập nhật bộ nhớ theo thời gian.

Port của v1 (app/longtermmemory/consolidation/consolidator.py) sang Neo4j driver:
- Bỏ multi-tenancy (v2 đã tắt user; scope toàn cục trên :Memory).
- Prop names camelCase (lastAccessed, updatedAt, createdAt).
- Truy vấn qua driver session thay cho graph.query().result_set của FalkorDB.

4 task định kỳ:
- decay   (ngày)   : giảm importance của memory cũ ít được truy cập
- creative (tuần)  : tổng hợp memories cùng type (SIMILAR_TO) thành Insight meta-memory
- cluster  (tháng) : gom nhóm memories có nhiều SIMILAR_TO thành cluster meta-memory
- forget   (tắt mặc định): archive/xóa memory có importance quá thấp
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    # Tham số chạy trả về từ Neo4j để có thể là neo4j.time.DateTime (có to_native).
    if hasattr(value, "to_native"):
        value = value.to_native()
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, type(datetime.now(timezone.utc).date())):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class MemoryConsolidator:
    """Consolidates memories over time: decay, creative synthesis, clustering, forgetting."""

    DEFAULT_INTERVALS: dict[str, timedelta] = {
        "decay": timedelta(seconds=86400),    # 1 ngày
        "creative": timedelta(seconds=604800),  # 7 ngày
        "cluster": timedelta(seconds=2592000),  # 30 ngày
        "forget": timedelta(seconds=0),        # tắt
    }

    def __init__(
        self,
        store: Any,
        *,
        embedding_service: Any = None,
        intervals: dict[str, int] | None = None,
        delete_threshold: float = 0.0,
        archive_threshold: float = 0.0,
        grace_period_days: int = 90,
        importance_protection_threshold: float = 0.7,
        protected_types: set[str] | None = None,
        base_decay_rate: float = 0.01,
        importance_floor_factor: float = 0.3,
        creative_min_count: int = 3,
        cluster_min_neighbors: int = 3,
    ) -> None:
        self.store = store
        self.embedding_service = embedding_service
        self.delete_threshold = delete_threshold
        self.archive_threshold = archive_threshold
        self.grace_period_days = grace_period_days
        self.importance_protection_threshold = importance_protection_threshold
        self.protected_types = protected_types or {"Decision", "Insight"}
        self.base_decay_rate = base_decay_rate
        self.importance_floor_factor = importance_floor_factor
        self.creative_min_count = max(1, int(creative_min_count))
        self.cluster_min_neighbors = max(1, int(cluster_min_neighbors))

        self.schedules: dict[str, dict[str, Any]] = {
            task: {"interval": timedelta(seconds=intervals.get(task, 0)), "last_run": None}
            for task in self.DEFAULT_INTERVALS
        }
        if intervals:
            for task, seconds in intervals.items():
                if task in self.schedules:
                    self.schedules[task]["interval"] = timedelta(seconds=max(0, int(seconds)))

    # ------------------------------------------------------------------
    # Neo4j helpers
    # ------------------------------------------------------------------

    def _session(self):
        return self.store.driver.session(database=self.store.database)

    def _read(self, cypher: str, **params: Any) -> list[dict]:
        with self._session() as session:
            records = session.execute_read(
                lambda tx: [dict(record) for record in tx.run(cypher, **params)]
            )
        return records

    def _write(self, cypher: str, **params: Any) -> None:
        with self._session() as session:
            session.execute_write(lambda tx: tx.run(cypher, **params))

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run_scheduled_tasks(self, *, decay_threshold: float | None = None) -> list[dict[str, Any]]:
        """Kiểm tra và chạy các task đến lịch. Trả về list kết quả."""
        now = _utc_now()
        results: list[dict[str, Any]] = []

        task_runners = {
            "decay": lambda: self._run_decay(decay_threshold),
            "creative": self._run_creative,
            "cluster": self._run_cluster,
            "forget": self._run_forget,
        }

        for task, runner in task_runners.items():
            schedule = self.schedules.get(task, {})
            interval: timedelta = schedule.get("interval", timedelta(seconds=0))
            if interval.total_seconds() <= 0:
                self.schedules[task]["last_run"] = now
                continue

            last_run = _parse_dt(schedule.get("last_run"))
            if last_run is not None and (now - last_run) < interval:
                continue

            started_at = now.isoformat()
            try:
                steps = runner()
                result = {
                    "mode": task,
                    "success": True,
                    "started_at": started_at,
                    "completed_at": _utc_now().isoformat(),
                    "steps": {task: steps},
                }
            except Exception as exc:
                result = {
                    "mode": task,
                    "success": False,
                    "started_at": started_at,
                    "completed_at": _utc_now().isoformat(),
                    "steps": {},
                    "error": str(exc),
                }
            self.schedules[task]["last_run"] = _utc_now()
            results.append(result)

        return results

    def get_next_runs(self) -> dict[str, str]:
        now = _utc_now()
        next_runs: dict[str, str] = {}
        for task, schedule in self.schedules.items():
            interval: timedelta = schedule.get("interval", timedelta(seconds=0))
            if interval.total_seconds() <= 0:
                next_runs[task] = "disabled"
                continue
            last_run = _parse_dt(schedule.get("last_run"))
            next_runs[task] = now.isoformat() if last_run is None else (last_run + interval).isoformat()
        return next_runs

    def create_meta_memory(self, *, content: str, memory_type: str, importance: float,
                           source_type: str | None, source_ids: list[str],
                           tags: list[str], metadata: dict[str, Any],
                           extra_props: dict[str, Any] | None = None) -> dict | None:
        """Tạo meta-memory node (dùng chung giữa creative/cluster)."""
        meta_id = str(uuid.uuid4())
        embedding = None
        if self.embedding_service is not None:
            try:
                embedding = [float(v) for v in self.embedding_service.embed_query(content)]
            except Exception:
                embedding = None

        props = {
            "id": meta_id,
            "content": content,
            "type": memory_type,
            "importance": float(importance),
            "metadata": json.dumps(metadata, ensure_ascii=False, default=str),
            "tags": tags,
            "tag_prefixes": [tag for tag in tags],
            "processed": False,
            "meta": True,
            "sessionId": None,
            "embedding": embedding,
        }
        if source_type:
            props["sourceType"] = source_type
        if source_ids:
            props["sourceIds"] = list(source_ids)
        if extra_props:
            props.update(extra_props)

        self._write(
            """
CREATE (m:Memory {
    id: $id,
    content: $content,
    createdAt: datetime(),
    updatedAt: datetime(),
    lastAccessed: datetime(),
    type: $type,
    importance: $importance,
    metadata: $metadata,
    tags: $tags,
    tagPrefixes: $tag_prefixes,
    processed: $processed,
    meta: $meta,
    sessionId: $sessionId,
    embedding: $embedding,
    sourceType: $source_type,
    sourceIds: $source_ids
})
            """,
            **props,
        )
        return {"id": meta_id, "embedding": embedding}

    # ------------------------------------------------------------------
    # Task: Decay
    # ------------------------------------------------------------------

    def _run_decay(self, decay_threshold: float | None = None) -> dict[str, Any]:
        cutoff = _utc_now() - timedelta(days=1)
        rows = self._read(
            """
MATCH (m:Memory)
WHERE coalesce(m.archived, false) = false
  AND m.lastAccessed < $cutoff
RETURN m.id AS id, m.importance AS importance, m.lastAccessed AS lastAccessed,
       m.type AS type, m.createdAt AS createdAt
LIMIT 500
""",
            cutoff=cutoff,
        )

        updated = 0
        skipped = 0
        now = _utc_now()

        for row in rows:
            memory_id = row.get("id")
            importance = row.get("importance")
            mem_type = row.get("type")
            if importance is not None and float(importance) >= self.importance_protection_threshold:
                skipped += 1
                continue
            if mem_type in self.protected_types:
                skipped += 1
                continue

            ref_time = _parse_dt(row.get("lastAccessed")) or _parse_dt(row.get("createdAt")) or now
            age_days = max(0.0, (now - ref_time).total_seconds() / 86400.0)
            current = float(importance) if importance is not None else 0.5
            floor = current * self.importance_floor_factor
            new_importance = round(max(floor, current - self.base_decay_rate * age_days), 4)

            if new_importance >= current:
                skipped += 1
                continue
            if decay_threshold is not None and current <= decay_threshold:
                skipped += 1
                continue

            try:
                self._write(
                    "MATCH (m:Memory {id: $id}) SET m.importance = $importance, m.decayUpdatedAt = datetime()",
                    id=memory_id,
                    importance=new_importance,
                )
                updated += 1
            except Exception:
                skipped += 1

        return {"updated": updated, "skipped": skipped}

    # ------------------------------------------------------------------
    # Task: Creative — tổng hợp memories cùng type thành Insight meta-memory
    # ------------------------------------------------------------------

    def _run_creative(self) -> dict[str, Any]:
        rows = self._read(
            """
MATCH (m:Memory)-[:SIMILAR_TO]->(n:Memory)
WHERE m.type = n.type
  AND coalesce(m.archived, false) = false
  AND coalesce(n.archived, false) = false
  AND coalesce(m.meta, false) = false
WITH m.type AS mem_type,
     collect(DISTINCT m.id)[..10] AS ids,
     collect(DISTINCT m.content)[..5] AS contents,
     avg(m.importance) AS avg_importance
WHERE size(ids) >= $min_count
RETURN mem_type, ids, contents, avg_importance
LIMIT 10
""",
            min_count=self.creative_min_count,
        )

        created_ids: list[str] = []
        now = _utc_now()

        for row in rows:
            mem_type = row.get("mem_type")
            ids = row.get("ids") or []
            contents = row.get("contents") or []

            # Kiểm tra đã có meta-memory cho type này trong 7 ngày chưa
            since = now - timedelta(days=7)
            existing = self._read(
                """
MATCH (m:Memory {meta: true, sourceType: $type})
WHERE m.createdAt > $since
RETURN m.id AS id
LIMIT 1
""",
                type=mem_type,
                since=since,
            )
            if existing:
                continue

            sample_contents = [str(c) for c in (contents or []) if c][:3]
            summary = (
                f"Tổng hợp {len(ids)} ký ức loại {mem_type}: "
                + " | ".join(c[:80] for c in sample_contents)
            )
            created = self.create_meta_memory(
                content=summary,
                memory_type="Insight",
                importance=min(0.9, float(row.get("avg_importance") or 0.5) + 0.1),
                source_type=mem_type,
                source_ids=list(ids or []),
                tags=["meta-memory", f"type:{str(mem_type).lower()}", "consolidation", "creative"],
                metadata={
                    "consolidation": {
                        "task": "creative",
                        "source_count": len(ids or []),
                        "source_type": mem_type,
                    }
                },
            )
            if not created:
                continue
            meta_id = created["id"]
            for src_id in (ids or [])[:5]:
                try:
                    self._write(
                        """
MATCH (meta:Memory {id: $meta_id})
MATCH (src:Memory {id: $src_id})
MERGE (meta)-[:DERIVED_FROM {transformation: 'creative_synthesis', confidence: 0.7}]->(src)
""",
                        meta_id=meta_id,
                        src_id=src_id,
                    )
                except Exception:
                    pass
            created_ids.append(meta_id)

        return {"created": len(created_ids), "meta_ids": created_ids}

    # ------------------------------------------------------------------
    # Task: Cluster — gom nhóm memories tương đồng (SIMILAR_TO hub)
    # ------------------------------------------------------------------

    def _run_cluster(self) -> dict[str, Any]:
        rows = self._read(
            """
MATCH (m:Memory)-[:SIMILAR_TO]->(n:Memory)
WHERE coalesce(m.archived, false) = false
  AND coalesce(m.meta, false) = false
WITH m, count(n) AS neighbor_count
WHERE neighbor_count >= $min_neighbors
ORDER BY neighbor_count DESC
LIMIT 5
RETURN m.id AS id, m.content AS content, m.type AS type,
       m.importance AS importance, neighbor_count
""",
            min_neighbors=self.cluster_min_neighbors,
        )

        created_ids: list[str] = []
        now = _utc_now()

        for row in rows:
            hub_id = row.get("id")
            hub_content = row.get("content")
            hub_type = row.get("type")
            hub_importance = row.get("importance")
            neighbor_count = row.get("neighbor_count")

            neighbors = self._read(
                """
MATCH (m:Memory {id: $id})-[:SIMILAR_TO]->(n:Memory)
WHERE coalesce(n.archived, false) = false
  AND coalesce(n.meta, false) = false
RETURN n.id AS id, n.content AS content
LIMIT 10
""",
                id=hub_id,
            )
            neighbor_ids = [n.get("id") for n in neighbors if n.get("id")]

            existing = self._read(
                "MATCH (m:Memory {clusterHub: $hub_id}) RETURN m.id AS id LIMIT 1",
                hub_id=hub_id,
            )
            if existing:
                continue

            cluster_content = f"Cluster {neighbor_count + 1} ký ức liên quan: " + str(hub_content or "")[:120]
            created = self.create_meta_memory(
                content=cluster_content,
                memory_type=hub_type or "Context",
                importance=min(0.85, float(hub_importance or 0.5) + 0.05),
                source_type=None,
                source_ids=[],
                tags=["cluster", "consolidation", f"type:{(hub_type or 'context').lower()}", "cluster-meta"],
                metadata={
                    "consolidation": {
                        "task": "cluster",
                        "hub_id": hub_id,
                        "cluster_size": len(neighbor_ids) + 1,
                    }
                },
                extra_props={
                    "clusterHub": hub_id,
                    "clusterSize": len(neighbor_ids) + 1,
                },
            )
            if not created:
                continue
            cluster_id = created["id"]
            for member_id in [hub_id] + neighbor_ids[:9]:
                try:
                    self._write(
                        """
MATCH (c:Memory {id: $cluster_id})
MATCH (m:Memory {id: $member_id})
MERGE (m)-[:PART_OF {role: 'cluster_member', context: 'auto_cluster'}]->(c)
""",
                        cluster_id=cluster_id,
                        member_id=member_id,
                    )
                except Exception:
                    pass
            created_ids.append(cluster_id)

        return {"meta_memories_created": len(created_ids), "meta_ids": created_ids}

    # ------------------------------------------------------------------
    # Task: Forget — archive hoặc xóa memory có importance thấp
    # ------------------------------------------------------------------

    def _run_forget(self) -> dict[str, Any]:
        if self.delete_threshold <= 0.0 and self.archive_threshold <= 0.0:
            return {"archived": 0, "deleted": 0, "skipped": 0}

        grace_cutoff = _utc_now() - timedelta(days=self.grace_period_days)
        rows = self._read(
            """
MATCH (m:Memory)
WHERE m.createdAt < $grace_cutoff
  AND coalesce(m.archived, false) = false
  AND coalesce(m.meta, false) = false
  AND m.importance < $max_threshold
RETURN m.id AS id, m.importance AS importance, m.type AS type
LIMIT 200
""",
            grace_cutoff=grace_cutoff,
            max_threshold=max(self.delete_threshold, self.archive_threshold),
        )

        archived = 0
        deleted = 0
        skipped = 0

        for row in rows:
            memory_id = row.get("id")
            imp = float(row.get("importance")) if row.get("importance") is not None else 0.0
            mem_type = row.get("type")

            if imp >= self.importance_protection_threshold:
                skipped += 1
                continue
            if mem_type in self.protected_types:
                skipped += 1
                continue

            if self.delete_threshold > 0.0 and imp < self.delete_threshold:
                try:
                    self._write("MATCH (m:Memory {id: $id}) DETACH DELETE m", id=memory_id)
                    deleted += 1
                except Exception:
                    skipped += 1
            elif self.archive_threshold > 0.0 and imp < self.archive_threshold:
                try:
                    self._write(
                        "MATCH (m:Memory {id: $id}) SET m.archived = true, m.archivedAt = datetime()",
                        id=memory_id,
                    )
                    archived += 1
                except Exception:
                    skipped += 1
            else:
                skipped += 1

        return {"archived": archived, "deleted": deleted, "skipped": skipped}