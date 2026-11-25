"""Schemas that every CookingAgent (rule / Ollama / OpenAI) must return. LLM output is validated against these."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from server.app.schemas.command import Intent


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class IntentResult(AgentModel):
    intent: Intent
    ingredient: str | None = Field(default=None, max_length=50)
    substitute: str | None = Field(default=None, max_length=50)
    servings: float | None = Field(default=None, gt=0, le=50)
    seconds: int | None = Field(default=None, gt=0, le=6 * 3600)
    explicit: bool = False  # user stated an imperative ("빼고 진행할게") vs. a question
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class CookingAnswer(AgentModel):
    answer: str = Field(min_length=1, max_length=600)
    safety_note: str | None = Field(default=None, max_length=300)
    grounded_in_recipe: bool = False


class SubstitutionProposal(AgentModel):
    supported: bool
    substitute: str = Field(min_length=1, max_length=50)
    quantity_text: str | None = Field(default=None, max_length=50)
    cooking_note: str | None = Field(default=None, max_length=300)
    reason: str | None = Field(default=None, max_length=300)


class StepTextUpdate(AgentModel):
    position: int = Field(ge=1)
    new_text: str = Field(min_length=1, max_length=500)


class RecipeAdaptationProposal(AgentModel):
    summary: str = Field(min_length=1, max_length=300)
    step_updates: list[StepTextUpdate] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=5)


class AgentContext(AgentModel):
    """Read-only snapshot handed to agents. Agents never receive DB handles."""

    recipe_title: str
    status: str
    current_step_position: int | None
    total_steps: int
    current_step_text: str | None
    current_step_duration_minutes: int | None = None
    active_ingredients: list[str]
    all_ingredients: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
