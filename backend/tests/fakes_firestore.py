"""Minimal in-memory fake of the Firestore client surface used by FirestoreSaver (no network, no credentials)."""

from typing import Any, Dict, List, Optional


class FakeFirestoreStore:
    """Flat store keyed by the full document path, e.g. 'coll/t-abc/cp/doc'."""

    def __init__(self) -> None:
        self.docs: Dict[str, Dict[str, Any]] = {}
        self.fail_with: Optional[Exception] = None  # makes every read raise (to simulate permission/API errors)


class FakeSnapshot:
    def __init__(self, ref: "FakeDocumentRef", data: Optional[dict]) -> None:
        self.reference = ref
        self._data = data
        self.exists = data is not None
        self.id = ref.path.rsplit("/", 1)[-1]

    def to_dict(self) -> dict:
        return dict(self._data or {})


class FakeDocumentRef:
    def __init__(self, store: FakeFirestoreStore, path: str) -> None:
        self.store = store
        self.path = path

    def get(self, **_: Any) -> FakeSnapshot:
        if self.store.fail_with:
            raise self.store.fail_with
        data = self.store.docs.get(self.path)
        return FakeSnapshot(self, dict(data) if data is not None else None)

    def set(self, data: dict) -> None:
        self.store.docs[self.path] = dict(data)

    def delete(self) -> None:
        self.store.docs.pop(self.path, None)

    def collection(self, name: str) -> "FakeCollectionRef":
        return FakeCollectionRef(self.store, f"{self.path}/{name}")

    def collections(self) -> List["FakeCollectionRef"]:
        prefix = f"{self.path}/"
        names = sorted({p[len(prefix) :].split("/", 1)[0] for p in self.store.docs if p.startswith(prefix)})
        return [FakeCollectionRef(self.store, f"{self.path}/{n}") for n in names]


class FakeQuery:
    def __init__(self, store: FakeFirestoreStore, path: str) -> None:
        self.store, self.path = store, path
        self._filters: List[Any] = []
        self._order: Optional[tuple] = None
        self._limit: Optional[int] = None

    def _clone(self) -> "FakeQuery":
        q = FakeQuery(self.store, self.path)
        q._filters, q._order, q._limit = list(self._filters), self._order, self._limit
        return q

    def where(self, *, filter: Any) -> "FakeQuery":  # noqa: A002 - mirrors the real keyword
        q = self._clone()
        q._filters.append(filter)
        return q

    def order_by(self, field: str, direction: str = "ASCENDING") -> "FakeQuery":
        q = self._clone()
        q._order = (field, direction)
        return q

    def limit(self, n: int) -> "FakeQuery":
        q = self._clone()
        q._limit = n
        return q

    def stream(self) -> List[FakeSnapshot]:
        if self.store.fail_with:
            raise self.store.fail_with
        prefix = f"{self.path}/"
        rows = [(p, d) for p, d in self.store.docs.items() if p.startswith(prefix) and "/" not in p[len(prefix) :]]
        ops = {
            "==": lambda a, b: a == b,
            "<": lambda a, b: a < b,
        }
        for f in self._filters:
            rows = [(p, d) for p, d in rows if ops[f.op_string](d.get(f.field_path), f.value)]
        if self._order:
            field, direction = self._order
            rows.sort(key=lambda r: r[1][field], reverse=direction == "DESCENDING")
        if self._limit is not None:
            rows = rows[: self._limit]
        return [FakeSnapshot(FakeDocumentRef(self.store, p), dict(d)) for p, d in rows]


class FakeCollectionRef(FakeQuery):
    def document(self, doc_id: str) -> FakeDocumentRef:
        return FakeDocumentRef(self.store, f"{self.path}/{doc_id}")


class FakeFirestoreClient:
    def __init__(self, store: Optional[FakeFirestoreStore] = None) -> None:
        self.store = store or FakeFirestoreStore()

    def collection(self, name: str) -> FakeCollectionRef:
        return FakeCollectionRef(self.store, name)
