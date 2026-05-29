from __future__ import annotations

import json
import math
import uuid
from collections import Counter, defaultdict
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
            "format": c.format,
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
            "lang": c.lang, "difficulty": c.difficulty, "format": c.format,
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

        passage_start_char = session.get("passage_start_char")

        # Resume active word if any
        current_pos = session["current_position"]
        if current_pos is not None:
            attempts = await (await db.execute(
                "SELECT attempt_num, guess, correct FROM guesses "
                "WHERE session_id = ? AND position = ? ORDER BY attempt_num",
                (session_id, current_pos),
            )).fetchall()
            word, _sliding_ctx = C.get_word_and_context(corpus_id, current_pos)
            if mode == "sequential" and passage_start_char is not None:
                context = C.get_growing_context(corpus_id, passage_start_char, current_pos)
            else:
                context = _sliding_ctx
            return {
                "session_complete": False,
                "context": context,
                "attempt_num": len(attempts) + 1,
                "max_attempts": game.max_attempts,
                "words_done": words_done,
                "words_total": None if mode == "sequential" else game.words_per_session,
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
            new_word_idx, new_pos, _sliding_ctx, _word = seq
        else:
            played = {r[0] for r in await (await db.execute(
                "SELECT position FROM word_results WHERE session_id = ?", (session_id,)
            )).fetchall()}
            result = C.sample_position(corpus_id, played)
            if result is None:
                score = await _session_score(db, session_id)
                return {"session_complete": True, "score": score, "words_done": words_done}
            new_pos, _sliding_ctx, _word = result
            new_word_idx = C._find_word_index_by_position(corpus_id, new_pos) if mode == "sequential" else None

        # For sequential sessions, build (or extend) the growing context.
        if mode == "sequential":
            if passage_start_char is None:
                # First word of this passage: anchor at the start of its 20-word seed.
                spans = C._get_word_spans(corpus_id)
                ctx_word_idx = max(new_word_idx - game.context_words, 0)
                passage_start_char = spans[ctx_word_idx][0]
            context = C.get_growing_context(corpus_id, passage_start_char, new_pos)
        else:
            context = _sliding_ctx

        await db.execute(
            "UPDATE sessions SET current_position = ?, chain_word_idx = ?, passage_start_char = ? "
            "WHERE session_id = ?",
            (new_pos, new_word_idx, passage_start_char, session_id),
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
        letter_feedback = C.compute_letter_feedback(body.guess.strip(), word, c.normalization)
        attempt_num = attempts_used + 1

        fb_json = json.dumps([f["status"] for f in letter_feedback])
        await db.execute(
            "INSERT INTO guesses (session_id, position, attempt_num, guess, correct, letter_feedback) "
            "VALUES (?,?,?,?,?,?)",
            (body.session_id, current_pos, attempt_num, body.guess.strip(), correct, fb_json),
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
            "word_length": len(word),
            "letter_feedback": letter_feedback,
        }


@app.get("/session/stats")
async def session_stats(session_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        words_data = await _load_words_data(db, session_id=session_id)

    if not words_data:
        return {"words_count": 0, "entropy": 0.0, "g_distribution": {str(g): 0 for g in range(1, 7)}}

    g_dist = {str(g): sum(1 for w in words_data if w["g_value"] == g) for g in range(1, 7)}
    return {
        "words_count": len(words_data),
        "entropy": _entropy_bound(words_data),
        "g_distribution": g_dist,
    }


@app.get("/leaderboard")
async def get_leaderboard(corpus_id: Optional[str] = None):
    game = C.get_game_config()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if corpus_id:
            wr_rows = await (await db.execute(
                "SELECT player_id, corpus_id, g_value, target_word, session_id, position "
                "FROM word_results WHERE corpus_id = ?",
                (corpus_id,),
            )).fetchall()
        else:
            wr_rows = await (await db.execute(
                "SELECT player_id, corpus_id, g_value, target_word, session_id, position "
                "FROM word_results"
            )).fetchall()
        urows = await (await db.execute("SELECT player_id, username FROM users")).fetchall()

        # Load all feedback data once
        all_guesses = await (await db.execute(
            "SELECT session_id, position, attempt_num, letter_feedback FROM guesses "
            "ORDER BY session_id, position, attempt_num"
        )).fetchall()

    usernames = {r[0]: r[1] for r in urows}

    # Build feedback lookup: (session_id, position) -> [status_list per attempt]
    feedback_map: dict = defaultdict(list)
    for sid, pos, _, fb_json in all_guesses:
        feedback_map[(sid, pos)].append(json.loads(fb_json) if fb_json else [])

    # Group by (player, corpus)
    buckets: dict = defaultdict(list)
    for row in wr_rows:
        key = (row["player_id"], row["corpus_id"])
        fb = feedback_map.get((row["session_id"], row["position"]), [])
        buckets[key].append({
            "g_value": row["g_value"],
            "word_length": len(row["target_word"]) if row["target_word"] else 0,
            "feedbacks": fb,
        })

    entries = []
    for (player_id, cid), words_data in buckets.items():
        if len(words_data) < game.min_words_for_leaderboard:
            continue
        entries.append({
            "player_id": player_id,
            "corpus_id": cid,
            "score": _entropy_bound(words_data),
            "words_played": len(words_data),
            "is_llm": False,
            "username": usernames.get(player_id),
        })

    entries.sort(key=lambda x: x["score"])
    for i, e in enumerate(entries):
        e["rank"] = i + 1
    return entries


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _load_words_data(db: aiosqlite.Connection, session_id: str) -> list[dict]:
    """Load word results with their feedback sequences for a session."""
    db.row_factory = aiosqlite.Row
    wr_rows = await (await db.execute(
        "SELECT g_value, target_word, position FROM word_results WHERE session_id = ?",
        (session_id,),
    )).fetchall()

    g_rows = await (await db.execute(
        "SELECT position, letter_feedback FROM guesses WHERE session_id = ? "
        "ORDER BY position, attempt_num",
        (session_id,),
    )).fetchall()

    fb_by_pos: dict = defaultdict(list)
    for pos, fb_json in g_rows:
        fb_by_pos[pos].append(json.loads(fb_json) if fb_json else [])

    return [
        {
            "g_value": r["g_value"],
            "word_length": len(r["target_word"]) if r["target_word"] else 0,
            "feedbacks": fb_by_pos.get(r["position"], []),
        }
        for r in wr_rows
    ]


async def _session_score(db: aiosqlite.Connection, session_id: str) -> float:
    words_data = await _load_words_data(db, session_id)
    return _entropy_bound(words_data)


def _entropy_bound(words_data: list[dict]) -> float:
    """
    Upper bound on conditional entropy F_N using the feedback-sequence method.

    Formula (§3 of the analysis):
        F_N ≤ h(q₁) + (1−q₁)[H(ℓ|miss) + Σ_t H(F_t | F_{<t}, ℓ)]

    where q₁ = P(correct on attempt 1), ℓ = word length, F_t = Wordle feedback
    pattern at attempt t.  Marginalising over F_{<t} (i.e. estimating H(F_t|ℓ)
    instead of the conditional) gives a valid, if looser, upper bound.
    When feedback data are sparse (<3 obs per group) the per-tile maximum
    log₂3 is used as the fallback, which is always an upper bound.
    """
    n = len(words_data)
    if n == 0:
        return 0.0

    def h_bin(p: float) -> float:
        if p <= 0 or p >= 1:
            return 0.0
        return -p * math.log2(p) - (1 - p) * math.log2(1 - p)

    def h_empirical(seq) -> float:
        counts = Counter(seq)
        total = sum(counts.values())
        return -sum((c / total) * math.log2(c / total) for c in counts.values())

    # q₁: fraction of first-attempt successes
    q1 = sum(1 for w in words_data if w["g_value"] == 1) / n
    bound = h_bin(q1)

    misses = [w for w in words_data if w["g_value"] > 1]
    if not misses:
        return round(bound, 4)

    n_miss = len(misses)
    # H(ℓ | miss): entropy of word-length distribution among misses
    h_len = h_empirical([w["word_length"] for w in misses])

    # Σ_t H(F_t | ℓ): for each (length, attempt-index) group, estimate H(F_t | ℓ).
    # Sum these per group, then divide by n_miss to get the expected sum per word.
    # (The formula sums over attempts per word then averages over words.)
    groups: dict = defaultdict(list)
    for w in misses:
        ell = w["word_length"]
        for t_idx, fb in enumerate(w["feedbacks"]):
            groups[(ell, t_idx)].append(tuple(fb))

    total_fb_bits = 0.0
    for (ell, _t_idx), patterns in groups.items():
        m = len(patterns)
        if m >= 3:
            h = h_empirical(patterns)
        else:
            # Sparse data: per-tile maximum log₂3 is a valid upper bound
            h = ell * math.log2(3)
        total_fb_bits += h * m  # H × count = contribution of this group

    # Divide by n_miss to get expected Σ_t H(F_t|ℓ) per missed word
    avg_fb_per_word = total_fb_bits / n_miss
    bound += (1 - q1) * (h_len + avg_fb_per_word)
    return round(bound, 4)
