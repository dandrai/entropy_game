from __future__ import annotations

import aiosqlite
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "entropy_game.db"

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS users (
    player_id  TEXT PRIMARY KEY,
    username   TEXT UNIQUE NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id       TEXT PRIMARY KEY,
    player_id        TEXT NOT NULL,
    corpus_id        TEXT NOT NULL,
    started_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    current_position INTEGER,
    mode             TEXT DEFAULT 'random',
    chain_word_idx   INTEGER
);

CREATE TABLE IF NOT EXISTS guesses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    position    INTEGER NOT NULL,
    attempt_num INTEGER NOT NULL,
    guess       TEXT NOT NULL,
    correct     BOOLEAN NOT NULL,
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS word_results (
    session_id TEXT NOT NULL,
    player_id  TEXT NOT NULL,
    corpus_id  TEXT NOT NULL,
    position   INTEGER NOT NULL,
    g_value    INTEGER NOT NULL,
    timestamp  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS llm_scores (
    model_id    TEXT NOT NULL,
    corpus_id   TEXT NOT NULL,
    position    INTEGER NOT NULL,
    g_value     INTEGER NOT NULL,
    exact_log2p REAL,
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

# Applied once on existing DBs; silently ignored if column already exists.
_MIGRATIONS = [
    "ALTER TABLE sessions ADD COLUMN current_position INTEGER",
    "ALTER TABLE word_results ADD COLUMN session_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE word_results ADD COLUMN context_text TEXT DEFAULT ''",
    "ALTER TABLE word_results ADD COLUMN target_word TEXT DEFAULT ''",
    "ALTER TABLE sessions ADD COLUMN mode TEXT DEFAULT 'random'",
    "ALTER TABLE sessions ADD COLUMN chain_word_idx INTEGER",
]


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_CREATE_TABLES)
        for sql in _MIGRATIONS:
            try:
                await db.execute(sql)
            except Exception:
                pass
        await db.commit()
