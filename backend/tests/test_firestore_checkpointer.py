"""FirestoreSaver and get_checkpointer() against an in-memory fake Firestore (no network, no credentials)."""

import logging
from typing import TypedDict

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.config import settings
from app.core.service_status import service_status
from app.db import checkpointer as checkpointer_module
from app.db.firestore_saver import MAX_PAYLOAD_BYTES, FirestoreSaver
from tests.fakes_firestore import FakeFirestoreClient, FakeFirestoreStore


def _cfg(thread="thread-1", ns="", checkpoint_id=None):
    configurable = {"thread_id": thread, "checkpoint_ns": ns}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def _saver(store=None, **kwargs):
    return FirestoreSaver(FakeFirestoreClient(store), "test_checkpoints", **kwargs)


def _checkpoint(cp_id, **values):
    cp = empty_checkpoint()
    cp["id"] = cp_id
    cp["channel_values"] = values
    return cp


def test_put_and_get_tuple_round_trip_with_parent_and_metadata():
    saver = _saver()
    first = saver.put(_cfg(), _checkpoint("001", messages=["hola"]), {"source": "input", "step": 0}, {})
    saver.put(first, _checkpoint("002", messages=["hola", "chau"]), {"source": "loop", "step": 1}, {})

    latest = saver.get_tuple(_cfg())

    assert latest.config["configurable"]["checkpoint_id"] == "002"
    assert latest.checkpoint["channel_values"] == {"messages": ["hola", "chau"]}
    assert latest.metadata["step"] == 1
    assert latest.parent_config["configurable"]["checkpoint_id"] == "001"
    assert saver.get_tuple(_cfg(checkpoint_id="001")).checkpoint["channel_values"] == {"messages": ["hola"]}
    assert saver.get_tuple(_cfg(checkpoint_id="zzz")) is None
    assert saver.get_tuple(_cfg(thread="otro")) is None


def test_list_is_newest_first_and_supports_before_limit_and_metadata_filter():
    saver = _saver()
    config = _cfg()
    for i, source in enumerate(["input", "loop", "loop"]):
        config = saver.put(config, _checkpoint(f"00{i}"), {"source": source, "step": i}, {})

    assert [t.config["configurable"]["checkpoint_id"] for t in saver.list(_cfg())] == ["002", "001", "000"]
    assert [t.config["configurable"]["checkpoint_id"] for t in saver.list(_cfg(), limit=2)] == ["002", "001"]
    assert [
        t.config["configurable"]["checkpoint_id"] for t in saver.list(_cfg(), before=_cfg(checkpoint_id="002"))
    ] == [
        "001",
        "000",
    ]
    assert [t.metadata["step"] for t in saver.list(_cfg(), filter={"source": "input"})] == [0]


def test_pending_writes_are_ordered_and_regular_writes_are_immutable():
    saver = _saver()
    config = saver.put(_cfg(), _checkpoint("001"), {"source": "loop", "step": 0}, {})

    saver.put_writes(config, [("messages", "a"), ("plan", "b")], task_id="task-b", task_path="2")
    saver.put_writes(config, [("messages", "first")], task_id="task-a", task_path="1")
    saver.put_writes(config, [("messages", "OVERWRITE")], task_id="task-a", task_path="1")  # ignored: idx >= 0

    writes = saver.get_tuple(config).pending_writes
    assert writes == [("task-a", "messages", "first"), ("task-b", "messages", "a"), ("task-b", "plan", "b")]


def test_special_writes_such_as_interrupts_are_overwritten():
    saver = _saver()
    config = saver.put(_cfg(), _checkpoint("001"), {"source": "loop", "step": 0}, {})

    saver.put_writes(config, [("__interrupt__", ["v1"])], task_id="t")
    saver.put_writes(config, [("__interrupt__", ["v2"])], task_id="t")

    assert saver.get_tuple(config).pending_writes == [("t", "__interrupt__", ["v2"])]


def test_threads_and_namespaces_are_isolated_and_ids_are_path_safe():
    saver = _saver()
    saver.put(_cfg(thread="a/b c"), _checkpoint("001", who="slash"), {"source": "input", "step": 0}, {})
    saver.put(_cfg(thread="other"), _checkpoint("001", who="other"), {"source": "input", "step": 0}, {})
    saver.put(_cfg(thread="a/b c", ns="sub:1"), _checkpoint("009", who="ns"), {"source": "input", "step": 0}, {})

    assert saver.get_tuple(_cfg(thread="a/b c")).checkpoint["channel_values"] == {"who": "slash"}
    assert saver.get_tuple(_cfg(thread="other")).checkpoint["channel_values"] == {"who": "other"}
    assert saver.get_tuple(_cfg(thread="a/b c", ns="sub:1")).checkpoint["channel_values"] == {"who": "ns"}


def test_delete_thread_removes_checkpoints_and_writes_only_for_that_thread():
    store = FakeFirestoreStore()
    saver = _saver(store)
    config = saver.put(_cfg(thread="gone"), _checkpoint("001"), {"source": "input", "step": 0}, {})
    saver.put_writes(config, [("messages", "x")], task_id="t")
    saver.put(_cfg(thread="kept"), _checkpoint("001"), {"source": "input", "step": 0}, {})

    saver.delete_thread("gone")

    assert saver.get_tuple(_cfg(thread="gone")) is None
    assert saver.get_tuple(_cfg(thread="kept")) is not None
    assert all("t-gone" not in path for path in store.docs)


