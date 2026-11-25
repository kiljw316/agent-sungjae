"""Ingredient vocabulary and Korean-text matching.

Korean recipe text frequently omits spaces ("고추가루2스푼", "파등을넣어"), and many
ingredient names are prefixes of other ingredients (고추 → 고추장/고추가루, 파 → 양파).
Matching therefore uses:
  * a negative look-behind for Hangul (so "파" does not match inside "양파"), and
  * a negative look-ahead for compound suffixes (so "고추" does not match "고추장").
Longer aliases are tried first so "진간장" / "고추가루" win over their prefixes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class CatalogEntry:
    canonical: str
    aliases: tuple[str, ...]
    role: str = "secondary"  # main | secondary | seasoning | garnish
    category: str = "other"  # vegetable | protein | sauce | spice ...


CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry("돼지고기", ("돼지고기앞다리살", "돼지앞다리살", "돼지고기", "앞다리살", "삼겹살", "목살", "돼지", "고기"), "main", "protein"),
    CatalogEntry("양파", ("양파",), "secondary", "vegetable"),
    CatalogEntry("당근", ("당근",), "secondary", "vegetable"),
    CatalogEntry("마늘", ("다진마늘", "다진 마늘", "마늘"), "seasoning", "aromatic"),
    CatalogEntry("대파", ("대파", "쪽파", "파"), "garnish", "aromatic"),
    CatalogEntry("고추", ("청양고추", "홍고추", "풋고추", "고추"), "garnish", "vegetable"),
    CatalogEntry("간장", ("진간장", "양조간장", "간장"), "seasoning", "sauce"),
    CatalogEntry("물엿", ("물엿", "올리고당"), "seasoning", "sweetener"),
    CatalogEntry("매실액", ("매실액", "매실청", "매실"), "seasoning", "sweetener"),
    CatalogEntry("고추장", ("고추장",), "seasoning", "sauce"),
    CatalogEntry("고춧가루", ("고춧가루", "고추가루"), "seasoning", "spice"),
    CatalogEntry("후추", ("후춧가루", "후추가루", "후추"), "seasoning", "spice"),
    CatalogEntry("설탕", ("설탕",), "seasoning", "sweetener"),
    CatalogEntry("양배추", ("양배추",), "secondary", "vegetable"),
    CatalogEntry("애호박", ("애호박",), "secondary", "vegetable"),
    CatalogEntry("참기름", ("참기름",), "garnish", "oil"),
    CatalogEntry("깨", ("통깨", "깨"), "garnish", "garnish"),
)

# Suffixes that turn an ingredient word into a *different* ingredient.
_COMPOUND_SUFFIXES = ("장", "가루", "기름", "즙", "청", "액", "소스", "잎", "씨")
_PARTICLE_SUFFIXES = ("이랑", "으로", "에서", "은", "는", "이", "가", "을", "를", "도", "와", "과", "랑", "로", "만")


@dataclass
class IngredientLexeme:
    canonical: str
    aliases: list[str] = field(default_factory=list)


def canonicalize(name: str) -> str:
    """Map a raw ingredient name ("돼지고기앞다리살", "후추가루") to its canonical key."""
    compact = name.replace(" ", "")
    for entry in CATALOG:
        if compact in (a.replace(" ", "") for a in entry.aliases):
            return entry.canonical
    return compact


def catalog_entry(canonical: str) -> CatalogEntry | None:
    for entry in CATALOG:
        if entry.canonical == canonical:
            return entry
    return None


def aliases_for(canonical: str, raw_names: list[str] | None = None) -> list[str]:
    names = set(raw_names or [])
    names.add(canonical)
    entry = catalog_entry(canonical)
    if entry:
        names.update(entry.aliases)
    return sorted((n.replace(" ", "") for n in names if n), key=len, reverse=True)


@lru_cache(maxsize=512)
def _alias_pattern(alias: str) -> re.Pattern[str]:
    suffixes = "|".join(_COMPOUND_SUFFIXES)
    return re.compile(rf"(?<![가-힣]){re.escape(alias)}(?!{suffixes})")


def find_alias_spans(text: str, aliases: list[str]) -> list[tuple[int, int, str]]:
    """Return non-overlapping (start, end, alias) matches, longest alias first."""
    spans: list[tuple[int, int, str]] = []
    taken = [False] * len(text)
    for alias in sorted(aliases, key=len, reverse=True):
        for m in _alias_pattern(alias).finditer(text):
            if any(taken[m.start() : m.end()]):
                continue
            spans.append((m.start(), m.end(), alias))
            for i in range(m.start(), m.end()):
                taken[i] = True
    return sorted(spans)


def mentions(text: str, canonical: str, raw_names: list[str] | None = None) -> bool:
    return bool(find_alias_spans(text, aliases_for(canonical, raw_names)))


def find_ingredient_refs(text: str, lexemes: list[IngredientLexeme]) -> list[str]:
    """Which of the given ingredients does ``text`` mention? Longest alias across all wins."""
    alias_owner: dict[str, str] = {}
    for lex in lexemes:
        for alias in lex.aliases:
            alias_owner.setdefault(alias, lex.canonical)
    refs: list[str] = []
    for _s, _e, alias in find_alias_spans(text, list(alias_owner)):
        owner = alias_owner[alias]
        if owner not in refs:
            refs.append(owner)
    return refs


def strip_particle(word: str) -> str:
    for p in _PARTICLE_SUFFIXES:
        if word.endswith(p) and len(word) > len(p):
            return word[: -len(p)]
    return word
