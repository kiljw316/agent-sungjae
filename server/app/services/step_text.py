"""Mechanical, conservative rewriting of step text (no LLM). If a clean rewrite is not
possible the text is left as-is and a note is attached instead."""

from __future__ import annotations

import re

from server.app.parsers.ingredient_text import find_alias_spans

# (batchim form, no-batchim form); longest first
_PARTICLES: tuple[tuple[str, str], ...] = (("이랑", "랑"), ("으로", "로"), ("과", "와"), ("을", "를"), ("은", "는"), ("이", "가"))
_CONJ_AFTER = re.compile(r"^(와|과|이랑|랑|하고|,|및)\s*")


def _batchim(word: str) -> int | None:
    last = word[-1] if word else ""
    if not ("가" <= last <= "힣"):
        return None
    return (ord(last) - 0xAC00) % 28


def _fix_particle(word: str, rest: str) -> tuple[str, int]:
    """Return (correct particle, length of the particle consumed from ``rest``)."""
    b = _batchim(word)
    for with_b, without_b in _PARTICLES:
        for form in (with_b, without_b):
            if rest.startswith(form):
                if b is None:
                    return form, len(form)
                if with_b == "으로":
                    chosen = "로" if b in (0, 8) else "으로"  # ㄹ받침(8) takes 로
                else:
                    chosen = with_b if b else without_b
                return chosen, len(form)
    return "", 0


def replace_ingredient(text: str, aliases: list[str], target: str, overrides: dict[str, str] | None = None) -> str:
    for old, new in (overrides or {}).items():
        text = text.replace(old, new)
    spans = find_alias_spans(text, aliases)
    out: list[str] = []
    cursor = 0
    for start, end, _alias in spans:
        out.append(text[cursor:start])
        particle, consumed = _fix_particle(target, text[end:])
        out.append(target + particle)
        cursor = end + consumed
    out.append(text[cursor:])
    return "".join(out)


_AMOUNT_AFTER = re.compile(
    r"^\s*(?:(?:크게|작게|듬뿍)\s*)?(?:\d+(?:[./]\d+)?|반)\s*(?:스푼|수저|큰술|작은술|숟가락|컵|g|kg|ml|개|근|쪽|줌|T|t)?(?:\([^)]*\))?"
    r"|^\s*(?:약간|톡톡|조금|적당량)"
)


def remove_ingredient(text: str, aliases: list[str]) -> tuple[str, bool]:
    """Remove list-style mentions ("고추와 파" → "파", "마늘반스푼, 간장1스푼" → "마늘반스푼").
    Returns (text, fully_removed). Mentions that can't be cut cleanly are left in place."""
    spans = find_alias_spans(text, aliases)
    if not spans:
        return text, True
    result = text
    removed_all = True
    for start, end, _alias in reversed(spans):
        amount = _AMOUNT_AFTER.match(result[end:])
        cut_end = end + (amount.end() if amount else 0)
        before, after = result[:start], result[cut_end:]
        conj = _CONJ_AFTER.match(after)
        if conj:
            result = before + after[conj.end():]
        elif re.search(r",\s*$", before):
            result = re.sub(r",\s*$", "", before) + after
        elif not amount and re.match(r"^\s+[가-힣]", after) and (start == 0 or before.endswith(" ")):
            result = before + after.lstrip()  # bare noun in a space-separated list: "당근 양파등을"
        else:
            removed_all = False
    return re.sub(r"\s{2,}", " ", result).strip(), removed_all
