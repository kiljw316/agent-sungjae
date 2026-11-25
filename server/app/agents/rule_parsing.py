"""Deterministic Korean command parsing helpers (durations, servings, ingredient phrases, negation)."""

from __future__ import annotations

import re

from server.app.parsers.ingredient_text import (
    CATALOG,
    IngredientLexeme,
    aliases_for,
    canonicalize,
    find_alias_spans,
    strip_particle,
)

_NATIVE = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "반": 0.5}
_NUM = r"(\d+(?:\.\d+)?|열|다섯|여섯|일곱|여덟|아홉|한|두|세|네|반)"


def _num(token: str) -> float:
    return float(_NATIVE[token]) if token in _NATIVE else float(token)


def parse_duration_seconds(text: str) -> int | None:
    """"3분" → 180, "1분 30초" → 90, "1분반" → 90, "30초" → 30."""
    t = text.replace(" ", "")
    total = 0.0
    if m := re.search(rf"{_NUM}시간", t):
        total += _num(m.group(1)) * 3600
    if m := re.search(rf"{_NUM}분(반)?", t):
        total += _num(m.group(1)) * 60 + (30 if m.group(2) else 0)
    if m := re.search(rf"{_NUM}초", t):
        total += _num(m.group(1))
    return int(total) if total > 0 else None


def parse_servings(text: str) -> float | None:
    m = re.search(rf"{_NUM}\s*인분", text)
    return _num(m.group(1)) if m else None


def build_lexemes(ingredient_names: list[str]) -> list[IngredientLexeme]:
    lexemes: dict[str, IngredientLexeme] = {}
    for name in ingredient_names:
        canonical = canonicalize(name)
        lex = lexemes.setdefault(canonical, IngredientLexeme(canonical, []))
        lex.aliases = sorted(set(lex.aliases) | set(aliases_for(canonical, [name])), key=len, reverse=True)
    for entry in CATALOG:
        if entry.canonical not in lexemes:
            lexemes[entry.canonical] = IngredientLexeme(entry.canonical, aliases_for(entry.canonical))
    return list(lexemes.values())


def first_ingredient_mention(text: str, lexemes: list[IngredientLexeme]) -> tuple[str, int] | None:
    """Return (canonical, position) of the first known ingredient in the utterance."""
    owner: dict[str, str] = {}
    for lex in lexemes:
        for alias in lex.aliases:
            owner.setdefault(alias, lex.canonical)
    spans = find_alias_spans(text, list(owner))
    if not spans:
        return None
    start, _end, alias = spans[0]
    return owner[alias], start


_WORD = r"([가-힣A-Za-z]+)"
_SUBST_PATTERNS = (
    re.compile(rf"{_WORD}\s*대신(?:에)?\s*{_WORD}"),
    re.compile(rf"{_WORD}\s*없(?:어서|으니까|으니|는데|으면)\s*,?\s*{_WORD}(?:으로|로)"),
    re.compile(rf"{_WORD}\s*{_WORD}(?:으로|로)\s*(?:바꿔|바꿀|바꾸|대체|변경|교체)"),
)
_TARGET_TRAILERS = ("를", "을", "로", "으로", "이", "가", "은", "는", "도", "만", "넣을게", "넣어", "쓸게")


def _clean_target(word: str) -> str:
    w = word
    for t in sorted(_TARGET_TRAILERS, key=len, reverse=True):
        if w.endswith(t) and len(w) > len(t):
            w = w[: -len(t)]
            break
    return w


def parse_substitution(text: str) -> tuple[str, str | None] | None:
    """Return (source phrase, target phrase|None) for "양파 대신 대파", "양파가 없어서 대파로 바꿔"."""
    for pat in _SUBST_PATTERNS:
        if m := pat.search(text):
            source = strip_particle(m.group(1))
            target = _clean_target(m.group(2))
            if source and target and source != target:
                return source, target
    m = re.search(rf"{_WORD}\s*대신", text)
    if m:
        return strip_particle(m.group(1)), None
    return None


_KEEP_RE = re.compile(r"(빼|제외하|생략하|없애)\s*지\s*(마|말|않)|안\s*뺄|안\s*빼|(빼|제외하)\s*면\s*안|그대로\s*(넣|둬|쓸|사용)|넣을\s*거야|있어")
_EXCLUDE_RE = re.compile(r"빼|제외|생략|없애|없어|없는|없으|없네|없다|넣지\s*(마|말)|안\s*넣|못\s*넣|떨어졌|다\s*썼")
_EXPLICIT_EXCLUDE_RE = re.compile(r"빼고|빼줘|빼 줘|뺄게|빼자|빼서|빼버|제외해|제외하고|제외할게|생략해|생략할게|생략하고|넣지\s*(마|말)|안\s*넣을")
_QUESTION_RE = re.compile(r"\?|돼$|될까|되나|돼요|괜찮|어떡|어떻게|할까|해도")


def is_keep_request(text: str) -> bool:
    return bool(_KEEP_RE.search(text)) and not re.search(r"없어|없는데|없으", text)


def is_exclusion_request(text: str) -> bool:
    return bool(_EXCLUDE_RE.search(text))


def is_explicit_exclusion(text: str) -> bool:
    return bool(_EXPLICIT_EXCLUDE_RE.search(text)) and not _QUESTION_RE.search(text.strip())


def fallback_ingredient_word(text: str) -> str | None:
    """Best-effort noun before 빼/없/제외 for ingredients that are not in the recipe."""
    m = re.search(rf"{_WORD}\s*(?:좀\s*)?(?:빼|제외|생략|없)", text)
    if not m:
        return None
    word = strip_particle(m.group(1))
    return None if word in {"그거", "이거", "저거", "그건", "다"} else word
