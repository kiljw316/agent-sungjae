"""Rule-based intent classification incl. negation handling (spec §6)."""

from __future__ import annotations

import pytest

from server.app.agents.rule_based import RuleBasedCookingAgent
from server.app.agents.rule_parsing import parse_duration_seconds
from server.app.schemas.agent import AgentContext
from server.app.schemas.command import Intent as I

CTX = AgentContext(
    recipe_title="제육볶음", status="IN_PROGRESS", current_step_position=1, total_steps=9, current_step_text="x",
    active_ingredients=["돼지고기앞다리살", "양파", "당근", "마늘", "파", "고추", "간장", "물엿", "매실액", "고추장", "고춧가루", "후추가루"],
)
agent = RuleBasedCookingAgent()


@pytest.mark.parametrize(
    ("text", "intent", "ingredient"),
    [
        ("제육볶음 시작해 줘", I.START_COOKING, None),
        ("요리 시작", I.START_COOKING, None),
        ("잠깐 멈춰", I.PAUSE, None),
        ("다시 시작", I.RESUME, None),
        ("요리 종료", I.END_SESSION, None),
        ("다음 단계", I.NEXT_STEP, None),
        ("준비 끝났어", I.NEXT_STEP, None),
        ("다시 설명해 줘", I.REPEAT_STEP, None),
        ("이전 단계 알려줘", I.PREVIOUS_STEP_INFO, None),
        ("이전 단계로 돌아가 줘", I.GO_PREVIOUS, None),
        ("지금 몇 단계야?", I.CURRENT_STEP_NUMBER, None),
        ("앞으로 얼마나 남았어?", I.REMAINING_STEPS, None),
        ("고추가 없어", I.EXCLUDE_INGREDIENT, "고추"),
        ("고추는 빼고 진행할게", I.EXCLUDE_INGREDIENT, "고추"),
        ("고추장 빼줘", I.EXCLUDE_INGREDIENT, "고추장"),
        ("양파 넣지 마", I.EXCLUDE_INGREDIENT, "양파"),
        ("고추를 빼지 마", I.KEEP_INGREDIENT, "고추"),
        ("고추 빼면 안 돼", I.KEEP_INGREDIENT, "고추"),
        ("고추는 그대로 넣을 거야", I.KEEP_INGREDIENT, "고추"),
        ("양파 대신 대파를 넣을게", I.SUBSTITUTE_INGREDIENT, "양파"),
        ("양파가 없어서 대파로 바꿔야겠어", I.SUBSTITUTE_INGREDIENT, "양파"),
        ("2인분으로 변경해 줘", I.SCALE_SERVINGS, None),
        ("지금 남은 재료 알려줘", I.LIST_INGREDIENTS, None),
        ("몇 분 더 볶아야 해?", I.COOKING_QUESTION, None),
        ("중불이 어느 정도야?", I.COOKING_QUESTION, None),
        ("양념이 너무 뻑뻑한데?", I.COOKING_QUESTION, None),
        ("돼지고기가 충분히 익었는지 어떻게 알아?", I.COOKING_QUESTION, None),
        ("3분 타이머 시작", I.TIMER_START, None),
        ("타이머 1분 추가", I.TIMER_ADD, None),
        ("남은 시간 알려줘", I.TIMER_STATUS, None),
        ("타이머 취소", I.TIMER_CANCEL, None),
        ("타이머 멈춰", I.TIMER_CANCEL, None),
        ("응", I.CONFIRM, None),
        ("아니", I.DENY, None),
        ("오늘 날씨 좋네", I.UNKNOWN, None),
    ],
)
def test_classify(text: str, intent: I, ingredient: str | None) -> None:
    r = agent.classify_intent(text, CTX)
    assert r.intent == intent, (text, r)
    if ingredient:
        assert r.ingredient == ingredient


def test_explicit_vs_question() -> None:
    assert agent.classify_intent("고추는 빼고 진행할게", CTX).explicit
    assert not agent.classify_intent("고추가 없는데 빼도 돼?", CTX).explicit
    assert not agent.classify_intent("고추가 없어", CTX).explicit


def test_substitution_target() -> None:
    r = agent.classify_intent("양파가 없어서 대파로 바꿔야겠어", CTX)
    assert (r.ingredient, r.substitute) == ("양파", "대파")


@pytest.mark.parametrize(("text", "sec"), [("3분", 180), ("1분 30초", 90), ("1분반", 90), ("30초", 30), ("세 시간", 10800), ("다섯 분", 300)])
def test_duration(text: str, sec: int) -> None:
    assert parse_duration_seconds(text) == sec
