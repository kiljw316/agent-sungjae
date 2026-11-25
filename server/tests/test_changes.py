"""#7 고추 제외 반영 / #8 양파→대파 변경안·승인 / #9 완료 단계 재료 변경 처리."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.tests.support import new_session, say


def _step(session: dict, pos: int) -> dict:
    return session["steps"][pos - 1]


def _ing(session: dict, name: str) -> dict:
    return next(i for i in session["ingredients"] if i["name"] == name)


def test_exclude_chili_question_requires_confirmation_then_updates_future_steps(client: TestClient) -> None:
    s = new_session(client)
    sid = s["sessionId"]
    r = say(client, sid, "고추가 없는데 빼도 돼?")
    assert r["intent"] == "EXCLUDE_INGREDIENT" and r["requiresConfirmation"]
    assert "제외할까요" in r["speech"]
    assert r["session"]["version"] == s["version"]  # nothing applied yet
    assert _ing(r["session"], "고추")["status"] == "ACTIVE"

    r = say(client, sid, "응")
    assert r["intent"] == "CONFIRM" and r["stateChanged"]
    assert r["speech"].startswith("고추를 제외하고 진행할게요")
    sess = r["session"]
    assert _ing(sess, "고추")["status"] == "EXCLUDED"
    step8 = _step(sess, 8)
    assert "고추" not in step8["text"].replace("고추장", "")
    assert step8["text"] == "불끄기3분전 나머지 파등을넣어 센불로 볶아주시면 끝!"
    assert "고추는 제외했어요" in " ".join(step8["notes"])
    assert "고추" not in step8["ingredientRefs"]
    # 양념장 step keeps 고추가루/고추장 untouched
    assert _step(sess, 2)["text"] == _step(sess, 2)["originalText"]
    assert [c["summary"] for c in sess["appliedChanges"]] == ["고추 제외"]

    # later the guide reads the adapted text
    for _ in range(7):
        r = say(client, sid, "다음 단계")
    assert r["session"]["currentStepPosition"] == 8
    assert "나머지 파등을넣어" in r["speech"] and "고추와" not in r["speech"]


def test_explicit_exclusion_is_applied_immediately(client: TestClient) -> None:
    s = new_session(client)
    r = say(client, s["sessionId"], "고추는 빼고 진행할게")
    assert r["stateChanged"] and not r["requiresConfirmation"]
    assert r["speech"].startswith("고추를 제외하고 진행할게요")


def test_negation_does_not_exclude(client: TestClient) -> None:
    s = new_session(client)
    r = say(client, s["sessionId"], "고추를 빼지 마")
    assert r["intent"] == "KEEP_INGREDIENT" and not r["stateChanged"]
    assert _ing(r["session"], "고추")["status"] == "ACTIVE"
    # a pending exclusion is cancelled by a negated follow-up
    say(client, s["sessionId"], "고추 없는데 빼도 돼?")
    r = say(client, s["sessionId"], "아 고추 빼지 마")
    assert r["session"]["pendingAction"] is None
    assert say(client, s["sessionId"], "응")["stateChanged"] is False
    assert _ing(client.get(f"/api/cooking-sessions/{s['sessionId']}").json(), "고추")["status"] == "ACTIVE"


def test_onion_to_green_onion_proposal_and_approval(client: TestClient) -> None:
    s = new_session(client)
    sid = s["sessionId"]
    r = say(client, sid, "양파가 없어서 대파로 바꿔야겠어")
    assert r["intent"] == "SUBSTITUTE_INGREDIENT" and r["requiresConfirmation"]
    change = r["change"]
    assert change["status"] == "PROPOSED" and change["source"] == "RULE"
    assert "1대 정도" in r["speech"] and "흰 부분" in r["speech"]
    assert _ing(r["session"], "양파")["status"] == "ACTIVE"  # not yet applied

    r = say(client, sid, "응 그렇게 해줘")
    sess = r["session"]
    assert r["stateChanged"]
    assert _ing(sess, "양파")["status"] == "REPLACED"
    added = next(i for i in sess["ingredients"] if i["status"] == "ADDED")
    assert added["name"] == "대파" and added["substituteForId"] == _ing(sess, "양파")["id"]
    assert _step(sess, 5)["text"].startswith("당근 대파등을 먼저")
    assert _step(sess, 6)["text"].startswith("대파 숨이 죽을 때쯤")  # rule phrase override, not "대파가 반투명"
    assert all("양파" not in _step(sess, p)["text"].replace("※", "") for p in (5, 6))
    assert sess["appliedChanges"][0]["summary"].startswith("양파 → 대파 대체")


def test_change_after_ingredient_already_used(client: TestClient) -> None:
    """#9: 양파 is used in steps 3,5,6. After step 5 is done, only step 6 may change and the
    onion must NOT be recorded as simply replaced."""
    s = new_session(client)
    sid = s["sessionId"]
    for _ in range(5):
        say(client, sid, "다음 단계")
    r = say(client, sid, "양파 대신 대파를 넣을게")
    assert r["change"]["plan"]["alreadyUsedInSteps"] == [3, 5]
    assert [e["position"] for e in r["change"]["plan"]["stepEdits"]] == [6]
    assert "이미 3, 5단계에서 사용" in r["speech"]
    sess = say(client, sid, "응")["session"]
    onion = _ing(sess, "양파")
    assert onion["status"] == "ACTIVE" and "3, 5단계에서 사용함" in onion["note"]
    assert _step(sess, 5)["text"] == _step(sess, 5)["originalText"]  # completed step untouched
    assert _step(sess, 6)["text"] != _step(sess, 6)["originalText"]

    # 마늘 is only used in steps 1-2 → excluding it now is refused
    r = say(client, sid, "마늘 빼줘")
    assert r["change"]["status"] == "NOT_APPLICABLE"
    assert "이미 1, 2단계에서 사용" in r["speech"] and not r["stateChanged"]


def test_unsupported_substitution_and_main_ingredient_refused(client: TestClient) -> None:
    s = new_session(client)
    sid = s["sessionId"]
    r = say(client, sid, "양파 대신 감자 넣을게")
    assert r["change"]["status"] == "NOT_APPLICABLE"
    assert "규칙이 없어요" in r["speech"] and "대파" in r["speech"]
    r = say(client, sid, "돼지고기 빼줘")
    assert "주재료" in r["speech"]
    r = say(client, sid, "버섯 빼줘")
    assert "없어요" in r["speech"]
    assert client.get(f"/api/cooking-sessions/{sid}").json()["version"] == s["version"]


def test_scale_servings(client: TestClient) -> None:
    s = new_session(client)
    sid = s["sessionId"]
    assert "이미 2인분" in say(client, sid, "2인분으로 변경해 줘")["speech"]
    r = say(client, sid, "4인분으로 바꿔줘")
    assert r["requiresConfirmation"]
    sess = say(client, sid, "네")["session"]
    assert sess["servings"] == 4
    assert _ing(sess, "돼지고기앞다리살")["display"] == "돼지고기앞다리살 2근"
    assert _ing(sess, "당근")["display"] == "당근 2/3개"
    assert _ing(sess, "고추")["display"] == "고추 기호에맞게"  # vague amount untouched


def test_rest_preview_apply_and_stale_replan(client: TestClient) -> None:
    s = new_session(client)
    sid = s["sessionId"]
    prev = client.post(f"/api/cooking-sessions/{sid}/changes/preview", json={"type": "EXCLUDE", "ingredient": "당근"})
    assert prev.status_code == 201
    change = prev.json()
    assert [e["position"] for e in change["plan"]["stepEdits"]] == [3, 5]
    # session moves on (steps 1-3 done) before the user approves
    for _ in range(3):
        client.post(f"/api/cooking-sessions/{sid}/next")
    r = client.post(f"/api/cooking-sessions/{sid}/changes/{change['changeId']}/apply")
    assert r.status_code == 200, r.text
    sess = r.json()["session"]
    assert _step(sess, 3)["text"] == _step(sess, 3)["originalText"]  # completed meanwhile → re-planned
    assert _step(sess, 5)["text"] != _step(sess, 5)["originalText"]
    # applying twice is idempotent; rejecting an applied change does nothing
    again = client.post(f"/api/cooking-sessions/{sid}/changes/{change['changeId']}/apply").json()
    assert again["session"]["version"] == sess["version"]
    assert client.get(f"/api/cooking-sessions/{sid}/changes").json()[0]["status"] == "APPLIED"


def test_original_recipe_is_never_modified(client: TestClient) -> None:
    s = new_session(client)
    before = client.get(f"/api/recipes/{s['recipeId']}").json()
    say(client, s["sessionId"], "고추는 빼고 진행할게")
    say(client, s["sessionId"], "양파 대신 대파")
    say(client, s["sessionId"], "응")
    assert client.get(f"/api/recipes/{s['recipeId']}").json() == before


def test_summary_lists_changes(client: TestClient) -> None:
    s = new_session(client)
    sid = s["sessionId"]
    say(client, sid, "고추는 빼고 진행할게")
    say(client, sid, "양파 대신 대파를 넣을게")
    say(client, sid, "응")
    summary = client.post(f"/api/cooking-sessions/{sid}/complete").json()
    assert len(summary["appliedChanges"]) == 2
    assert "바뀐 점은 2가지" in summary["speech"]
