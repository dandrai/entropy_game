from __future__ import annotations

import re
import random
import yaml
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

_CYRILLIC_WORD = re.compile(r'^[а-яёА-ЯЁ]+$')

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass(frozen=True)
class GameConfig:
    max_attempts: int
    words_per_session: int
    context_words: int
    min_words_for_leaderboard: int
    fail_penalty_g: int


@dataclass(frozen=True)
class CorpusConfig:
    id: str
    lang: str
    author: str
    title: str
    file: str
    start_offset: int
    normalization: str
    difficulty: str
    format: str = "prose"


@lru_cache(maxsize=1)
def load_config() -> tuple[GameConfig, dict[str, CorpusConfig]]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    game = GameConfig(**raw["game"])
    corpora = {c["id"]: CorpusConfig(**c) for c in raw["corpora"]}
    return game, corpora


def get_game_config() -> GameConfig:
    game, _ = load_config()
    return game


def get_corpus_config(corpus_id: str) -> CorpusConfig:
    _, corpora = load_config()
    return corpora[corpus_id]


def get_all_corpora() -> dict[str, CorpusConfig]:
    _, corpora = load_config()
    return corpora


def is_corpus_available(corpus_id: str) -> bool:
    _, corpora = load_config()
    if corpus_id not in corpora:
        return False
    return (PROJECT_ROOT / corpora[corpus_id].file).exists()


_CYRILLIC_TO_LATIN = str.maketrans(
    'аеорсхАЕОРСХ',
    'aeopcxAEOPCX',
)


def _clean_text(text: str) -> str:
    # Normalize Latin stress-mark lookalikes used in Russian OCR
    text = text.replace('\u00F2', 'о').replace('\u00F3', 'о')  # ò, ó → о
    text = text.replace('\u00C0', 'А').replace('\u00E0', 'а')  # À, à → А/а
    # Remove separator lines
    text = re.sub(r'^\s*-{4,}\s*$', '', text, flags=re.MULTILINE)
    # Remove standalone footnote numbers and Roman numeral chapter headers
    text = re.sub(r'^\s*(?:[IVXLCDM]+|[0-9]+)\s*$', '', text, flags=re.MULTILINE)
    # Remove inline footnote numbers like "... слово 68 -- следующее ..."
    text = re.sub(r'\s+\d{1,3}(?=\s+--)', '', text)
    return text


@lru_cache(maxsize=10)
def _load_text(corpus_id: str) -> str:
    _, corpora = load_config()
    corpus = corpora[corpus_id]
    text = _clean_text((PROJECT_ROOT / corpus.file).read_text(encoding="utf-8"))
    if corpus.lang != 'ru':
        # Replace Cyrillic lookalike characters left by EPUB OCR (e.g. Cyrillic а→a)
        text = text.translate(_CYRILLIC_TO_LATIN)
    return text


@lru_cache(maxsize=10)
def _get_word_spans(corpus_id: str) -> list[tuple[int, int]]:
    """(start, end) char spans of every word token, in document order."""
    text = _load_text(corpus_id)
    return [(m.start(), m.end()) for m in re.finditer(r"\w+", text, re.UNICODE)]


@lru_cache(maxsize=10)
def _get_eligible_indices(corpus_id: str) -> list[int]:
    """
    Word indices eligible for sampling:
    - char position in [start_offset, len(text) - 50]
    - at least context_words predecessors exist
    - target word is Cyrillic (for Russian corpora with mixed-script noise)
    - all context_words preceding words are Cyrillic
    """
    game, corpora = load_config()
    corpus = corpora[corpus_id]
    text = _load_text(corpus_id)
    spans = _get_word_spans(corpus_id)
    max_char = len(text) - 50

    # Precompute Cyrillic flag for each word token
    is_cyr = [bool(_CYRILLIC_WORD.match(text[s:e])) for s, e in spans]

    eligible = []
    for idx, (start, _end) in enumerate(spans):
        if start < corpus.start_offset:
            continue
        if start > max_char:
            break
        if idx < game.context_words:
            continue
        if corpus.lang == 'ru':
            if not is_cyr[idx]:
                continue
            if not all(is_cyr[idx - game.context_words:idx]):
                continue
        else:
            if len(text[start:_end]) < 2:
                continue

        # Skip if target is the first word of a sentence
        pos = start - 1
        while pos >= 0 and text[pos] in ' \t\n\r':
            pos -= 1
        if pos >= 0 and text[pos] in '.!?\u2026':
            continue

        eligible.append(idx)
    return eligible


