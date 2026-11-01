"""Private SQLite ledger. Pending input is never evicted with background."""

import hashlib
import json
import math
import os
import sqlite3
import uuid
from dataclasses import asdict
from pathlib import Path

from .batch_protocol import InvalidBatch, Judgment
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
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS rooms (
            room TEXT PRIMARY KEY, origin TEXT NOT NULL, transport TEXT NOT NULL,
            epoch INTEGER NOT NULL DEFAULT 0, last_attempt REAL NOT NULL DEFAULT 0,
            last_reply REAL NOT NULL DEFAULT 0, judge_after REAL NOT NULL DEFAULT 0,
            failures INTEGER NOT NULL DEFAULT 0, adaptive INTEGER NOT NULL DEFAULT 0,
            send_after REAL NOT NULL DEFAULT 0,
            send_failures INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS reset_dedup (
            room TEXT NOT NULL, dedup TEXT NOT NULL, PRIMARY KEY(room,dedup)
        );
        CREATE TABLE IF NOT EXISTS cache_usage (
            room TEXT PRIMARY KEY, requests INTEGER NOT NULL DEFAULT 0,
            responses INTEGER NOT NULL DEFAULT 0, input_reports INTEGER NOT NULL DEFAULT 0,
            output_reports INTEGER NOT NULL DEFAULT 0, known_cache_responses INTEGER NOT NULL DEFAULT 0,
            hit_responses INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
            known_cache_input_tokens INTEGER NOT NULL DEFAULT 0,
            ratio_cached_tokens INTEGER NOT NULL DEFAULT 0,
            last_input_tokens INTEGER, last_cached_tokens INTEGER, last_output_tokens INTEGER,
            updated REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT NOT NULL,
            dedup TEXT NOT NULL, payload TEXT, eligible INTEGER NOT NULL,
            state TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0, reply INTEGER, reply_text TEXT NOT NULL DEFAULT '',
            send_state TEXT NOT NULL DEFAULT 'none', receipt TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
            retry_after REAL NOT NULL DEFAULT 0, must_reply INTEGER NOT NULL DEFAULT 0, UNIQUE(room, dedup)
        );
        CREATE INDEX IF NOT EXISTS items_room_state ON items(room,state,id);
        CREATE INDEX IF NOT EXISTS items_room_send ON items(room,send_state,id);
        CREATE INDEX IF NOT EXISTS items_live_context ON items(room,id) WHERE payload IS NOT NULL;
        CREATE INDEX IF NOT EXISTS items_live_reply ON items(room,id) WHERE reply_text!='';
        CREATE INDEX IF NOT EXISTS items_retry ON items(room,retry_after,id) WHERE state='pending';
        CREATE INDEX IF NOT EXISTS items_send_work ON items(room,id) WHERE send_state='pending';
        """)
        # Additive migration: old queue rows retain their original optional status.
        if "must_reply" not in {
            row[1] for row in self.db.execute("PRAGMA table_info(items)")
        }:
            with self.db:
                self.db.execute(
                    "ALTER TABLE items ADD COLUMN must_reply INTEGER NOT NULL DEFAULT 0"
                )
        if "send_failures" not in {
            row[1] for row in self.db.execute("PRAGMA table_info(rooms)")
        }:
            with self.db:
                self.db.execute(
                    "ALTER TABLE rooms ADD COLUMN send_failures INTEGER NOT NULL DEFAULT 0"
                )
        # A crash after dispatch cannot establish whether the remote accepted it.
        with self.db:
            self.db.execute(
                "UPDATE items SET send_state='unknown',error='interrupted_send' WHERE send_state='sending'"
            )

    def record_cache_request(self, room):
        with self.db:
            self.db.execute(
                "INSERT INTO cache_usage(room,requests) VALUES(?,1) "
                "ON CONFLICT(room) DO UPDATE SET requests=requests+1",
                (room,),
            )

    def record_cache_usage(self, room, usage, now):
        inputs, cached, output = (
            usage[k] for k in ("input_tokens", "cached_tokens", "output_tokens")
        )
        comparable = inputs is not None and cached is not None
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO cache_usage(room) VALUES(?)", (room,)
            )
            self.db.execute(
                "UPDATE cache_usage SET responses=responses+1,input_reports=input_reports+?,"
                "output_reports=output_reports+?,known_cache_responses=known_cache_responses+?,"
                "hit_responses=hit_responses+?,input_tokens=input_tokens+?,cached_tokens=cached_tokens+?,"
                "output_tokens=output_tokens+?,known_cache_input_tokens=known_cache_input_tokens+?,"
                "ratio_cached_tokens=ratio_cached_tokens+?,last_input_tokens=?,last_cached_tokens=?,"
                "last_output_tokens=?,updated=? WHERE room=?",
                (
                    int(inputs is not None),
                    int(output is not None),
                    int(cached is not None),
                    int((cached or 0) > 0),
                    inputs or 0,
                    cached or 0,
                    output or 0,
                    inputs if comparable else 0,
                    cached if comparable else 0,
                    inputs,
                    cached,
                    output,
                    now,
                    room,
                ),
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

    def enqueue(
        self,
        key: GroupKey,
        origin: str,
        transport: dict,
        message: Message,
        *,
        must_reply: bool = False,
    ):
        dedup = (
            hashlib.sha256(message.message_id.encode()).hexdigest()
            if message.message_id
            else uuid.uuid4().hex
        )
        with self.db:
            if self.db.execute(
                "SELECT 1 FROM reset_dedup WHERE room=? AND dedup=?",
                (self.key(key), dedup),
            ).fetchone():
                return None
            room = self._room(key, origin, transport)
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO items(room,dedup,payload,eligible,state,generation,must_reply) VALUES(?,?,?,1,'pending',?,?)",
                (
                    room,
                    dedup,
                    self._payload(message),
                    self.room(room)["epoch"],
                    int(must_reply),
                ),
            )
            result = cursor.lastrowid if cursor.rowcount else None
            if not cursor.rowcount and must_reply:
                previous = self.db.execute(
                    "SELECT * FROM items WHERE room=? AND dedup=?", (room, dedup)
                ).fetchone()
                if (
                    previous
                    and previous["eligible"]
                    and not previous["must_reply"]
                    and previous["generation"] == self.room(room)["epoch"]
                    and (
                        previous["state"] == "pending"
                        or (previous["state"] == "judged" and previous["reply"] == 0)
                    )
                ):
                    # Some adapters deliver ordinary and @ metadata for the same
                    # platform ID. Upgrade once, never duplicate a prior reply or
                    # revive explicitly cleared/invalidated work.
                    self.db.execute(
                        "UPDATE items SET must_reply=1,payload=?,state='pending',reply=NULL,reply_text='',send_state='none',retry_after=0 WHERE id=?",
                        (self._payload(message), previous["id"]),
                    )
                    result = previous["id"]
            self._prune(room)
            return result

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
                "UPDATE items SET reply_text='' WHERE room=? AND id<? AND reply_text!='' AND send_state IN ('accepted','shadow','cancelled','none','failed','unknown')",
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
        query = (
            "SELECT id,payload,must_reply FROM items WHERE room=? AND state='pending'"
        )
        params = [room]
        if ready_at is not None:
            query += " AND retry_after<=?"
            params.append(ready_at)
        rows = self.db.execute(
            query + " ORDER BY id LIMIT ?", (*params, limit)
        ).fetchall()
        return [
            {
                "id": row["id"],
                **json.loads(row["payload"]),
                "must_reply": bool(row["must_reply"]),
            }
            for row in rows
        ]

    def earliest_retry(self, room):
        return self.db.execute(
            "SELECT min(retry_after) FROM items WHERE room=? AND state='pending'",
            (room,),
        ).fetchone()[0]

    def background(self, room, limit, exclude=()):
        rows = self.db.execute(
            "SELECT id,payload,must_reply,state FROM items WHERE room=? AND payload IS NOT NULL ORDER BY id DESC LIMIT ?",
            (room, limit),
        ).fetchall()
        return [
            {
                "id": row["id"],
                **json.loads(row["payload"]),
                "must_reply": bool(row["must_reply"]),
                "_cacheable": row["state"] != "pending",
            }
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
            for decision in decisions:
                required = self.db.execute(
                    "SELECT must_reply FROM items WHERE id=?", (decision.id,)
                ).fetchone()[0]
                if required and (not decision.reply or not decision.text.strip()):
                    raise InvalidBatch("required_reply_missing")
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
        # Ready replies retain ID order. Failed/uncertain or not-yet-judged
        # items are not send barriers and are never silently replayed.
        row = self.db.execute(
            "SELECT * FROM items WHERE room=? AND reply=1 AND send_state='pending' ORDER BY id LIMIT 1",
            (room,),
        ).fetchone()
        return dict(row) if row is not None else None

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

    def backoff_send(self, room, now, base):
        current = self.room(room)
        delay = min(60.0, base * 2 ** min(current["send_failures"], 6))
        with self.db:
            self.db.execute(
                "UPDATE rooms SET send_after=?,send_failures=send_failures+1 WHERE room=?",
                (now + delay, room),
            )

    def failed(self, id, category):
        with self.db:
            self.db.execute(
                "UPDATE items SET send_state='failed',error=? WHERE id=? AND send_state='sending'",
                (category[:80], id),
            )

    def claim(self, id):
        with self.db:
            return (
                self.db.execute(
                    "UPDATE items SET send_state='sending' WHERE id=? AND send_state='pending'",
                    (id,),
                ).rowcount
                == 1
            )

    def not_ready(self, id, category=""):
        with self.db:
            self.db.execute(
                "UPDATE items SET send_state='pending',error=CASE WHEN ?='' THEN error ELSE ? END WHERE id=? AND send_state='sending'",
                (category[:80], category[:80], id),
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
                return False
            self.db.execute(
                "UPDATE items SET send_state='accepted',receipt=?,error='' WHERE id=?",
                (receipt[:256], id),
            )
            if row["generation"] != self.room(row["room"])["epoch"]:
                return True
            self.db.execute(
                "UPDATE rooms SET last_reply=?,send_failures=0,send_after=0 WHERE room=?",
                (now, row["room"]),
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
            return True

    def purge(self, room):
        """Delete one room's queue/body rows; retain only epoch/dedup fences.

        Aggregate token accounting, diagnostics and external backups are not
        conversation contents and are outside this operation's scope.
        """
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO reset_dedup(room,dedup) "
                "SELECT room,dedup FROM items WHERE room=? AND eligible=1",
                (room,),
            )
            count = self.db.execute("DELETE FROM items WHERE room=?", (room,)).rowcount
            self.db.execute(
                "UPDATE rooms SET epoch=epoch+1,origin='',transport='{}',"
                "last_attempt=0,last_reply=0,judge_after=0,failures=0,adaptive=0,"
                "send_after=0,send_failures=0,last_error='' WHERE room=?",
                (room,),
            )
        return count

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
            "SELECT count(CASE WHEN eligible=1 THEN 1 END) AS received,count(CASE WHEN state='pending' THEN 1 END) AS pending,count(CASE WHEN state='judged' THEN 1 END) AS judged,count(CASE WHEN state='cancelled' THEN 1 END) AS cancelled,count(CASE WHEN send_state='pending' THEN 1 END) AS send_pending,count(CASE WHEN send_state='accepted' THEN 1 END) AS accepted,count(CASE WHEN send_state='unknown' THEN 1 END) AS unknown,count(CASE WHEN send_state='failed' THEN 1 END) AS failed FROM items"
            + where,
            params,
        ).fetchone()
        return dict(row)

    def close(self):
        self.db.close()