def test_ttl_adds_expire_at_only_when_configured():
    store = FakeFirestoreStore()
    _saver(store, ttl_seconds=3600).put(_cfg(), _checkpoint("001"), {"source": "input", "step": 0}, {})
    assert all("expire_at" in doc for doc in store.docs.values())

    other = FakeFirestoreStore()
    _saver(other).put(_cfg(), _checkpoint("001"), {"source": "input", "step": 0}, {})
    assert all("expire_at" not in doc for doc in other.docs.values())


def test_oversized_checkpoint_fails_with_a_clear_error():
    saver = _saver()
    huge = _checkpoint("001", blob="x" * (MAX_PAYLOAD_BYTES + 10))

    with pytest.raises(ValueError, match="1 MiB"):
        saver.put(_cfg(), huge, {"source": "input", "step": 0}, {})


@pytest.mark.asyncio
async def test_async_entry_points_delegate_to_the_sync_implementation():
    saver = _saver()
    config = await saver.aput(_cfg(), _checkpoint("001", n=1), {"source": "input", "step": 0}, {})
    await saver.aput_writes(config, [("messages", "x")], task_id="t")

    got = await saver.aget_tuple(_cfg())
    listed = [t async for t in saver.alist(_cfg())]

    assert got.checkpoint["channel_values"] == {"n": 1} and got.pending_writes == [("t", "messages", "x")]
    assert len(listed) == 1


# --- Human-in-the-loop (interrupt + resume) survives a "process restart": a new saver over the same Firestore -----


class _State(TypedDict, total=False):
    amount: int
    approved: bool
    done: bool


def _build_hitl_graph(saver):
    def stage(state):
        return {"amount": 49_999}

    def approval(state):
        decision = interrupt({"action": "approve_transfer", "amount": state["amount"]})
        return {"approved": bool(decision)}

    def execute(state):
        return {"done": state["approved"]}

    graph = StateGraph(_State)
    graph.add_node("stage", stage)
    graph.add_node("approval", approval)
    graph.add_node("execute", execute)
    graph.add_edge(START, "stage")
    graph.add_edge("stage", "approval")
    graph.add_edge("approval", "execute")
    graph.add_edge("execute", END)
    return graph.compile(checkpointer=saver)


def test_interrupt_and_resume_work_across_saver_instances():
    store = FakeFirestoreStore()
    config = {"configurable": {"thread_id": "hitl-session"}}

    paused = _build_hitl_graph(_saver(store))
    list(paused.stream({}, config=config, stream_mode="updates"))
    snapshot = paused.get_state(config)
    assert snapshot.next == ("approval",)
    assert snapshot.tasks[0].interrupts[0].value == {"action": "approve_transfer", "amount": 49_999}

    # A brand-new saver and graph (as after a restart) resumes from what Firestore holds.
    resumed = _build_hitl_graph(_saver(store))
    list(resumed.stream(Command(resume=True), config=config, stream_mode="updates"))

    final = resumed.get_state(config)
    assert final.values["done"] is True and final.next == ()


# --- get_checkpointer() selection and logged fallback -------------------------------------------------------------


@pytest.fixture
def _status():
    service_status.reset()
    yield
    service_status.reset()


def test_memory_checkpointer_by_default(monkeypatch, _status):
    monkeypatch.setattr(settings, "checkpointer_type", "memory")

    assert isinstance(checkpointer_module.get_checkpointer(), MemorySaver)
    assert service_status.snapshot()["checkpointer"]["local"] == 1


def test_firestore_checkpointer_is_used_when_configured(monkeypatch, _status):
    monkeypatch.setattr(settings, "checkpointer_type", "firestore")
    monkeypatch.setattr(
        checkpointer_module, "build_firestore_checkpointer", lambda: FirestoreSaver(FakeFirestoreClient(), "c")
    )

    assert isinstance(checkpointer_module.get_checkpointer(), FirestoreSaver)
    snap = service_status.snapshot()["checkpointer"]
    assert snap["gcp"] == 1 and snap["local_fallback"] == 0


def test_firestore_failure_falls_back_to_memory_with_log_and_record(monkeypatch, caplog, _status):
    monkeypatch.setattr(settings, "checkpointer_type", "firestore")

    def boom():
        raise RuntimeError("403 Cloud Firestore API has not been used")

    monkeypatch.setattr(checkpointer_module, "build_firestore_checkpointer", boom)

    with caplog.at_level(logging.WARNING, logger="app.db.checkpointer"):
        saver = checkpointer_module.get_checkpointer()

    assert isinstance(saver, MemorySaver)
    assert any("MemorySaver LOCAL" in r.message and "403" in r.message for r in caplog.records)
    snap = service_status.snapshot()["checkpointer"]
    assert snap["local_fallback"] == 1 and "403" in snap["last_fallback_reason"]


def test_verify_connection_surfaces_firestore_errors():
    store = FakeFirestoreStore()
    store.fail_with = PermissionError("permission denied")

    with pytest.raises(PermissionError):
        FirestoreSaver(FakeFirestoreClient(store), "c").verify_connection()
