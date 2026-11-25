"""Rules first, LLM only for what rules cannot handle. LLM failures degrade to rule behaviour."""

from __future__ import annotations

import logging

from server.app.agents.base import AgentError, CookingAgent
from server.app.agents.rule_based import RuleBasedCookingAgent
from server.app.schemas.agent import (
    AgentContext,
    CookingAnswer,
    IntentResult,
    RecipeAdaptationProposal,
    SubstitutionProposal,
)
from server.app.schemas.command import Intent

log = logging.getLogger(__name__)


class HybridCookingAgent(CookingAgent):
    def __init__(self, rule: RuleBasedCookingAgent, llm: CookingAgent | None) -> None:
        self.rule = rule
        self.llm = llm
        self.name = f"rule+{llm.name}" if llm else "rule"
        self.uses_llm = llm is not None
        self.last_source = "rule"

    def classify_intent(self, text: str, ctx: AgentContext) -> IntentResult:
        result = self.rule.classify_intent(text, ctx)
        self.last_source = "rule"
        if result.intent is Intent.UNKNOWN and self.llm:
            try:
                result = self.llm.classify_intent(text, ctx)
                self.last_source = self.llm.name
            except AgentError as exc:
                log.warning("LLM intent classification failed: %s", exc)
        return result

    def answer_cooking_question(self, question: str, ctx: AgentContext) -> CookingAnswer | None:
        answer = self.rule.answer_cooking_question(question, ctx)
        self.last_source = "rule"
        if answer is None and self.llm:
            answer = self.llm.answer_cooking_question(question, ctx)  # AgentError propagates to caller
            self.last_source = self.llm.name
        return answer

    def propose_ingredient_substitution(
        self, ctx: AgentContext, ingredient: str, substitute: str
    ) -> SubstitutionProposal | None:
        proposal = self.rule.propose_ingredient_substitution(ctx, ingredient, substitute)
        self.last_source = "rule"
        if proposal is None and self.llm:
            proposal = self.llm.propose_ingredient_substitution(ctx, ingredient, substitute)
            self.last_source = self.llm.name
        return proposal

    def propose_recipe_adaptation(self, ctx: AgentContext, request: str) -> RecipeAdaptationProposal | None:
        if self.llm:
            self.last_source = self.llm.name
            return self.llm.propose_recipe_adaptation(ctx, request)
        return None
