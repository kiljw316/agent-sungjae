"""Provider-agnostic agent interface.

Agents only *read* an ``AgentContext`` and *return* validated Pydantic objects.
They never receive a DB session; the server validates and applies every change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from server.app.schemas.agent import (
    AgentContext,
    CookingAnswer,
    IntentResult,
    RecipeAdaptationProposal,
    SubstitutionProposal,
)


class AgentError(Exception):
    """Model unavailable, timed out, or returned output that failed validation."""


class CookingAgent(ABC):
    name: str = "agent"
    uses_llm: bool = False

    @abstractmethod
    def classify_intent(self, text: str, ctx: AgentContext) -> IntentResult: ...

    @abstractmethod
    def answer_cooking_question(self, question: str, ctx: AgentContext) -> CookingAnswer | None:
        """Return ``None`` when the agent has no grounded answer."""

    @abstractmethod
    def propose_ingredient_substitution(
        self, ctx: AgentContext, ingredient: str, substitute: str
    ) -> SubstitutionProposal | None:
        """Return ``None`` when the agent cannot judge this substitution."""

    @abstractmethod
    def propose_recipe_adaptation(self, ctx: AgentContext, request: str) -> RecipeAdaptationProposal | None: ...


class VisionAnalyzer(ABC):
    """Future camera-analysis hook (interface only; no implementation in this MVP).

    Implementations must report real model output only and must never confirm meat
    doneness from images alone.
    """

    @abstractmethod
    def describe_pan(self, image_bytes: bytes, ctx: AgentContext) -> str: ...
