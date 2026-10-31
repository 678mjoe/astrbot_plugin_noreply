"""Private SQLite ledger. Pending input is never evicted with background."""

import hashlib
import json
import math
import os
import sqlite3
import uuid
from dataclasses import asdict
from pathlib import Path

from .batch_protocol import Judgment
from .engine import GroupKey, Message, _bounded_identity


class StaleBatch(RuntimeError):
    pass


class BatchStore:
    def __init__(self, directory: Path, *, window: int = 300):
        directory = Path(directory)
        if directory.is_symlink():
            raise ValueError("queue_directory_symlink")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        path = directory / "queue.sqlite3"
        if any(
            Path(str(path) + suffix).is_symlink() for suffix in ("", "-wal", "-shm")
        ):
            raise ValueError("queue_symlink")
        if not path.exists():
            with path.open("xb") as stream:
                os.fchmod(stream.fileno(), 0o600)
        path.chmod(0o600)
        self.window = window
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS rooms (
            room TEXT PRIMARY KEY, origin TEXT NOT NULL, transport TEXT NOT NULL,
            epoch INTEGER NOT NULL DEFAULT 0, last_attempt REAL NOT NULL DEFAULT 0,
            last_reply REAL NOT NULL DEFAULT 0, judge_after REAL NOT NULL DEFAULT 0,
            failures INTEGER NOT NULL DEFAULT 0, adaptive INTEGER NOT NULL DEFAULT 0,
            send_after REAL NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT NOT NULL,
            dedup TEXT NOT NULL, payload TEXT, eligible INTEGER NOT NULL,
            state TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0, reply INTEGER, reply_text TEXT NOT NULL DEFAULT '',
            send_state TEXT NOT NULL DEFAULT 'none', receipt TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
            retry_after REAL NOT NULL DEFAULT 0, UNIQUE(room, dedup)
        );
        CREATE INDEX IF NOT EXISTS items_room_state ON items(room,state,id);
        CREATE INDEX IF NOT EXISTS items_room_send ON items(room,send_state,id);
        CREATE INDEX IF NOT EXISTS items_live_context ON items(room,id) WHERE payload IS NOT NULL;
        CREATE INDEX IF NOT EXISTS items_live_reply ON items(room,id) WHERE reply_text!='';
        CREATE INDEX IF NOT EXISTS items_retry ON items(room,retry_after,id) WHERE state='pending';
        CREATE INDEX IF NOT EXISTS items_send_work ON items(room,id) WHERE send_state='pending';
        """)
        # A crash after dispatch cannot establish whether the remote accepted it.
        with self.db:
            self.db.execute(
                "UPDATE items SET send_state='unknown',error='interrupted_send' WHERE send_state='sending'"
            )

    @staticmethod
    def key(key: GroupKey) -> str:
        return json.dumps(
            [key.platform_id, key.self_id, key.group_id], separators=(",", ":")
        )

    @staticmethod
    def group(room: str) -> GroupKey:
        return GroupKey(*json.loads(room))

    def _room(self, key: GroupKey, origin: str, transport: dict):
        room = self.key(key)
        self.db.execute(
            "INSERT INTO rooms(room,origin,transport) VALUES(?,?,?) ON CONFLICT(room) DO UPDATE SET origin=excluded.origin,transport=excluded.transport",
            (room, origin, json.dumps(transport, separators=(",", ":"))),
        )
        return room

    def _payload(self, message: Message) -> str:
        data = asdict(message)
        data["sender_id"] = _bounded_identity(message.sender_id)
        data["display_name"] = message.display_name[:80]
        data["timestamp"] = (
            message.timestamp if math.isfinite(message.timestamp) else 0.0
        )
        data["message_id"] = (
            hashlib.sha256(message.message_id.encode()).hexdigest()
            if message.message_id
            else ""
        )
        return json.dumps(
            data, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )

    def enqueue(self, key: GroupKey, origin: str, transport: dict, message: Message):
        dedup = (
            hashlib.sha256(message.message_id.encode()).hexdigest()
            if message.message_id
            else uuid.uuid4().hex
        )
        with self.db:
            room = self._room(key, origin, transport)
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO items(room,dedup,payload,eligible,state,generation) VALUES(?,?,?,1,'pending',?)",
                (room, dedup, self._payload(message), self.room(room)["epoch"]),
            )
            self._prune(room)
            return cursor.lastrowid if cursor.rowcount else None

    def observe(self, key: GroupKey, origin: str, transport: dict, message: Message):
        with self.db:
            room = self._room(key, origin, transport)
            self.db.execute(
                "INSERT OR IGNORE INTO items(room,dedup,payload,eligible,state) VALUES(?,?,?,0,'background')",
                (room, message.message_id or uuid.uuid4().hex, self._payload(message)),
            )
            self._prune(room)

    def _prune(self, room):
        # Retain latest N context records plus ALL pending/uncertain work.
        threshold = self.db.execute(
            "SELECT id FROM items WHERE room=? AND payload IS NOT NULL ORDER BY id DESC LIMIT 1 OFFSET ?",
            (room, self.window - 1),
        ).fetchone()
        if threshold:
            self.db.execute(
                "UPDATE items SET payload=NULL WHERE room=? AND id<? AND state!='pending' AND payload IS NOT NULL",
                (room, threshold[0]),
            )
            self.db.execute(
                "UPDATE items SET reply_text='' WHERE room=? AND id<? AND reply_text!='' AND send_state IN ('accepted','shadow','cancelled','none')",
                (room, threshold[0]),
            )

    def room(self, room):
        row = self.db.execute("SELECT * FROM rooms WHERE room=?", (room,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["key"] = self.group(room)
        data["transport"] = json.loads(data["transport"])
        return data

    def pending(self, room, limit, *, ready_at=None):
        query = "SELECT id,payload FROM items WHERE room=? AND state='pending'"
        params = [room]
        if ready_at is not None:
            query += " AND retry_after<=?"
            params.append(ready_at)
        rows = self.db.execute(
            query + " ORDER BY id LIMIT ?", (*params, limit)
        ).fetchall()
        return [{"id": row["id"], **json.loads(row["payload"])} for row in rows]

    def earliest_retry(self, room):
        return self.db.execute(
            "SELECT min(retry_after) FROM items WHERE room=? AND state='pending'",
            (room,),
        ).fetchone()[0]

    def background(self, room, limit, exclude=()):
        rows = self.db.execute(
            "SELECT id,payload FROM items WHERE room=? AND payload IS NOT NULL ORDER BY id DESC LIMIT ?",
            (room, limit),
        ).fetchall()
        return [
            {"id": row["id"], **json.loads(row["payload"])}
            for row in reversed(rows)
            if row["id"] not in exclude
        ]

    def attempt(self, room, now):
        with self.db:
            self.db.execute("UPDATE rooms SET last_attempt=? WHERE room=?", (now, room))

    def defer(self, room, now, ids, base, category):
        with self.db:
            for id in ids:
                row = self.db.execute(
                    "SELECT attempts FROM items WHERE id=? AND room=? AND state='pending'",
                    (id, room),
                ).fetchone()
                if row is not None:
                    attempts = row[0] + 1
                    delay = min(60.0, base * 2 ** min(attempts - 1, 6))
                    self.db.execute(
                        "UPDATE items SET attempts=?,retry_after=?,error=? WHERE id=?",
                        (attempts, now + delay, category[:80], id),
                    )
            self.db.execute(
                "UPDATE rooms SET failures=failures+1,adaptive=?,last_error=? WHERE room=?",
                (max(1, len(ids) // 2), category[:80], room),
            )

    def accept(
        self,
        room,
        expected,
        decisions: tuple[Judgment, ...],
        epoch,
        *,
        shadow=False,
        suppress=False,
    ):
        if tuple(d.id for d in decisions) != tuple(expected):
            raise ValueError("batch_id_mismatch")
        with self.db:
            if any(
                self.db.execute(
                    "SELECT 1 FROM items WHERE id=? AND room=? AND state='pending'",
                    (id, room),
                ).fetchone()
                is None
                for id in expected
            ):
                raise StaleBatch("pending_changed")
            stale = self.room(room)["epoch"] != epoch or suppress
            for decision in decisions:
                generation = self.db.execute(
                    "SELECT generation FROM items WHERE id=?", (decision.id,)
                ).fetchone()[0]
                old_generation = generation != self.room(room)["epoch"]
                send_state = (
                    "none"
                    if not decision.reply
                    else (
                        "cancelled"
                        if stale or old_generation
                        else "shadow"
                        if shadow
                        else "pending"
                    )
                )
                self.db.execute(
                    "UPDATE items SET state='judged',reply=?,reply_text=?,send_state=? WHERE id=?",
                    (int(decision.reply), decision.text, send_state, decision.id),
                )
            self.db.execute(
                "UPDATE rooms SET failures=0,adaptive=0,judge_after=0,last_error='' WHERE room=?",
                (room,),
            )
            self._prune(room)

    def next_send(self, room):
        row = self.db.execute(
            "SELECT * FROM items WHERE room=? AND reply=1 AND send_state IN ('pending','sending','unknown') ORDER BY id LIMIT 1",
            (room,),
        ).fetchone()
        if row is None or row["send_state"] != "pending":
            return None
        older = self.db.execute(
            "SELECT 1 FROM items WHERE room=? AND state='pending' AND id<? LIMIT 1",
            (room, row["id"]),
        ).fetchone()
        return None if older else dict(row)

    def work_rooms(self):
        rows = self.db.execute(
            "SELECT room FROM items WHERE state='pending' UNION SELECT room FROM items WHERE send_state='pending'"
        ).fetchall()
        return [
            self.room(row[0])
            for row in rows
            if self.pending(row[0], 1) or self.next_send(row[0])
        ]

    def defer_send(self, room, until):
        with self.db:
            self.db.execute("UPDATE rooms SET send_after=? WHERE room=?", (until, room))

    def claim(self, id):
        with self.db:
            return (
                self.db.execute(
                    "UPDATE items SET send_state='sending' WHERE id=? AND send_state='pending'",
                    (id,),
                ).rowcount
                == 1
            )

    def not_ready(self, id):
        with self.db:
            self.db.execute(
                "UPDATE items SET send_state='pending' WHERE id=? AND send_state='sending'",
                (id,),
            )

    def unknown(self, id, category):
        with self.db:
            self.db.execute(
                "UPDATE items SET send_state='unknown',error=? WHERE id=? AND send_state='sending'",
                (category[:80], id),
            )

    def sent(self, id, receipt, now):
        with self.db:
            row = self.db.execute(
                "SELECT * FROM items WHERE id=? AND send_state='sending'", (id,)
            ).fetchone()
            if row is None:
                raise StaleBatch("send_changed")
            self.db.execute(
                "UPDATE items SET send_state='accepted',receipt=?,error='' WHERE id=?",
                (receipt[:256], id),
            )
            self.db.execute(
                "UPDATE rooms SET last_reply=? WHERE room=?", (now, row["room"])
            )
            key = self.group(row["room"])
            msg = Message(
                "assistant",
                key.self_id,
                "bot",
                now,
                "sent:" + str(id),
                row["reply_text"],
                is_bot=True,
                is_admin=None,
            )
            self.db.execute(
                "INSERT OR IGNORE INTO items(room,dedup,payload,eligible,state) VALUES(?,?,?,0,'background')",
                (row["room"], "sent:" + str(id), self._payload(msg)),
            )
            self._prune(row["room"])

    def invalidate(self, room=None, *, clear=False):
        rooms = (
            [room]
            if room is not None
            else [row[0] for row in self.db.execute("SELECT room FROM rooms")]
        )
        with self.db:
            for key in rooms:
                self.db.execute("UPDATE rooms SET epoch=epoch+1 WHERE room=?", (key,))
                self.db.execute(
                    "UPDATE items SET send_state='cancelled' WHERE room=? AND send_state='pending'",
                    (key,),
                )
                if clear:
                    self.db.execute(
                        "UPDATE items SET send_state='cancelled' WHERE room=? AND send_state='unknown'",
                        (key,),
                    )
                    self.db.execute(
                        "UPDATE items SET state='cancelled',error='explicit_clear' WHERE room=? AND state='pending'",
                        (key,),
                    )
                    self.db.execute(
                        "UPDATE items SET payload=NULL WHERE room=?", (key,)
                    )

    def counts(self, room=None):
        where, params = (" WHERE room=?", (room,)) if room is not None else ("", ())
        row = self.db.execute(
            "SELECT count(CASE WHEN eligible=1 THEN 1 END) AS received,count(CASE WHEN state='pending' THEN 1 END) AS pending,count(CASE WHEN state='judged' THEN 1 END) AS judged,count(CASE WHEN state='cancelled' THEN 1 END) AS cancelled,count(CASE WHEN send_state='pending' THEN 1 END) AS send_pending,count(CASE WHEN send_state='accepted' THEN 1 END) AS accepted,count(CASE WHEN send_state='unknown' THEN 1 END) AS unknown FROM items"
            + where,
            params,
        ).fetchone()
        return dict(row)

    def close(self):
        self.db.close()
