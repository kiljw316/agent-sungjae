"""Default, offline agent: regex/keyword intent classification + curated knowledge."""

from __future__ import annotations

import re

from server.app.agents import knowledge
from server.app.agents.base import CookingAgent
from server.app.agents.rule_parsing import (
    build_lexemes,
    fallback_ingredient_word,
    first_ingredient_mention,
    is_exclusion_request,
    is_explicit_exclusion,
    is_keep_request,
    parse_duration_seconds,
    parse_servings,
    parse_substitution,
)
from server.app.parsers.ingredient_text import canonicalize
from server.app.schemas.agent import (
    AgentContext,
    CookingAnswer,
    IntentResult,
    RecipeAdaptationProposal,
    SubstitutionProposal,
)
from server.app.schemas.command import Intent

_CONFIRM_TOKEN = (
    r"(응|어|웅|네|넵|예|그래|그래요|좋아|좋아요|맞아|맞아요|오케이|ok|okay|yes|그렇게\s*해(줘|주세요)?|적용(해\s*줘|해)?"
    r"|해\s*줘|확인|부탁해|진행해(줘)?|그래\s*줘|바꿔\s*줘|제외해\s*줘|빼\s*줘|종료해\s*줘)"
)
_CONFIRM_RE = re.compile(rf"^{_CONFIRM_TOKEN}([\s,.!~]+{_CONFIRM_TOKEN})*[\s.!~요]*$", re.I)
_DENY_RE = re.compile(r"^(아니|아니야|아니요|아뇨|싫어|취소|하지\s*마|됐어|괜찮아|그냥\s*둬|no|아냐)[\s.!~요]*$", re.I)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().rstrip(".!~")).strip()


