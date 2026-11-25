"""Polite single-page HTML fetcher. Honors robots.txt; no auth bypass, no retries to evade blocks."""

from __future__ import annotations

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from server.app.parsers.errors import RecipeFetchError, RecipeFetchForbidden

MAX_BYTES = 5 * 1024 * 1024


class HtmlFetcher:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float,
        respect_robots: bool = True,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._timeout = timeout
        self._respect_robots = respect_robots
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={"User-Agent": self._user_agent, "Accept-Language": "ko-KR,ko;q=0.9"},
            timeout=self._timeout,
            follow_redirects=True,
            transport=self._transport,
        )

    def _check_robots(self, client: httpx.Client, url: str) -> None:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        try:
            resp = client.get(robots_url)
        except httpx.HTTPError:
            return  # robots.txt unreachable → treat as no restrictions (RFC 9309 §2.3.1.3 for 4xx; network errors: lenient)
        if resp.status_code >= 400:
            return
        parser = RobotFileParser()
        parser.parse(resp.text.splitlines())
        if not parser.can_fetch(self._user_agent, url):
            raise RecipeFetchForbidden(f"robots.txt 정책상 이 URL은 수집할 수 없습니다: {url}")

    def fetch(self, url: str) -> str:
        with self._client() as client:
            if self._respect_robots:
                self._check_robots(client, url)
            try:
                resp = client.get(url)
            except httpx.TimeoutException as exc:
                raise RecipeFetchError(f"레시피 페이지 응답 시간 초과: {url}") from exc
            except httpx.HTTPError as exc:
                raise RecipeFetchError(f"레시피 페이지에 연결할 수 없습니다: {exc}") from exc
            if resp.status_code in (401, 403):
                raise RecipeFetchForbidden(f"사이트가 접근을 거부했습니다 (HTTP {resp.status_code}). 텍스트 입력 경로를 사용하세요.")
            if resp.status_code >= 400:
                raise RecipeFetchError(f"레시피 페이지를 가져오지 못했습니다 (HTTP {resp.status_code}).")
            if len(resp.content) > MAX_BYTES:
                raise RecipeFetchError("페이지가 너무 큽니다.")
            return resp.text
