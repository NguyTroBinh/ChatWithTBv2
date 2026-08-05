"""scheduler.py — ConsolidationScheduler: background daemon chạy định kỳ.

Mỗi tick:
1. Đọc ConsolidationControl node để khôi phục last_run từng task (không chạy lặp sau restart).
2. Build MemoryConsolidator + áp interval từ config.
3. Chạy các task đến lịch.
4. Persist kết quả (ConsolidationRun node + control node) + prune lịch sử.
5. Enqueue meta-memory mới vào enrichment pipeline (đặc biệt là embedding/type).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from threading import Event, Thread
from typing import Any

from app.memory.config import (
    CONSOLIDATION_BASE_DECAY_RATE,
    CONSOLIDATION_CLUSTER_INTERVAL_SECONDS,
    CONSOLIDATION_CLUSTER_MIN_NEIGHBORS,
    CONSOLIDATION_CONTROL_LABEL,
    CONSOLIDATION_CONTROL_NODE_ID,
    CONSOLIDATION_CREATIVE_INTERVAL_SECONDS,
    CONSOLIDATION_CREATIVE_MIN_COUNT,
    CONSOLIDATION_DECAY_IMPORTANCE_THRESHOLD,
    CONSOLIDATION_DECAY_INTERVAL_SECONDS,
    CONSOLIDATION_DELETE_THRESHOLD,
    CONSOLIDATION_ARCHIVE_THRESHOLD,
    CONSOLIDATION_FORGET_INTERVAL_SECONDS,
    CONSOLIDATION_GRACE_PERIOD_DAYS,
    CONSOLIDATION_HISTORY_LIMIT,
    CONSOLIDATION_IMPORTANCE_FLOOR_FACTOR,
    CONSOLIDATION_IMPORTANCE_PROTECTION_THRESHOLD,
    CONSOLIDATION_PROTECTED_TYPES,
    CONSOLIDATION_RUN_LABEL,
    CONSOLIDATION_TASK_FIELDS,
    CONSOLIDATION_TICK_SECONDS,
)

from app.memory.longterm.consolidation import MemoryConsolidator, _parse_dt, _utc_now


_LOGGER = logging.getLogger(__name__)


class ConsolidationScheduler:
    def __init__(
        self,
        store: Any,
        *,
        enrichment_service: Any = None,
        embedding_service: Any = None,
    ) -> None:
        self.store = store
        self.enrichment_service = enrichment_service
        self.embedding_service = embedding_service
        self.stop_event = Event()
        self.thread: Thread | None = None
        self._intervals = {
            "decay": CONSOLIDATION_DECAY_INTERVAL_SECONDS,
            "creative": CONSOLIDATION_CREATIVE_INTERVAL_SECONDS,
            "cluster": CONSOLIDATION_CLUSTER_INTERVAL_SECONDS,
            "forget": CONSOLIDATION_FORGET_INTERVAL_SECONDS,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = Thread(target=self._run, name="consolidation-scheduler", daemon=True)
        self.thread.start()
        _LOGGER.info("Consolidation scheduler started")

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)

    def _run(self) -> None:
        while not self.stop_event.wait(CONSOLIDATION_TICK_SECONDS):
            try:
                self.tick()
            except Exception:
                _LOGGER.exception("Consolidation scheduler tick failed")

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick(self) -> None:
        consolidator = self._build_consolidator()
        if consolidator is None:
            return
        results = consolidator.run_scheduled_tasks(
            decay_threshold=CONSOLIDATION_DECAY_IMPORTANCE_THRESHOLD
        )
        for result in results:
            self._persist_run(result)
            # Enqueue meta-memory mới để được nạp embedding/type bởi enrichment.
            self._enqueue_meta_ids(result)

    def _build_consolidator(self) -> MemoryConsolidator | None:
        consolidator = MemoryConsolidator(
            self.store,
            embedding_service=self.embedding_service,
            intervals=self._intervals,
            delete_threshold=CONSOLIDATION_DELETE_THRESHOLD,
            archive_threshold=CONSOLIDATION_ARCHIVE_THRESHOLD,
            grace_period_days=CONSOLIDATION_GRACE_PERIOD_DAYS,
            importance_protection_threshold=CONSOLIDATION_IMPORTANCE_PROTECTION_THRESHOLD,
            protected_types=set(CONSOLIDATION_PROTECTED_TYPES),
            base_decay_rate=CONSOLIDATION_BASE_DECAY_RATE,
            importance_floor_factor=CONSOLIDATION_IMPORTANCE_FLOOR_FACTOR,
            creative_min_count=CONSOLIDATION_CREATIVE_MIN_COUNT,
            cluster_min_neighbors=CONSOLIDATION_CLUSTER_MIN_NEIGHBORS,
        )
        control = self._load_control()
        for task, schedule in consolidator.schedules.items():
            field = CONSOLIDATION_TASK_FIELDS.get(task)
            if field and control.get(field):
                schedule["last_run"] = control[field]
        return consolidator

    def _enqueue_meta_ids(self, result: dict[str, Any]) -> None:
        if self.enrichment_service is None:
            return
        steps = result.get("steps") or {}
        for task_steps in steps.values():
            if not isinstance(task_steps, dict):
                continue
            for meta_id in task_steps.get("meta_ids") or []:
                self.enrichment_service.enqueue(meta_id)

    # ------------------------------------------------------------------
    # Persist control / runs
    # ------------------------------------------------------------------

    def _load_control(self) -> dict[str, Any]:
        with self.store.driver.session(database=self.store.database) as session:
            node = session.execute_read(
                lambda tx: tx.run(
                    f"MATCH (c:{CONSOLIDATION_CONTROL_LABEL} {{id: $id}}) RETURN c AS node",
                    id=CONSOLIDATION_CONTROL_NODE_ID,
                ).single()
            )
        if node is None:
            return {}
        props = node["node"]
        data = dict(props)
        # Neo4j temporal → chuẩn hóa về ISO string để _parse_dt dễ xử lý khi build scheduler.
        return {
            key: value
            for key, value in data.items()
            if key in CONSOLIDATION_TASK_FIELDS.values() and value is not None
        }

    def _persist_run(self, result: dict[str, Any]) -> None:
        mode = result.get("mode", "unknown")
        completed_at = result.get("completed_at") or _utc_now().isoformat()
        started_at = result.get("started_at") or completed_at
        success = bool(result.get("success"))
        dry_run = bool(result.get("dry_run"))

        with self.store.driver.session(database=self.store.database) as session:
            session.execute_write(
                lambda tx: tx.run(
                    f"""
