"""File-per-item queue: writing a JSON file into queue/ IS banking an item,
so any Claude session can enqueue without going through the CLI."""
from __future__ import annotations
import json
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

_TYPE_PRIORITY = {"ask": 0, "images": 1, "review": 2, "cmd": 2, "backfill": 3}


@dataclass
class Item:
    id: str
    type: str
    banked: bool
    priority: int
    payload: dict
    created: str
    expires: str | None = None
    attempts: int = 0
    max_attempts: int = 2
    # "HH:MM" of the DIEM day. Before it the drain does not see the item at all: work that
    # should only soak up leftovers (see drain._due). None = eligible at every checkpoint.
    not_before: str | None = None

    def dedupe_key(self) -> str:
        p = self.payload
        if self.type == "review":
            return f"review:{p.get('repo')}:{'diff' if p.get('diff') else p.get('range')}"
        if self.type == "images":
            return f"images:{p.get('repo')}"
        if self.type == "ask":
            return f"ask:{p.get('question')}"
        if self.type == "cmd":
            return f"cmd:{p.get('name')}"
        return f"{self.type}:{self.id}"  # backfill chunks never dedupe

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1)

    @classmethod
    def from_json(cls, text: str) -> "Item":
        return cls(**json.loads(text))


def new_item(type: str, payload: dict, *, banked: bool = False,
             expires: str | None = None, created: str,
             max_attempts: int = 2, not_before: str | None = None) -> Item:
    return Item(id=uuid.uuid4().hex, type=type, banked=banked,
                priority=_TYPE_PRIORITY.get(type, 5), payload=payload,
                created=created, expires=expires, max_attempts=max_attempts,
                not_before=not_before)


class QueueDir:
    def __init__(self, root: Path):
        self.qdir = Path(root) / "queue"
        self.adir = Path(root) / "archive"
        self.qdir.mkdir(parents=True, exist_ok=True)
        self.adir.mkdir(parents=True, exist_ok=True)

    def _load_all(self) -> list[Item]:
        items = []
        for f in sorted(self.qdir.glob("*.json")):
            try:
                items.append(Item.from_json(f.read_text()))
            except (json.JSONDecodeError, TypeError):
                f.rename(self.adir / f"corrupt-{f.name}")
        return items

    def add(self, item: Item) -> bool:
        keys = {i.dedupe_key() for i in self._load_all()}
        if item.dedupe_key() in keys:
            return False
        (self.qdir / f"{item.id}.json").write_text(item.to_json())
        return True

    def pending(self, now_iso: str) -> list[Item]:
        live = []
        for it in self._load_all():
            if it.expires and it.expires <= now_iso:
                self.archive(it, {"ok": False, "error": "expired"})
            else:
                live.append(it)
        return sorted(live, key=lambda i: (not i.banked, i.priority, i.created))

    def archive(self, item: Item, outcome: dict) -> None:
        rec = json.loads(item.to_json())
        rec["outcome"] = outcome
        (self.adir / f"{item.id}.json").write_text(json.dumps(rec, indent=1))
        (self.qdir / f"{item.id}.json").unlink(missing_ok=True)

    def remove(self, item_id: str) -> bool:
        p = self.qdir / f"{item_id}.json"
        if p.exists():
            p.unlink()
            return True
        return False

    def requeue(self, item: Item) -> None:
        (self.qdir / f"{item.id}.json").write_text(item.to_json())

    def night_count(self, type_: str, since_iso: str) -> int:
        n = 0
        for d in (self.qdir, self.adir):
            for f in d.glob("*.json"):
                try:
                    rec = json.loads(f.read_text())
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(rec, dict):
                    continue
                if rec.get("type") == type_ and rec.get("created", "") >= since_iso:
                    n += 1
        return n

    def archived_keys_since(self, since_iso: str) -> set[str]:
        keys = set()
        for f in self.adir.glob("*.json"):
            try:
                rec = json.loads(f.read_text())
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(rec, dict):
                continue
            try:
                rec.pop("outcome", None)
                item = Item(**rec)
            except TypeError:
                continue
            if item.created >= since_iso:
                keys.add(item.dedupe_key())
        return keys
