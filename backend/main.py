from __future__ import annotations

import math
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Optional

import aiosqlite
from fastapi import Cookie, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .db import DB_PATH, init_db
from . import corpus as C

FRONTEND     = Path(__file__).parent.parent / "frontend" / "index.html"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Entropy Game", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ── Models ────────────────────────────────────────────────────────────────────

class UserRequest(BaseModel):
    username: str


class SessionRequest(BaseModel):
    corpus_id: str
    mode: str = "random"


class GuessRequest(BaseModel):
    session_id: str
    guess: str


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def serve_frontend():
    return FileResponse(FRONTEND)


@app.get("/i18n/{lang}.json")
async def get_i18n(lang: str):
    if lang not in ("ru", "en", "fr"):
        raise HTTPException(404)
    path = FRONTEND_DIR / "i18n" / f"{lang}.json"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="application/json")


@app.post("/user")
async def identify_user(
    body: UserRequest,
    response: Response,
    player_id: Annotated[Optional[str], Cookie()] = None,
):
    username = body.username.strip()[:40]
    if not username:
        raise HTTPException(400, "Username required")

    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT player_id FROM users WHERE username = ?", (username,)
        )).fetchone()

        if row:
            player_id = row[0]
        else:
            if not player_id:
                player_id = str(uuid.uuid4())
            await db.execute(
                "INSERT OR IGNORE INTO users (player_id, username) VALUES (?,?)",
                (player_id, username),
            )
            await db.commit()

    response.set_cookie(
        "player_id", player_id, max_age=365 * 24 * 3600,
        httponly=True, samesite="lax",
    )
    return {"player_id": player_id, "username": username}


@app.get("/me")
async def get_me(player_id: Annotated[Optional[str], Cookie()] = None):
    if not player_id:
        return {"player_id": None, "username": None}
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT username FROM users WHERE player_id = ?", (player_id,)
        )).fetchone()
    return {"player_id": player_id, "username": row[0] if row else None}


@app.get("/corpora")
async def list_corpora():
    corpora = C.get_all_corpora()
    return [
        {
            "id": c.id,
            "lang": c.lang,
            "author": c.author,
            "title": c.title,
            "difficulty": c.difficulty,
            "available": C.is_corpus_available(c.id),
        }
        for c in corpora.values()
    ]


@app.post("/session")
async def create_session(
    body: SessionRequest,
    response: Response,
    player_id: Annotated[Optional[str], Cookie()] = None,
):
    corpora = C.get_all_corpora()
    if body.corpus_id not in corpora:
        raise HTTPException(404, "Unknown corpus")
    if not C.is_corpus_available(body.corpus_id):
        raise HTTPException(503, "Corpus file not yet available")

    if not player_id:
        player_id = str(uuid.uuid4())
        response.set_cookie(
            "player_id", player_id, max_age=365 * 24 * 3600,
            httponly=True, samesite="lax"
        )

    mode = body.mode if body.mode in ("random", "sequential") else "random"
    session_id = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sessions (session_id, player_id, corpus_id, mode) VALUES (?,?,?,?)",
            (session_id, player_id, body.corpus_id, mode),
        )
        await db.commit()

    c = corpora[body.corpus_id]
    return {
        "session_id": session_id,
        "player_id": player_id,
        "mode": mode,
        "corpus": {
            "id": c.id, "author": c.author, "title": c.title,
            "lang": c.lang, "difficulty": c.difficulty,
        },
    }


@app.get("/next")
async def next_word(session_id: str):
    game = C.get_game_config()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        row = await (await db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        )).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")
        session = dict(row)
        corpus_id = session["corpus_id"]

        words_done = (await (await db.execute(
            "SELECT COUNT(*) FROM word_results WHERE session_id = ?", (session_id,)
        )).fetchone())[0]

        mode = session.get("mode") or "random"

        # In sequential mode the user decides when to stop; only cap random sessions.
        if mode != "sequential" and words_done >= game.words_per_session:
            score = await _session_score(db, session_id)
            return {"session_complete": True, "score": score, "words_done": words_done}

        # Resume active word if any
        current_pos = session["current_position"]
        if current_pos is not None:
            attempts = await (await db.execute(
                "SELECT attempt_num, guess, correct FROM guesses "
                "WHERE session_id = ? AND position = ? ORDER BY attempt_num",
                (session_id, current_pos),
            )).fetchall()
            word, context = C.get_word_and_context(corpus_id, current_pos)
            return {
                "session_complete": False,
                "context": context,
                "attempt_num": len(attempts) + 1,
                "max_attempts": game.max_attempts,
                "words_done": words_done,
                "words_total": None,
                "attempts_history": [
                    {"attempt_num": a["attempt_num"], "guess": a["guess"], "correct": bool(a["correct"])}
                    for a in attempts
                ],
            }

        chain_word_idx = session.get("chain_word_idx")

        if mode == "sequential" and chain_word_idx is not None:
            seq = C.next_sequential_word(corpus_id, chain_word_idx)
            if seq is None:
                score = await _session_score(db, session_id)
                return {"session_complete": True, "score": score, "words_done": words_done}
            new_word_idx, new_pos, context, _word = seq
        else:
            played = {r[0] for r in await (await db.execute(
                "SELECT position FROM word_results WHERE session_id = ?", (session_id,)
            )).fetchall()}
            result = C.sample_position(corpus_id, played)
            if result is None:
                score = await _session_score(db, session_id)
                return {"session_complete": True, "score": score, "words_done": words_done}
            new_pos, context, _word = result
            new_word_idx = C._find_word_index_by_position(corpus_id, new_pos) if mode == "sequential" else None

        await db.execute(
            "UPDATE sessions SET current_position = ?, chain_word_idx = ? WHERE session_id = ?",
            (new_pos, new_word_idx, session_id),
        )
        await db.commit()

        return {
            "session_complete": False,
            "context": context,
            "attempt_num": 1,
            "max_attempts": game.max_attempts,
            "words_done": words_done,
            "words_total": None if mode == "sequential" else game.words_per_session,
            "attempts_history": [],
        }