CREATE (r:{CONSOLIDATION_RUN_LABEL} {{
    id: $id,
    mode: $mode,
    success: $success,
    dryRun: $dry_run,
    startedAt: datetime($started_at),
    completedAt: datetime($completed_at),
    result: $result
}})
""",
                    id=uuid.uuid4().hex,
                    mode=mode,
                    success=success,
                    dry_run=dry_run,
                    started_at=started_at,
                    completed_at=completed_at,
                    result=json.dumps(result, default=str),
                )
            )
            session.execute_write(
                lambda tx: tx.run(
                    f"""
MERGE (c:{CONSOLIDATION_CONTROL_LABEL} {{id: $id}})
SET c.{CONSOLIDATION_TASK_FIELDS[mode] if mode in CONSOLIDATION_TASK_FIELDS else 'decayLastRun'} = datetime($timestamp)
""",
                    id=CONSOLIDATION_CONTROL_NODE_ID,
                    timestamp=completed_at,
                )
            )
            session.execute_write(
                lambda tx: tx.run(
                    f"""
MATCH (r:{CONSOLIDATION_RUN_LABEL})
WITH r ORDER BY r.startedAt DESC
SKIP $keep
DELETE r
""",
                    keep=CONSOLIDATION_HISTORY_LIMIT,
                )
            )