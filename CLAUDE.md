# CLAUDE.md — Entropy Game (Kolmogorov's Guessing Game)

## What this project is

A web game in which players guess the next word of a literary text, one attempt at a time (max 5), producing data that computes an upper bound on the conditional entropy of literary language — following the methodology described in Kolmogorov (1965), "Three Approaches to the Definition of the Notion of Amount of Information".

This is a **single-developer personal project**, non-commercial, non-monetized. Optimize for clarity and correctness, not scale.

---

## Architecture overview

```
entropy_game/
├── backend/           # FastAPI (Python)
│   ├── main.py        # routes: /session, /guess, /next, /leaderboard
│   ├── corpus.py      # text loading, position sampling, context extraction
│   ├── scoring.py     # entropy computation, per-player score
│   └── db.py          # SQLite: sessions, guesses, players, llm_scores
├── frontend/          # HTML + CSS + JS vanilla (no framework)
│   └── index.html     # single page
├── data/
│   ├── ru/
│   │   └── war_and_peace.txt          # Gutenberg Russian UTF-8
│   ├── en/
│   │   ├── austen_pride_prejudice.txt # Public domain
│   │   └── woolf_mrs_dalloway.txt     # Public domain
│   └── fr/
│       ├── houellebecq_particules.txt # Copyright — fair use assumed (non-commercial, short extracts)
│       └── levy_...txt                # Same
├── llm_runner/
│   └── run_llms.py    # offline script: runs configured LLMs on same task, stores scores
├── analysis/
│   └── entropy.py     # offline: computes H(G) from collected data
└── config.yaml        # corpus definitions, game parameters
```

The pipeline is **idempotent**: safe to restart at any time. Positions already played are logged and not resampled for the same session.

---

## Multi-corpus architecture (CRITICAL — anticipate from day one)

Language and author are a **configuration dimension**, not hardcoded. Every component (sampling, verification, display, scoring) must be corpus-agnostic.

```yaml
# config.yaml
game:
  max_attempts: 5
  words_per_session: 10
  context_words: 200
  min_words_for_leaderboard: 20  # player must play at least N words to appear
  fail_penalty_g: 6              # G value assigned on failure (for log2(G) scoring)

corpora:
  - id: tolstoy
    lang: ru
    author: Толстой
    title: Война и мир
    file: data/ru/war_and_peace.txt
    start_offset: 500            # skip Gutenberg headers (set manually after inspection)
    normalization: yo            # normalize ё→е in comparison only, not display
    difficulty: ★★★

  - id: austen
    lang: en
    author: Austen
    title: Pride and Prejudice
    file: data/en/austen_pride_prejudice.txt
    start_offset: 300
    normalization: none
    difficulty: ★★

  - id: woolf
    lang: en
    author: Woolf
    title: Mrs Dalloway
    file: data/en/woolf_mrs_dalloway.txt
    start_offset: 200
    normalization: none
    difficulty: ★★★★

  - id: houellebecq
    lang: fr
    author: Houellebecq
    title: Les Particules élémentaires
    file: data/fr/houellebecq_particules.txt
    start_offset: 400
    normalization: none
    difficulty: ★★★

  - id: levy
    lang: fr
    author: Marc Levy
    title: ...
    file: data/fr/levy_...txt
    start_offset: 200
    normalization: none
    difficulty: ★
```

---

## Scoring

**Per-word score**: log₂(G), where:
- G = 1 if correct on first attempt
- G = 2 if correct on second attempt
- ...
- G = 5 if correct on fifth attempt
- G = 6 (= `fail_penalty_g`) if all attempts exhausted

**Player score**: mean of log₂(G) over all words played.
Lower is better. Perfect player = 0.0 bits/word.

**Leaderboard**: sorted ascending by score. Players appear only after `min_words_for_leaderboard` words played. Display format:

```
Rank | Name/Avatar | Score (bits/word) | Words played | Corpus
```

LLMs appear in the same leaderboard as humans, with their own fixed avatars (see CONTEXT.md). Display number of words evaluated next to LLM scores — they will have far more observations than humans.

---

## Input verification

- Strip leading/trailing whitespace
- Case-insensitive comparison
- **Russian only**: normalize ё→е in both input and target before comparison (display always shows original)
- No lemmatization — exact form required (communicate this clearly in UI)
- Accept the comparison silently — do not reveal the correct word after failure until session ends

---

## Position sampling

- Sample uniformly from `[start_offset, len(text) - 50]`
- Log the exact character position of every sampled word
- Never resample the same position for the same session
- Positions are shared across sessions (different players may see the same position — this is fine and scientifically valid)

---

## Data stored (SQLite)

```sql
-- One row per game session
sessions (
  session_id TEXT PRIMARY KEY,
  player_id  TEXT,             -- anonymous cookie-based
  corpus_id  TEXT,
  started_at DATETIME
)

-- One row per word attempt within a session
guesses (
  id          INTEGER PRIMARY KEY,
  session_id  TEXT,
  position    INTEGER,         -- character offset in source text
  attempt_num INTEGER,         -- 1..5
  guess       TEXT,
  correct     BOOLEAN,
  timestamp   DATETIME
)

-- Derived: one row per (player, word), written when word is resolved
word_results (
  player_id   TEXT,
  corpus_id   TEXT,
  position    INTEGER,
  g_value     INTEGER,         -- 1..6
  timestamp   DATETIME
)

-- LLM baseline scores (populated by llm_runner/run_llms.py)
llm_scores (
  model_id    TEXT,
  corpus_id   TEXT,
  position    INTEGER,
  g_value     INTEGER,         -- rank of correct word in model's probability ordering
  exact_log2p REAL,            -- if logprobs available: exact -log2(p(correct|context))
  timestamp   DATETIME
)
```

---

## LLM competitors

LLMs are run **offline** via `llm_runner/run_llms.py` and their scores are inserted into `llm_scores`. They appear on the leaderboard as fixed competitors with custom avatars.

**Protocol for LLMs with logprobs** (GPT-4, open models via HuggingFace):
- Extract full probability distribution over next token given context
- Rank the correct word → G = rank
- Store exact -log₂(p) as `exact_log2p` for higher-resolution analysis

**Protocol for LLMs without logprobs** (Claude, etc.):
- Prompt: given context, produce top-5 guesses in order of confidence
- G = position of correct word in list, or 6 if absent

Start with: GPT-4o, Claude Sonnet, Mistral-large, one smaller open model (e.g. Llama-3-8B). Expand later.

---

## Frontend design

- Single HTML page, no framework
- Minimal, typographic, inspired by Soviet academic journals (see CONTEXT.md)
- Entry screen: corpus selector (language + author + difficulty stars)
- Game screen: context block (200 words) + hidden last word + input field + attempt tracker
- After each attempt: verdict (✓ or ✗) + remaining attempts
- After session: your score for this session + leaderboard
- Leaderboard: humans and LLMs interleaved, sorted by score, avatars visible

**Design reference**: Soviet academic journals of the 1960s — *Вопросы языкознания*, *Успехи физических наук*. See CONTEXT.md for aesthetic direction. Images will be provided separately.

---

## What NOT to do

- Do not build a login system — anonymous session cookies are sufficient
- Do not reveal the correct word during active attempts
- Do not show per-position statistics to players (prevents gaming)
- Do not hardcode any corpus — everything goes through config.yaml
- Do not over-engineer the LLM runner — it runs offline, not in real time
- Do not add features not listed here