def _find_word_index_by_position(corpus_id: str, char_position: int) -> int:
    """Binary search: char position → word index in spans list."""
    spans = _get_word_spans(corpus_id)
    lo, hi = 0, len(spans) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        start = spans[mid][0]
        if start == char_position:
            return mid
        elif start < char_position:
            lo = mid + 1
        else:
            hi = mid - 1
    raise ValueError(f"No word at char position {char_position}")


def get_word_and_context(corpus_id: str, char_position: int) -> tuple[str, str]:
    """Return (target_word, context_text) for a stored char position."""
    game, _ = load_config()
    text = _load_text(corpus_id)
    spans = _get_word_spans(corpus_id)
    word_idx = _find_word_index_by_position(corpus_id, char_position)
    word = text[spans[word_idx][0]:spans[word_idx][1]]
    context_start_char = spans[word_idx - game.context_words][0]
    context = text[context_start_char:char_position].rstrip()
    return word, context


def sample_position(
    corpus_id: str,
    excluded_positions: set[int],
) -> Optional[tuple[int, str, str]]:
    """
    Sample a word not in excluded_positions.

    Returns (char_position, context_text, target_word):
    - char_position: start offset of target word in source file
    - context_text: raw text of the context_words words preceding the target
    - target_word: exact word form as it appears in the source
    Returns None if all eligible positions are exhausted.
    """
    game, _ = load_config()
    spans = _get_word_spans(corpus_id)
    text = _load_text(corpus_id)

    eligible = _get_eligible_indices(corpus_id)
    available = [i for i in eligible if spans[i][0] not in excluded_positions]
    if not available:
        return None

    word_idx = random.choice(available)
    char_pos = spans[word_idx][0]
    target_word = text[char_pos: spans[word_idx][1]]

    context_start_char = spans[word_idx - game.context_words][0]
    context_text = text[context_start_char:char_pos].rstrip()

    return char_pos, context_text, target_word


def next_sequential_word(
    corpus_id: str, current_word_idx: int
) -> Optional[tuple[int, int, str, str]]:
    """
    Advance to the next eligible word after current_word_idx.
    Applies the same per-language eligibility rules as _get_eligible_indices.
    Returns (new_word_idx, char_pos, context_text, target_word) or None if text exhausted.
    """
    game, _ = load_config()
    corpus = get_corpus_config(corpus_id)
    text = _load_text(corpus_id)
    spans = _get_word_spans(corpus_id)
    max_char = len(text) - 50

    idx = current_word_idx + 1
    while idx < len(spans) and spans[idx][0] <= max_char:
        if idx < game.context_words:
            idx += 1
            continue
        start, end = spans[idx]
        word = text[start:end]
        if corpus.lang == 'ru':
            if not _CYRILLIC_WORD.match(word):
                idx += 1
                continue
        else:
            if len(word) < 2:
                idx += 1
                continue
        # No sentence-start filter: in sequential mode the user reads forward
        # through the text and should see every eligible word as a target.
        context_start = spans[idx - game.context_words][0]
        context = text[context_start:start].rstrip()
        return idx, start, context, word
    return None


def get_growing_context(corpus_id: str, passage_start_char: int, target_char: int) -> str:
    """Full text from passage_start_char up to (not including) target_char."""
    return _load_text(corpus_id)[passage_start_char:target_char].rstrip()


def normalize_for_comparison(word: str, normalization: str) -> str:
    normalized = word.strip().lower()
    if normalization == "yo":
        normalized = normalized.replace("ё", "е")
    return normalized


def verify_guess(guess: str, target: str, normalization: str) -> bool:
    return normalize_for_comparison(guess, normalization) == normalize_for_comparison(
        target, normalization
    )