class RuleBasedCookingAgent(CookingAgent):
    name = "rule"
    uses_llm = False

    # ---------------------------------------------------------------- intent
    def classify_intent(self, text: str, ctx: AgentContext) -> IntentResult:
        t = _norm(text)
        c = t.replace(" ", "")
        if _CONFIRM_RE.match(t):
            return IntentResult(intent=Intent.CONFIRM)
        if _DENY_RE.match(t):
            return IntentResult(intent=Intent.DENY)

        # timers first: "타이머 멈춰" must not become PAUSE
        seconds = parse_duration_seconds(t)
        if "타이머" in c or "알람" in c or (seconds and re.search(r"(재|세|맞춰|잡아)", c) and "볶" not in c):
            if re.search(r"취소|꺼|끄|멈춰|그만|중지|삭제", c):
                return IntentResult(intent=Intent.TIMER_CANCEL)
            if seconds and re.search(r"추가|연장|더|늘려", c):
                return IntentResult(intent=Intent.TIMER_ADD, seconds=seconds)
            if re.search(r"남|얼마|몇분|확인", c) and not re.search(r"시작|맞춰|재줘|설정", c):
                return IntentResult(intent=Intent.TIMER_STATUS)
            if seconds:
                return IntentResult(intent=Intent.TIMER_START, seconds=seconds)
            return IntentResult(intent=Intent.TIMER_STATUS)
        if "남은시간" in c or "몇분남" in c:
            return IntentResult(intent=Intent.TIMER_STATUS)

        lexemes = build_lexemes(ctx.active_ingredients + ctx.all_ingredients)
        mention = first_ingredient_mention(t, lexemes)

        servings = parse_servings(t)
        if servings:
            return IntentResult(intent=Intent.SCALE_SERVINGS, servings=servings)

        # substitution ("A 대신 B", "A 없어서 B로")
        subst = parse_substitution(t)
        if subst:
            source, target = subst
            src_mention = first_ingredient_mention(source, lexemes)
            src = src_mention[0] if src_mention else canonicalize(source)
            return IntentResult(intent=Intent.SUBSTITUTE_INGREDIENT, ingredient=src, substitute=target, explicit=True)

        if re.search(r"남은재료|재료(목록|알려|뭐|리스트|확인)|무슨재료|어떤재료|재료가뭐", c):
            return IntentResult(intent=Intent.LIST_INGREDIENTS)

        if mention or fallback_ingredient_word(t):
            ingredient = mention[0] if mention else fallback_ingredient_word(t)
            if is_keep_request(t):
                return IntentResult(intent=Intent.KEEP_INGREDIENT, ingredient=ingredient)
            if is_exclusion_request(t):
                return IntentResult(
                    intent=Intent.EXCLUDE_INGREDIENT, ingredient=ingredient, explicit=is_explicit_exclusion(t)
                )

        # session / step control
        if re.search(r"요리.*(종료|끝내|끝낼|그만|마칠|마치|끝났)|^(종료|세션종료)", c):
            return IntentResult(intent=Intent.END_SESSION)
        if re.search(r"다시시작|재개|계속(하|진행)|이어서", c):
            return IntentResult(intent=Intent.RESUME)
        if re.search(r"다시(설명|말해|알려|읽어|한번)|한번더|뭐라고|반복|못들었|다시$", c):
            return IntentResult(intent=Intent.REPEAT_STEP)
        if re.search(r"(이전|전|앞)단계로(돌아|가|이동)|뒤로가|되돌아", c):
            return IntentResult(intent=Intent.GO_PREVIOUS)
        if re.search(r"(이전|전|앞|아까)단계", c):
            return IntentResult(intent=Intent.PREVIOUS_STEP_INFO)
        if re.search(r"몇단계남|얼마나남|남은단계|몇개남|얼마남", c):
            return IntentResult(intent=Intent.REMAINING_STEPS)
        if re.search(r"몇단계|지금단계|현재단계|어디까지|몇번째", c):
            return IntentResult(intent=Intent.CURRENT_STEP_NUMBER)
        if re.search(r"다음|준비(끝|됐|다했|완료)|다했어|했어요?$|완료|넘어가|넘겨", c):
            return IntentResult(intent=Intent.NEXT_STEP)
        if re.search(r"멈춰|잠깐|일시정지|중지|기다려|스톱|stop|pause", c, re.I):
            return IntentResult(intent=Intent.PAUSE)
        if re.search(r"시작", c):
            return IntentResult(intent=Intent.START_COOKING)

        if re.search(r"\?|어떻게|어느정도|얼마나|몇분|왜|뭐야|인가|나요|까요|돼$|될까|되나|해야", c) or any(
            k in c for keys, _ in knowledge.COOKING_FAQ for k in keys
        ):
            return IntentResult(intent=Intent.COOKING_QUESTION)
        return IntentResult(intent=Intent.UNKNOWN, confidence=0.0)

    # -------------------------------------------------------------- answers
    def answer_cooking_question(self, question: str, ctx: AgentContext) -> CookingAnswer | None:
        c = question.replace(" ", "")
        if re.search(r"몇분|얼마나(더)?(볶|익|끓|구워|조려|재워)|언제까지", c) and not re.search(r"익었", c):
            return self._duration_answer(ctx)
        for keys, answer in knowledge.COOKING_FAQ:
            if any(k in c for k in keys):
                return CookingAnswer(answer=answer, grounded_in_recipe=False)
        return None

    @staticmethod
    def _duration_answer(ctx: AgentContext) -> CookingAnswer:
        if ctx.current_step_duration_minutes:
            return CookingAnswer(
                answer=f"원본 레시피 기준으로 이 단계는 약 {ctx.current_step_duration_minutes}분이에요. "
                f"'{ctx.current_step_duration_minutes}분 타이머 시작'이라고 말하면 타이머를 맞춰 드릴게요.",
                grounded_in_recipe=True,
            )
        cue = _state_cue(ctx.current_step_text or "")
        tail = f" 지금 단계 설명의 '{cue}' 상태를 기준으로 판단해 주세요." if cue else " 재료의 색과 상태를 보면서 판단해 주세요."
        return CookingAnswer(answer="원본 레시피에는 이 단계의 시간이 적혀 있지 않아요." + tail, grounded_in_recipe=True)

    # ---------------------------------------------------------- proposals
    def propose_ingredient_substitution(
        self, ctx: AgentContext, ingredient: str, substitute: str
    ) -> SubstitutionProposal | None:
        rule = knowledge.find_substitution(canonicalize(ingredient), canonicalize(substitute)) or knowledge.find_substitution(
            canonicalize(ingredient), substitute
        )
        if rule is None:
            return None
        return SubstitutionProposal(
            supported=True,
            substitute=substitute,
            quantity_text=rule.amount,
            cooking_note=rule.cooking_note,
            reason="사전에 정의된 안전 대체 규칙",
        )

    def propose_recipe_adaptation(self, ctx: AgentContext, request: str) -> RecipeAdaptationProposal | None:
        return None  # rule mode adapts steps mechanically inside ChangePlanner


def _state_cue(text: str) -> str | None:
    m = re.search(r"([^\s,.()]+(?:\s[^\s,.()]+){0,2}\s?[^\s,.()]*(?:때까지|때쯤|할\s?때|해지면))", text)
    return m.group(1).strip() if m else None
