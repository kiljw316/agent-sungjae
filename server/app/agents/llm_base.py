"""Shared prompt/validation logic for LLM-backed agents. Transport is provider-specific."""

from __future__ import annotations

import json
import logging
from abc import abstractmethod
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from server.app.agents.base import AgentError, CookingAgent
from server.app.schemas.agent import (
    AgentContext,
    CookingAnswer,
    IntentResult,
    RecipeAdaptationProposal,
    SubstitutionProposal,
)
from server.app.schemas.command import Intent

log = logging.getLogger(__name__)
M = TypeVar("M", bound=BaseModel)

SYSTEM = (
    "너는 한국어 요리 보조 에이전트다. 반드시 지정된 JSON 스키마로만 답한다. "
    "데이터베이스나 세션 상태를 직접 바꾸지 않으며, 서버가 검증할 구조화된 제안만 만든다. "
    "레시피에 없는 수량이나 조리 시간을 지어내지 않는다. 모르면 모른다고 한다. "
    "고기 익힘은 눈으로 확정할 수 없다고 안내하고 중심 온도 확인을 권한다. 답변은 2~3문장으로 짧게."
)


def _ctx_block(ctx: AgentContext) -> str:
    return json.dumps(ctx.model_dump(), ensure_ascii=False)


class LLMCookingAgent(CookingAgent):
    uses_llm = True

    @abstractmethod
    def _complete_json(self, system: str, user: str, schema: dict[str, object]) -> str:
        """Return raw JSON text from the model. Raise AgentError on transport failure."""

    def _ask(self, user: str, model: type[M]) -> M:
        raw = self._complete_json(SYSTEM, user, model.model_json_schema())
        try:
            return model.model_validate_json(raw)
        except ValidationError as exc:
            log.warning("LLM output failed validation (%s): %.200s", self.name, raw)
            raise AgentError(f"invalid model output: {exc.errors()[0]['msg']}") from exc

    def classify_intent(self, text: str, ctx: AgentContext) -> IntentResult:
        intents = ", ".join(i.value for i in Intent)
        result = self._ask(
            f"요리 상황: {_ctx_block(ctx)}\n사용자 발화: {text!r}\n"
            f"intent는 다음 중 하나: {intents}. 재료 관련이면 ingredient/substitute를, 인분은 servings, 시간은 seconds로.",
            IntentResult,
        )
        # Anything the LLM classifies as a state change must be confirmed by the user.
        return result.model_copy(update={"explicit": False})

    def answer_cooking_question(self, question: str, ctx: AgentContext) -> CookingAnswer | None:
        return self._ask(f"요리 상황: {_ctx_block(ctx)}\n질문: {question}", CookingAnswer)

    def propose_ingredient_substitution(
        self, ctx: AgentContext, ingredient: str, substitute: str
    ) -> SubstitutionProposal | None:
        proposal = self._ask(
            f"요리 상황: {_ctx_block(ctx)}\n'{ingredient}' 대신 '{substitute}'를 써도 되는지 판단하고, "
            "적정 양(quantity_text)과 조리 방법 변경(cooking_note)을 제안해. 부적절하면 supported=false.",
            SubstitutionProposal,
        )
        return proposal.model_copy(update={"substitute": substitute})

    def propose_recipe_adaptation(self, ctx: AgentContext, request: str) -> RecipeAdaptationProposal | None:
        return self._ask(
            f"요리 상황: {_ctx_block(ctx)}\n변경 요청: {request}\n"
            "아직 수행하지 않은 단계(position이 현재 단계 이상)만 수정해. 완료된 단계는 수정하지 마.",
            RecipeAdaptationProposal,
        )
