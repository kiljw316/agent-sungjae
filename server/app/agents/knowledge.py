"""Curated, deterministic cooking knowledge used when no LLM is connected.

Only substitutions listed here are allowed in rule-based mode. Anything else is refused
with a clear message instead of being guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SubstitutionRule:
    source: str  # canonical
    target: str  # canonical / display name
    amount: str  # short amount of the target, e.g. "1대 정도"
    cooking_note: str
    # Exact phrase rewrites for step text where a plain name swap would be wrong.
    phrase_overrides: dict[str, str] = field(default_factory=dict)
    quantity_ratio: float | None = None  # numeric ratio when meaningful (target per source unit)
    target_unit: str | None = None


SAFE_SUBSTITUTIONS: tuple[SubstitutionRule, ...] = (
    SubstitutionRule(
        "양파", "대파", "1대 정도",
        "대파는 양파보다 빨리 익고 타기 쉬우니 흰 부분은 양파 넣을 때, 초록 부분은 마지막에 넣어 주세요.",
        phrase_overrides={"양파가 반투명해질때쯤": "대파 숨이 죽을 때쯤", "양파가 반투명해질 때쯤": "대파 숨이 죽을 때쯤"},
    ),
    SubstitutionRule("양파", "양배추", "한 줌 정도", "양배추는 숨이 죽을 때까지 센불에 볶아 주세요.",
                     phrase_overrides={"양파가 반투명해질때쯤": "양배추 숨이 죽을 때쯤", "양파가 반투명해질 때쯤": "양배추 숨이 죽을 때쯤"}),
    SubstitutionRule("매실액", "설탕", "약 2/3스푼", "설탕은 양념장에 미리 녹여 주세요.",
                     quantity_ratio=1 / 3, target_unit="스푼"),
    SubstitutionRule("물엿", "올리고당", "같은 양", "물엿과 같은 방법으로 넣으면 돼요.", quantity_ratio=1.0, target_unit="스푼"),
    SubstitutionRule("물엿", "설탕", "약 2/3스푼", "설탕은 양념장에 미리 녹여 주세요.", quantity_ratio=2 / 3, target_unit="스푼"),
    SubstitutionRule("고추", "청양고추", "같은 양 또는 조금 적게", "청양고추는 더 매우니 기호에 맞게 양을 줄이세요."),
    SubstitutionRule("대파", "쪽파", "같은 양", "쪽파는 더 빨리 익으니 마지막에 넣어 주세요."),
    SubstitutionRule("대파", "양파", "1/4개 정도", "양파는 채소를 볶는 단계에서 함께 넣어 주세요."),
    SubstitutionRule("당근", "애호박", "같은 양", "애호박은 당근보다 빨리 익으니 조금 늦게 넣어 주세요."),
    SubstitutionRule("마늘", "다진마늘", "같은 양", "그대로 사용하면 돼요."),
    SubstitutionRule("간장", "진간장", "같은 양", "그대로 사용하면 돼요.", quantity_ratio=1.0, target_unit="스푼"),
)

EXCLUSION_NOTES: dict[str, str] = {
    "매실액": "단맛과 향이 줄어요. 원본 레시피에는 매실액이 없으면 설탕 2/3수저로 대체하라고 되어 있어요.",
    "물엿": "단맛과 윤기가 줄어요.",
    "고추장": "매운맛과 감칠맛이 크게 줄어 양념 맛이 달라져요.",
    "고춧가루": "색과 매운맛이 줄어요.",
    "간장": "간이 싱거워질 수 있어요. 마지막에 간을 보고 소금으로 맞춰 주세요.",
    "마늘": "향이 줄어요.",
}


def find_substitution(source: str, target: str) -> SubstitutionRule | None:
    for rule in SAFE_SUBSTITUTIONS:
        if rule.source == source and rule.target == target:
            return rule
    return None


def substitutes_for(source: str) -> list[str]:
    return [r.target for r in SAFE_SUBSTITUTIONS if r.source == source]


FOOD_SAFETY_DONENESS = (
    "눈으로 보는 것만으로는 고기 속까지 익었는지 확정할 수 없어요. "
    "가장 확실한 방법은 조리용 온도계로 가장 두꺼운 조각의 중심 온도를 재는 거예요. "
    "식약처는 고기를 중심 온도 75도에서 1분 이상 익히도록 권장해요. "
    "온도계가 없다면 가장 두꺼운 조각을 잘라 속에 붉은 기가 없는지, 육즙이 맑은지 확인하되 참고용으로만 봐 주세요."
)

# (keywords, answer). First match wins; checked against the space-stripped question.
COOKING_FAQ: tuple[tuple[tuple[str, ...], str], ...] = (
    (("익었", "익은지", "익었는지", "덜익", "안익", "속까지"), FOOD_SAFETY_DONENESS),
    (("중불",), "중불은 가스레인지 기준으로 불꽃 끝이 팬 바닥에 닿을 듯 말 듯한 정도예요. 인덕션이라면 최대 화력의 중간쯤으로 맞추면 돼요."),
    (("센불", "강불"), "센불은 불꽃이 팬 바닥 전체에 넉넉히 닿는 정도예요. 인덕션은 최대 화력에 가깝게 두면 돼요."),
    (("약불",), "약불은 불꽃이 팬 바닥에 닿지 않을 정도로 작게 줄인 상태예요. 인덕션은 낮은 단계로 맞추세요."),
    (("뻑뻑", "되직", "너무되", "걸쭉"), "양념이 뻑뻑하면 물을 1~2스푼씩 조금씩 넣어 가며 농도를 맞춰 주세요. 양념이 타지 않게 불은 중불 이하로 줄여 주세요."),
    (("너무짜", "짜요", "짠데", "짜다", "짜게"), "너무 짜면 물을 조금 넣거나 고기·채소 양을 늘려 간을 희석해 주세요. 간장이나 고추장을 더 넣지 마세요."),
    (("싱거", "간이약", "밍밍"), "싱거우면 간장을 반 스푼씩 넣어 가며 간을 맞춰 주세요. 원본 레시피도 마지막에 간을 보고 남은 양념을 넣으라고 해요."),
    (("탈것", "타요", "탄다", "눌어", "탔", "타는"), "타기 시작하면 바로 불을 줄이고 물을 1~2스푼 넣어 바닥을 긁어 주세요. 양념이 들어간 뒤엔 중불 이하가 좋아요."),
    (("물이생", "물이나", "물이많", "질척"), "물이 많이 생기면 센불로 올려 수분을 날려 주세요. 원본 레시피에서도 채소는 센불에 볶으라고 해요."),
)
