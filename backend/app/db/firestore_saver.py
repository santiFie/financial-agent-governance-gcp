"""
LangGraph checkpoint saver backed by Cloud Firestore (native mode).

Own minimal implementation on top of `google-cloud-firestore` (already a pinned dependency) instead of a third-party
saver: checkpoints hold the whole graph state, so the code that persists them stays small and auditable.

Data model (every path is under the configured collection):

    {collection}/t-{thread_id}/cp[-{ns}]/{checkpoint_id}              one document per checkpoint
    {collection}/t-{thread_id}/wr[-{ns}]/{checkpoint_id}~{task}~{idx}  one document per pending write

State is serialized with LangGraph's own serde and stored as Firestore bytes. Queries only use equality / range
filters and ordering on a single field, so they run on Firestore's automatic indexes (no composite index to create).
Documents are limited to 1 MiB by Firestore; an oversized checkpoint raises a clear error instead of failing obscurely.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote

from google.cloud.firestore_v1.base_query import FieldFilter
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
    writes_sort_key,
)

logger = logging.getLogger(__name__)

# Firestore rejects documents above 1 MiB (including field names and overhead): stay below with some margin.
MAX_PAYLOAD_BYTES = 1_000_000
HEALTHCHECK_DOC = "_healthcheck"


def _segment(value: str) -> str:
    """Makes any string safe as a single Firestore path segment (no '/', no reserved names)."""
    return quote(value, safe="")


class FirestoreSaver(BaseCheckpointSaver):
    """Persists LangGraph checkpoints and pending writes in Cloud Firestore."""

    def __init__(self, client: Any, collection: str, ttl_seconds: Optional[int] = None) -> None:
        super().__init__()
        self.client = client
        self.collection = collection
        self.ttl_seconds = ttl_seconds

    # ------------------------------------------------------------------ helpers

    def _thread_ref(self, thread_id: str):
        return self.client.collection(self.collection).document(f"t-{_segment(thread_id)}")

    @staticmethod
    def _suffix(checkpoint_ns: str) -> str:
        return f"-{_segment(checkpoint_ns)}" if checkpoint_ns else ""

    def _cp_collection(self, thread_id: str, checkpoint_ns: str):
        return self._thread_ref(thread_id).collection(f"cp{self._suffix(checkpoint_ns)}")

    def _wr_collection(self, thread_id: str, checkpoint_ns: str):
        return self._thread_ref(thread_id).collection(f"wr{self._suffix(checkpoint_ns)}")

    def _expiry(self) -> dict:
        if not self.ttl_seconds:
            return {}
        return {"expire_at": datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)}

    @staticmethod
    def _check_size(data: bytes, what: str) -> None:
        if len(data) > MAX_PAYLOAD_BYTES:
            raise ValueError(
                f"{what} serializado pesa {len(data)} bytes y supera el límite de documento de Firestore (1 MiB)."
            )

    def _load_writes(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> list:
        docs = (
            self._wr_collection(thread_id, checkpoint_ns)
            .where(filter=FieldFilter("checkpoint_id", "==", checkpoint_id))
            .stream()
        )
        rows = [d.to_dict() for d in docs]
        rows.sort(key=lambda r: writes_sort_key(r["task_path"], r["task_id"], r["idx"]))
        return [(r["task_id"], r["channel"], self.serde.loads_typed((r["value_type"], r["value"]))) for r in rows]

    def _tuple_from_doc(self, thread_id: str, checkpoint_ns: str, data: dict) -> CheckpointTuple:
        checkpoint_id = data["checkpoint_id"]
        parent_id = data.get("parent_checkpoint_id")
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint=self.serde.loads_typed((data["checkpoint_type"], data["checkpoint"])),
            metadata=self.serde.loads_typed((data["metadata_type"], data["metadata"])),
            parent_config=(
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "checkpoint_id": parent_id}}
                if parent_id
                else None
            ),
            pending_writes=self._load_writes(thread_id, checkpoint_ns, checkpoint_id),
        )

    # ------------------------------------------------------------------ BaseCheckpointSaver (sync)

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        collection = self._cp_collection(thread_id, checkpoint_ns)

        if checkpoint_id := get_checkpoint_id(config):
            snapshot = collection.document(_segment(checkpoint_id)).get()
            if not snapshot.exists:
                return None
            return self._tuple_from_doc(thread_id, checkpoint_ns, snapshot.to_dict())

        latest = list(collection.order_by("checkpoint_id", direction="DESCENDING").limit(1).stream())
        if not latest:
            return None
        return self._tuple_from_doc(thread_id, checkpoint_ns, latest[0].to_dict())

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            raise ValueError("FirestoreSaver.list necesita un config con thread_id (no lista todos los hilos).")
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")

        query = self._cp_collection(thread_id, checkpoint_ns)
        if config_checkpoint_id := get_checkpoint_id(config):
            query = query.where(filter=FieldFilter("checkpoint_id", "==", config_checkpoint_id))
        elif before and (before_id := get_checkpoint_id(before)):
            query = query.where(filter=FieldFilter("checkpoint_id", "<", before_id))
        query = query.order_by("checkpoint_id", direction="DESCENDING")
        if limit is not None and not filter:
            query = query.limit(limit)

        remaining = limit
        for doc in query.stream():
            tuple_ = self._tuple_from_doc(thread_id, checkpoint_ns, doc.to_dict())
            if filter and not all(tuple_.metadata.get(k) == v for k, v in filter.items()):
                continue
            if remaining is not None:
                if remaining <= 0:
                    break
                remaining -= 1
            yield tuple_

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_type, checkpoint_bytes = self.serde.dumps_typed(checkpoint)
        metadata_type, metadata_bytes = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        self._check_size(checkpoint_bytes, "El checkpoint")

        self._cp_collection(thread_id, checkpoint_ns).document(_segment(checkpoint["id"])).set(
            {
                "checkpoint_id": checkpoint["id"],
                "parent_checkpoint_id": config["configurable"].get("checkpoint_id"),
                "checkpoint_type": checkpoint_type,
                "checkpoint": checkpoint_bytes,
                "metadata_type": metadata_type,
                "metadata": metadata_bytes,
                "created_at": datetime.now(timezone.utc),
                **self._expiry(),
            }
        )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        collection = self._wr_collection(thread_id, checkpoint_ns)

        for position, (channel, value) in enumerate(writes):
            idx = WRITES_IDX_MAP.get(channel, position)
            doc = collection.document(f"{_segment(checkpoint_id)}~{_segment(task_id)}~{idx}")
            # Regular writes are immutable once stored; special writes (error, interrupt, resume...) are overwritten.
            if idx >= 0 and doc.get().exists:
                continue
            value_type, value_bytes = self.serde.dumps_typed(value)
            self._check_size(value_bytes, f"La escritura pendiente '{channel}'")
            doc.set(
                {
                    "checkpoint_id": checkpoint_id,
                    "task_id": task_id,
                    "task_path": task_path,
                    "idx": idx,
                    "channel": channel,
                    "value_type": value_type,
                    "value": value_bytes,
                    **self._expiry(),
                }
            )

    def delete_thread(self, thread_id: str) -> None:
        thread_ref = self._thread_ref(thread_id)
        for sub in thread_ref.collections():
            for doc in sub.stream():
                doc.reference.delete()
        thread_ref.delete()

    # ------------------------------------------------------------------ BaseCheckpointSaver (async)
    # The Firestore client is synchronous: async entry points run the sync implementation in a worker thread.

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        items = await asyncio.to_thread(lambda: list(self.list(config, filter=filter, before=before, limit=limit)))
        for item in items:
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)

    # ------------------------------------------------------------------ connectivity

    def verify_connection(self, timeout: float = 10.0) -> None:
        """Round-trip read against Firestore. Raises if credentials, the API or the database are not usable."""
        self.client.collection(self.collection).document(HEALTHCHECK_DOC).get(timeout=timeout)
