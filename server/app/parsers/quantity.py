"""Parse "돼지고기앞다리살 1근", "당근 1/3개", "고추 기호에맞게" into name / quantity / unit / note.

Rules: never invent a number. Vague amounts ("약간", "기호에맞게") are kept as ``note``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

_VAGUE = ("약간", "적당량", "적당히", "기호에맞게", "기호에 맞게", "기호껏", "조금", "톡톡", "취향껏", "넉넉히", "한줌", "한 줌")
_NATIVE_NUMBERS = {"반": Fraction(1, 2), "한": Fraction(1), "두": Fraction(2), "세": Fraction(3), "네": Fraction(4)}
_AMOUNT_RE = re.compile(
    r"^(?P<num>\d+(?:\.\d+)?\s*(?:과|와)?\s*\d+/\d+|\d+/\d+|\d+(?:\.\d+)?|반|한|두|세|네)\s*(?P<unit>[^\d\s(]*)\s*(?P<rest>.*)$"
)


@dataclass(frozen=True)
class ParsedQuantity:
    name: str
    quantity: float | None
    quantity_text: str | None
    unit: str | None
    note: str | None


def _to_fraction(token: str) -> Fraction | None:
    token = token.replace(" ", "")
    if token in _NATIVE_NUMBERS:
        return _NATIVE_NUMBERS[token]
    mixed = re.fullmatch(r"(\d+)(?:과|와)?(\d+)/(\d+)", token)
    if mixed:
        return Fraction(int(mixed.group(1))) + Fraction(int(mixed.group(2)), int(mixed.group(3)))
    try:
        return Fraction(token)
    except (ValueError, ZeroDivisionError):
        return None


def parse_amount(amount_text: str) -> tuple[float | None, str | None, str | None, str | None]:
    """Return (quantity, quantity_text, unit, note) for an amount string like "1/3개"."""
    text = amount_text.strip()
    if not text:
        return None, None, None, None
    for vague in _VAGUE:
        if text.replace(" ", "").startswith(vague.replace(" ", "")):
            return None, None, None, text
    m = _AMOUNT_RE.match(text)
    if not m:
        return None, None, None, text
    frac = _to_fraction(m.group("num"))
    if frac is None:
        return None, None, None, text
    unit = m.group("unit") or None
    rest = m.group("rest").strip() or None
    return float(frac), m.group("num").replace(" ", ""), unit, rest


def _looks_like_amount(token: str) -> bool:
    t = token.replace(" ", "")
    return bool(re.match(r"^(\d|반\S|한\S|두\S|세\S|네\S)", t)) or any(t.startswith(v.replace(" ", "")) for v in _VAGUE)


def parse_ingredient_line(line: str) -> ParsedQuantity:
    text = re.sub(r"\s+", " ", line.strip().lstrip("-•*·").strip())
    tokens = text.split(" ")
    split_at = next((i for i, tok in enumerate(tokens) if i > 0 and _looks_like_amount(tok)), None)
    if split_at is None:
        # "돼지고기600g" (no space) → split at first digit
        m = re.match(r"^(?P<name>[^\d]+?)(?P<amount>\d.*)$", text)
        if m:
            name, amount = m.group("name").strip(), m.group("amount")
        else:
            return ParsedQuantity(text, None, None, None, None)
    else:
        name, amount = " ".join(tokens[:split_at]), " ".join(tokens[split_at:])
    quantity, qtext, unit, note = parse_amount(amount)
    return ParsedQuantity(name=name, quantity=quantity, quantity_text=qtext, unit=unit, note=note)


def format_quantity(quantity: float) -> str:
    frac = Fraction(quantity).limit_denominator(4)
    if frac.denominator == 1:
        return str(frac.numerator)
    if abs(float(frac) - quantity) < 0.005:
        whole, rem = divmod(frac.numerator, frac.denominator)
        return f"{whole}과 {rem}/{frac.denominator}" if whole else f"{rem}/{frac.denominator}"
    return f"{quantity:.2f}".rstrip("0").rstrip(".")


_STEP_MINUTES_RE = re.compile(r"(\d+)\s*분")


def extract_step_minutes(text: str) -> int | None:
    """Only explicit minute mentions in the source step text are used. No guessing."""
    m = _STEP_MINUTES_RE.search(text)
    return int(m.group(1)) if m else None


_ISO_DURATION_RE = re.compile(r"^P(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)$")


def parse_iso_duration_minutes(value: str | None) -> int | None:
    if not value:
        return None
    m = _ISO_DURATION_RE.match(value.strip())
    if not m or not any(m.groupdict().values()):
        return None
    return int(m.group("h") or 0) * 60 + int(m.group("m") or 0) + (1 if int(m.group("s") or 0) >= 30 else 0)


def parse_servings(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    m = re.search(r"(\d+(?:\.\d+)?)", str(value))
    return float(m.group(1)) if m else None
