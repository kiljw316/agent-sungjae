"""#14 LLM 응답 오류 시 안전한 실패 + LLM 제안은 서버 검증·사용자 확인 후에만 적용.

Ollama/OpenAI are replaced by httpx.MockTransport — these tests verify our adapter contract,
not a live model."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from server.app.agents.base import AgentError
from server.app.agents.hybrid import HybridCookingAgent
from server.app.agents.ollama_agent import OllamaCookingAgent
from server.app.agents.openai_agent import OpenAICookingAgent
from server.app.agents.rule_based import RuleBasedCookingAgent
from server.app.main import create_app
from server.app.schemas.agent import AgentContext, CookingAnswer
from server.tests.support import new_session, say


def ollama(reply: Callable[[dict], object]) -> HybridCookingAgent:
    def handler(request: httpx.Request) -> httpx.Response:
        out = reply(json.loads(request.content))
        if isinstance(out, Exception):
            raise out
        return httpx.Response(200, json={"message": {"content": out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)}})

    llm = OllamaCookingAgent("http://ollama.test", "qwen2.5:7b", timeout=1, transport=httpx.MockTransport(handler))
    return HybridCookingAgent(RuleBasedCookingAgent(), llm)


def _client(make_container: Callable, agent: HybridCookingAgent) -> TestClient:
    return TestClient(create_app(make_container(agent=agent)))


@pytest.mark.parametrize(
    "failure",
    [lambda body: "not json at all", lambda body: {"answer": ""}, lambda body: httpx.ReadTimeout("slow"), lambda body: httpx.ConnectError("down")],
    ids=["garbage", "schema-violation", "timeout", "connection"],
)
def test_llm_failure_leaves_session_untouched(make_container: Callable, failure: Callable) -> None:
    with _client(make_container, ollama(failure)) as c:
        s = new_session(c)
        sid = s["sessionId"]
        # unknown utterance: LLM classification fails → falls back to rule UNKNOWN
        r = say(c, sid, "음 그거 있잖아 그거")
        assert r["intent"] == "UNKNOWN" and not r["stateChanged"]
        # open question the rules can't answer → LLM fails → safe message
        r = say(c, sid, "제육볶음에 어울리는 반찬은 뭐야?")
        assert "AI 응답을 받지 못했어요" in r["speech"]
        # unsupported substitution needs the LLM → fails → not applied
        r = say(c, sid, "양파 대신 감자 넣을게")
        assert r["change"]["status"] == "NOT_APPLICABLE" and "AI 응답" in r["speech"]
        after = c.get(f"/api/cooking-sessions/{sid}").json()
        assert after["version"] == s["version"] and after["appliedChanges"] == []
        assert say(c, sid, "응")["stateChanged"] is False


def test_llm_substitution_requires_confirmation_and_is_server_applied(make_container: Callable) -> None:
    def reply(body: dict) -> object:
        return {"supported": True, "substitute": "감자", "quantity_text": "작은 것 1개", "cooking_note": "감자는 얇게 썰어 먼저 볶으세요."}

    with _client(make_container, ollama(reply)) as c:
        sid = new_session(c)["sessionId"]
        r = say(c, sid, "양파 대신 감자 넣을게")
        assert r["requiresConfirmation"] and r["change"]["source"] == "LLM"
        assert "AI 제안" in r["speech"]
        sess = say(c, sid, "응")["session"]
        added = next(i for i in sess["ingredients"] if i["status"] == "ADDED")
        assert added["name"] == "감자"
        assert sess["steps"][4]["text"].startswith("당근 감자등을")  # mechanical server rewrite, not LLM text


def test_llm_says_unsupported(make_container: Callable) -> None:
    reply = lambda body: {"supported": False, "substitute": "초콜릿", "reason": "맛이 맞지 않아요"}  # noqa: E731
    with _client(make_container, ollama(reply)) as c:
        sid = new_session(c)["sessionId"]
        r = say(c, sid, "양파 대신 초콜릿 넣을게")
        assert r["change"]["status"] == "NOT_APPLICABLE" and "맛이 맞지 않아요" in r["speech"]


def test_llm_state_change_intent_always_needs_confirmation(make_container: Callable) -> None:
    reply = lambda body: {"intent": "EXCLUDE_INGREDIENT", "ingredient": "당근", "explicit": True}  # noqa: E731
    with _client(make_container, ollama(reply)) as c:
        sid = new_session(c)["sessionId"]
        r = say(c, sid, "주황색 채소는 이번엔 패스")
        assert r["handledBy"] == "ollama" and r["requiresConfirmation"] and not r["stateChanged"]


def test_doneness_safety_note_enforced_server_side(make_container: Callable) -> None:
    reply = lambda body: {"answer": "겉이 갈색이면 다 익은 거예요."}  # noqa: E731
    agent = ollama(reply)
    agent.rule.answer_cooking_question = lambda q, ctx: None  # type: ignore[method-assign]  # force LLM path
    with _client(make_container, agent) as c:
        sid = new_session(c)["sessionId"]
        r = say(c, sid, "고기 속까지 익었나?")
        assert "중심 온도 75도" in r["speech"]


def test_openai_adapter_contract() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"answer": "네"})}}]})

    agent = OpenAICookingAgent("sk-test", "gpt-x", "https://api.openai.test/v1", 1, transport=httpx.MockTransport(handler))
    ctx = AgentContext(recipe_title="t", status="IN_PROGRESS", current_step_position=1, total_steps=1, current_step_text="x", active_ingredients=[])
    assert agent.answer_cooking_question("질문", ctx) == CookingAnswer(answer="네")
    assert seen["auth"] == "Bearer sk-test" and seen["body"]["response_format"]["type"] == "json_schema"

    bad = OpenAICookingAgent("k", "m", "https://x.test", 1, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(AgentError):
        bad.answer_cooking_question("q", ctx)


def test_default_settings_need_no_api_key(client: TestClient) -> None:
    h = client.get("/health").json()
    assert h == {"status": "ok", "db": "ok", "agent": "rule", "llm": "disabled"}
