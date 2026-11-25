"""#3 세션 생성 / #4 다음 단계 / #5 현재 단계 반복 / #6 마지막 단계 완료."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.tests.support import import_confirmed, new_session, say


def test_create_session_snapshots_recipe(client: TestClient) -> None:
    rid = import_confirmed(client)
    s = client.post("/api/cooking-sessions", json={"recipeId": rid}).json()
    assert s["status"] == "READY" and s["version"] == 1
    assert s["totalSteps"] == 9 and s["currentStepPosition"] == 1
    assert len(s["ingredients"]) == 12 and all(i["status"] == "ACTIVE" for i in s["ingredients"])
    assert s["appliedChanges"] == []
    for key in ("sessionId", "recipeId", "status", "currentStepId", "version", "ingredients", "steps", "appliedChanges", "createdAt", "updatedAt"):
        assert key in s


def test_start_and_next(client: TestClient) -> None:
    s = new_session(client)
    sid = s["sessionId"]
    assert s["status"] == "IN_PROGRESS" and s["steps"][0]["status"] == "IN_PROGRESS"
    r = client.post(f"/api/cooking-sessions/{sid}/next", json={"expectedVersion": s["version"]})
    assert r.status_code == 200
    body = r.json()
    assert body["session"]["currentStepPosition"] == 2
    assert body["session"]["steps"][0]["status"] == "COMPLETED"
    assert body["speech"].startswith("2단계예요.")


def test_repeat_does_not_change_state(client: TestClient) -> None:
    s = new_session(client)
    r1 = say(client, s["sessionId"], "다시 설명해 줘")
    r2 = say(client, s["sessionId"], "한 번 더 말해줘")
    assert r1["intent"] == r2["intent"] == "REPEAT_STEP"
    assert r1["session"]["version"] == r2["session"]["version"] == s["version"]
    assert "돼지앞다리살" in r1["speech"]


def test_previous_step_navigation(client: TestClient) -> None:
    s = new_session(client)
    sid = s["sessionId"]
    client.post(f"/api/cooking-sessions/{sid}/next")
    r = client.post(f"/api/cooking-sessions/{sid}/previous").json()
    assert r["session"]["currentStepPosition"] == 1
    assert r["session"]["steps"][0]["status"] == "IN_PROGRESS" and r["session"]["steps"][1]["status"] == "PENDING"
    r = client.post(f"/api/cooking-sessions/{sid}/previous")
    assert r.status_code == 409 and r.json()["error"] == "ALREADY_FIRST_STEP"


def test_last_step_completes_session(client: TestClient) -> None:
    s = new_session(client)
    sid = s["sessionId"]
    for _ in range(8):
        client.post(f"/api/cooking-sessions/{sid}/next")
    last = client.get(f"/api/cooking-sessions/{sid}").json()
    assert last["currentStepPosition"] == 9
    r = client.post(f"/api/cooking-sessions/{sid}/next").json()
    assert r["session"]["status"] == "COMPLETED"
    assert all(st["status"] == "COMPLETED" for st in r["session"]["steps"])
    assert "요리를 마쳤어요" in r["speech"]
    assert client.post(f"/api/cooking-sessions/{sid}/next").status_code == 409


def test_pause_resume_complete(client: TestClient) -> None:
    s = new_session(client)
    sid = s["sessionId"]
    assert client.post(f"/api/cooking-sessions/{sid}/pause").json()["session"]["status"] == "PAUSED"
    assert client.post(f"/api/cooking-sessions/{sid}/next").status_code == 409
    assert client.post(f"/api/cooking-sessions/{sid}/resume").json()["session"]["status"] == "IN_PROGRESS"
    summary = client.post(f"/api/cooking-sessions/{sid}/complete").json()
    assert summary["status"] == "COMPLETED" and summary["completedSteps"] == 0
    assert "원본대로" in summary["speech"]


def test_voice_commands_for_session_control(client: TestClient) -> None:
    rid = import_confirmed(client)
    sid = client.post("/api/cooking-sessions", json={"recipeId": rid}).json()["sessionId"]
    assert say(client, sid, "제육볶음 시작해 줘")["session"]["status"] == "IN_PROGRESS"
    assert say(client, sid, "준비 끝났어")["session"]["currentStepPosition"] == 2
    assert say(client, sid, "지금 몇 단계야?")["speech"].startswith("전체 9단계 중 2단계")
    assert "7단계가 남았어요" in say(client, sid, "앞으로 얼마나 남았어?")["speech"]
    assert "이전 1단계" in say(client, sid, "이전 단계 알려줘")["speech"]
    assert say(client, sid, "잠깐 멈춰")["session"]["status"] == "PAUSED"
    assert say(client, sid, "다시 시작")["session"]["status"] == "IN_PROGRESS"
    r = say(client, sid, "요리 종료")
    assert r["requiresConfirmation"] and r["session"]["status"] == "IN_PROGRESS"
    r = say(client, sid, "응")
    assert r["session"]["status"] == "COMPLETED" and r["summary"]["completedSteps"] == 1
