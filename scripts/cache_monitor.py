#!/usr/bin/env python3
"""Read-only, content-free cache accounting for NoReply's existing queue."""

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

COUNTERS = (
    "requests",
    "responses",
    "input_reports",
    "output_reports",
    "known_cache_responses",
    "hit_responses",
    "input_tokens",
    "cached_tokens",
    "output_tokens",
    "known_cache_input_tokens",
    "ratio_cached_tokens",
)


def decorate(row):
    row["hit_rate"] = (
        row["hit_responses"] / row["known_cache_responses"]
        if row["known_cache_responses"]
        else None
    )
    row["token_share"] = (
        row["ratio_cached_tokens"] / row["known_cache_input_tokens"]
        if row["known_cache_input_tokens"]
        else None
    )
    return row


def snapshot(path):
    if not path.is_file():
        return {"available": False, "reason": "queue_not_created"}
    try:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            db.execute("PRAGMA query_only=ON")
            db.row_factory = sqlite3.Row
            rows = [
                dict(row)
                for row in db.execute("SELECT * FROM cache_usage ORDER BY updated DESC")
            ]
        finally:
            db.close()
    except sqlite3.Error:
        return {
            "available": False,
            "reason": "stats_not_initialized_or_temporarily_unavailable",
        }
    total = {key: sum(row[key] for row in rows) for key in COUNTERS}
    for row in rows:
        row["room"] = hashlib.sha256(row["room"].encode()).hexdigest()[:12]
        decorate(row)
    return {"available": True, "total": decorate(total), "rooms": rows}


def percent(value):
    return "unknown" if value is None else f"{value:.1%}"


def render(data):
    if not data["available"]:
        return "缓存统计尚不可用；未把缺失数据记为 0。"
    lines = [
        "ROOM          REQ   RESP  CACHE_KNOWN  HIT%      TOKEN_SHARE   INPUT(known) CACHED    OUTPUT(known)"
    ]
    for row in [dict(data["total"], room="TOTAL")] + data["rooms"]:
        coverage = f"{row['known_cache_responses']}/{row['responses']}"
        lines.append(
            f"{row['room']:<13} {row['requests']:<5} {row['responses']:<5} {coverage:<12} {percent(row['hit_rate']):<9} {percent(row['token_share']):<13} {row['input_tokens']:<12} {row['cached_tokens']:<9} {row['output_tokens']}"
        )
    lines.append(
        "HIT%：已报告缓存数的响应中，cached>0 的比例；TOKEN_SHARE：同时报告输入/缓存数的样本内缓存 token 占比。"
    )
    lines.append(
        "unknown ≠ 0；输入/输出只累计已报告值；不含未返回的计费、未提供的推理 token，也不估算金额。"
    )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path.home()
        / "astrbot/data/plugin_data/astrbot_plugin_noreply/batch/queue.sqlite3",
    )
    parser.add_argument(
        "--json", action="store_true", help="machine-readable, anonymized snapshot"
    )
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()
    if not 0.25 <= args.interval <= 3600:
        parser.error("interval must be between 0.25 and 3600 seconds")
    try:
        while True:
            data = snapshot(args.db)
            if args.watch and not args.json:
                print("\033[2J\033[H", end="")
            print(
                json.dumps(data, ensure_ascii=False) if args.json else render(data),
                flush=True,
            )
            if not args.watch:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
