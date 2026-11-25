"""#12 타이머 시작·연장·취소·종료 이벤트."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.tests.support import new_session, say


def test_timer_lifecycle_via_api(client: TestClient, clock) -> None:  # type: ignore[no-untyped-def]
    sid = new_session(client)["sessionId"]
    t = client.post(f"/api/cooking-sessions/{sid}/timers", json={"durationSeconds": 180}).json()
    assert t["status"] == "RUNNING" and t["remainingSeconds"] == 180 and t["label"].startswith("1단계")
    clock.advance(100)
    assert client.get(f"/api/cooking-sessions/{sid}/timers").json()[0]["remainingSeconds"] == 80
    t = client.post(f"/api/cooking-sessions/{sid}/timers/{t['timerId']}/extend", json={"seconds": 60}).json()
    assert t["remainingSeconds"] == 140
    clock.advance(140)
    ev = client.get(f"/api/cooking-sessions/{sid}/events").json()
    finished = [e for e in ev["events"] if e["type"] == "TIMER_FINISHED"]
    assert len(finished) == 1 and "끝났어요" in finished[0]["message"]
    # event emitted exactly once
    ev2 = client.get(f"/api/cooking-sessions/{sid}/events", params={"afterId": ev["lastEventId"]}).json()
    assert ev2["events"] == []
    assert client.get(f"/api/cooking-sessions/{sid}/timers").json()[0]["status"] == "FINISHED"
    assert client.post(f"/api/cooking-sessions/{sid}/timers/{t['timerId']}/cancel").status_code == 409


def test_timer_voice_commands(client: TestClient, clock) -> None:  # type: ignore[no-untyped-def]
    sid = new_session(client)["sessionId"]
    r = say(client, sid, "3분 타이머 시작")
    assert r["intent"] == "TIMER_START" and r["timer"]["durationSeconds"] == 180
    clock.advance(30)
    r = say(client, sid, "타이머 1분 추가")
    assert r["intent"] == "TIMER_ADD" and r["timer"]["remainingSeconds"] == 210
    r = say(client, sid, "남은 시간 알려줘")
    assert r["intent"] == "TIMER_STATUS" and "3분 30초" in r["speech"]
    r = say(client, sid, "타이머 취소")
    assert r["intent"] == "TIMER_CANCEL" and r["timer"]["status"] == "CANCELLED"
    assert "없어요" in say(client, sid, "남은 시간 알려줘")["speech"]
    # timers never bump the session version
    assert r["session"]["version"] == client.get(f"/api/cooking-sessions/{sid}").json()["version"]


def test_timers_cancelled_on_completion(client: TestClient) -> None:
    sid = new_session(client)["sessionId"]
    say(client, sid, "1분 타이머 시작")
    client.post(f"/api/cooking-sessions/{sid}/complete")
    assert client.get(f"/api/cooking-sessions/{sid}/timers").json()[0]["status"] == "CANCELLED"
