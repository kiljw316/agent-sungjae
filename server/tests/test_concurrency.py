"""#10 세션 버전 충돌 / 중복 요청 방지."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm.exc import StaleDataError

from server.app.services.session_service import SessionService
from server.tests.support import new_session, say


def test_expected_version_mismatch_returns_409(client: TestClient) -> None:
    s = new_session(client)
    sid, v = s["sessionId"], s["version"]
    assert client.post(f"/api/cooking-sessions/{sid}/next", json={"expectedVersion": v}).status_code == 200
    dup = client.post(f"/api/cooking-sessions/{sid}/next", json={"expectedVersion": v})  # duplicate "다음"
    assert dup.status_code == 409
    body = dup.json()
    assert body["error"] == "VERSION_CONFLICT" and body["details"]["currentVersion"] == v + 1
    assert client.get(f"/api/cooking-sessions/{sid}").json()["currentStepPosition"] == 2


def test_command_with_stale_version(client: TestClient) -> None:
    s = new_session(client)
    sid, v = s["sessionId"], s["version"]
    say(client, sid, "다음 단계", expectedVersion=v)
    r = client.post(f"/api/cooking-sessions/{sid}/commands", json={"text": "다음 단계", "expectedVersion": v})
    assert r.status_code == 409
    # read-only commands ignore stale versions
    assert client.post(f"/api/cooking-sessions/{sid}/commands", json={"text": "다시 설명해 줘", "expectedVersion": v}).status_code == 200


def test_idempotency_key_replays_instead_of_advancing_twice(client: TestClient) -> None:
    s = new_session(client)
    sid = s["sessionId"]
    h = {"Idempotency-Key": "abc-1"}
    r1 = client.post(f"/api/cooking-sessions/{sid}/next", headers=h)
    r2 = client.post(f"/api/cooking-sessions/{sid}/next", headers=h)
    assert r1.status_code == r2.status_code == 200
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert r1.json() == r2.json()
    assert client.get(f"/api/cooking-sessions/{sid}").json()["currentStepPosition"] == 2


def test_concurrent_writers_hit_optimistic_lock(make_container) -> None:  # type: ignore[no-untyped-def]
    """Two transactions load the same version; the second UPDATE must fail."""
    c = make_container()
    with c.uow() as db:
        from server.app.schemas.recipe import RecipeImportRequest
        from server.app.services.recipe_service import RecipeService
        from server.tests.support import RECIPE_URL

        rid = RecipeService(db, c.clock, c.fetcher).import_recipe(RecipeImportRequest(url=RECIPE_URL)).id
        RecipeService(db, c.clock, c.fetcher).confirm(rid)
        sid = SessionService(db, c.clock).create(rid).id

    db1, db2 = c.session_factory(), c.session_factory()
    s1, s2 = SessionService(db1, c.clock), SessionService(db2, c.clock)
    a, b = s1.load(sid), s2.load(sid)
    s1.start(a)
    db1.commit()
    s2.start(b)
    with pytest.raises(StaleDataError):
        db2.commit()
    db1.close()
    db2.close()