@app.post("/guess")
async def submit_guess(body: GuessRequest):
    game = C.get_game_config()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        row = await (await db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (body.session_id,)
        )).fetchone()
        if not row:
            raise HTTPException(404, "Session not found")
        session = dict(row)

        current_pos = session["current_position"]
        if current_pos is None:
            raise HTTPException(400, "No active word — call /next first")

        corpus_id = session["corpus_id"]
        c = C.get_corpus_config(corpus_id)

        attempts_used = (await (await db.execute(
            "SELECT COUNT(*) FROM guesses WHERE session_id = ? AND position = ?",
            (body.session_id, current_pos),
        )).fetchone())[0]

        if attempts_used >= game.max_attempts:
            raise HTTPException(400, "Word already resolved")

        word, context_text = C.get_word_and_context(corpus_id, current_pos)
        correct = C.verify_guess(body.guess, word, c.normalization)
        attempt_num = attempts_used + 1

        await db.execute(
            "INSERT INTO guesses (session_id, position, attempt_num, guess, correct) "
            "VALUES (?,?,?,?,?)",
            (body.session_id, current_pos, attempt_num, body.guess.strip(), correct),
        )

        word_resolved = correct or (attempt_num >= game.max_attempts)
        g_value = None

        if word_resolved:
            g_value = attempt_num if correct else game.fail_penalty_g
            await db.execute(
                "INSERT INTO word_results "
                "(session_id, player_id, corpus_id, position, g_value, context_text, target_word) "
                "VALUES (?,?,?,?,?,?,?)",
                (body.session_id, session["player_id"], corpus_id, current_pos,
                 g_value, context_text, word),
            )
            await db.execute(
                "UPDATE sessions SET current_position = NULL WHERE session_id = ?",
                (body.session_id,),
            )

        await db.commit()

        return {
            "correct": correct,
            "attempt_num": attempt_num,
            "attempts_left": game.max_attempts - attempt_num,
            "word_resolved": word_resolved,
            "g_value": g_value,
            "target_word": word if word_resolved else None,
        }


@app.get("/session/stats")
async def session_stats(session_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute(
            "SELECT g_value FROM word_results WHERE session_id = ? ORDER BY rowid",
            (session_id,),
        )).fetchall()

    g_values = [r[0] for r in rows]
    if not g_values:
        return {"words_count": 0, "entropy": 0.0, "g_distribution": {str(g): 0 for g in range(1, 7)}}

    entropy = sum(math.log2(g) for g in g_values) / len(g_values)
    g_dist = {str(g): sum(1 for v in g_values if v == g) for g in range(1, 7)}
    return {
        "words_count": len(g_values),
        "entropy": round(entropy, 4),
        "g_distribution": g_dist,
    }


@app.get("/leaderboard")
async def get_leaderboard(corpus_id: Optional[str] = None):
    game = C.get_game_config()

    from collections import defaultdict
    async with aiosqlite.connect(DB_PATH) as db:
        if corpus_id:
            rows = await (await db.execute(
                "SELECT player_id, corpus_id, g_value FROM word_results WHERE corpus_id = ?",
                (corpus_id,),
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT player_id, corpus_id, g_value FROM word_results"
            )).fetchall()
        urows = await (await db.execute("SELECT player_id, username FROM users")).fetchall()
    usernames = {r[0]: r[1] for r in urows}
    buckets: dict = defaultdict(list)
    for player_id, cid, g in rows:
        buckets[(player_id, cid)].append(g)

    entries = []
    for (player_id, cid), g_values in buckets.items():
        if len(g_values) < game.min_words_for_leaderboard:
            continue
        score = sum(math.log2(g) for g in g_values) / len(g_values)
        entries.append({
            "player_id": player_id,
            "corpus_id": cid,
            "score": round(score, 4),
            "words_played": len(g_values),
            "is_llm": False,
            "username": usernames.get(player_id),
        })

    entries.sort(key=lambda x: x["score"])
    for i, e in enumerate(entries):
        e["rank"] = i + 1
    return entries


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _session_score(db: aiosqlite.Connection, session_id: str) -> float:
    rows = await (await db.execute(
        "SELECT g_value FROM word_results WHERE session_id = ?", (session_id,)
    )).fetchall()
    if not rows:
        return 0.0
    return round(sum(math.log2(r[0]) for r in rows) / len(rows), 4)
