# Voice Cooking Assistant — MVP

MacBook 한 대에서 동작하는 **음성 기반 AI 요리 비서** 프로토타입입니다.
레시피 URL을 넣으면 서버가 재료·수량·단계를 구조화해 저장하고, Push-to-Talk 음성(또는 키보드)으로 단계를 진행하며
재료 제외·대체·인분 변경을 **현재 요리 세션에만** 반영합니다. 기본 실행에는 API 키나 LLM이 필요 없습니다(규칙 기반 에이전트).

> 결정이 필요한 항목은 [`DECISIONS.md`](DECISIONS.md)에 정리했습니다.

---

## 1. 프로젝트 구조

```
agent-sungjae/
├── server/app/
│   ├── main.py              # FastAPI 앱 (create_app / 지연 생성 app)
│   ├── config.py, db.py, clock.py
│   ├── api/                 # recipes.py, sessions.py, deps.py(UoW·DI), errors.py
│   ├── models/              # SQLAlchemy ORM: Recipe / Session / Ingredient / Step / Change / Timer / Event / Idempotency
│   ├── schemas/             # Pydantic DTO (camelCase on the wire), agent 출력 스키마
│   ├── repositories/        # recipe_repo, session_repo
│   ├── services/            # recipe, session, change(+planner, step_text), timer, command, presenters
│   ├── agents/              # base(인터페이스), rule_based, knowledge, llm_base, ollama_agent, openai_agent, hybrid, factory
│   └── parsers/             # fetcher(robots.txt), jsonld, tenk_recipe(만개의레시피), text_recipe(수동), quantity, ingredient_text
├── server/tests/            # pytest (fixture 기반 + --run-network)
├── client/
│   ├── main.py              # python -m client.main
│   ├── app.py               # CLI 흐름 (비즈니스 로직 없음)
│   ├── inputs.py            # TextInput / VoiceInput(PTT) / ScriptedInput
│   ├── api/client.py        # httpx 클라이언트, 재시도 시 동일 Idempotency-Key
│   ├── audio/               # recorder(sounddevice), stt(faster-whisper), tts(macOS say)
│   ├── ui/console.py
│   └── tests/               # 텍스트 전체 플로우, (--run-voice) say→whisper 왕복
├── fixtures/                # 만개의레시피 6845428 테스트용 축약본 (JSON-LD 有/無)
├── conftest.py, pyproject.toml, .env.example, DECISIONS.md
```

## 2. 주요 설계 결정

| 결정 | 이유 |
|---|---|
| **Recipe Definition ↔ Session 스냅샷 분리** | 세션 생성 시 재료·단계를 `cooking_session_*` 테이블로 복사. 원본 `recipes.data`는 절대 수정하지 않음(테스트로 검증). |
| **변경은 Preview → Apply 2단계** | 변경안(`ChangePlan`: 재료 편집·단계 텍스트 before/after·이미 사용한 단계·경고)을 PROPOSED로 저장, 확인 후 적용. 적용 시점에 세션 버전이 바뀌었으면 **현재 상태로 재계획**(그 사이 완료된 단계는 건드리지 않음). |
| **완료 단계 보호** | COMPLETED 단계는 어떤 경우에도 수정 불가(적용 직전 재검증). 이미 사용된 재료는 `REPLACED`로 기록하지 않고 `ACTIVE + "N단계에서 사용함 · 이후 대파로 대체"` 메모 + 대체 재료 `ADDED`. 모든 사용처가 완료됐으면 변경 거부. |
| **LLM은 제안만** | 에이전트는 `AgentContext`(읽기 전용 스냅샷)만 받고 Pydantic 검증된 객체만 반환. 단계 텍스트 수정은 서버가 기계적으로 수행. LLM이 분류한 상태 변경 의도는 항상 사용자 확인 필요. 실패/타임아웃/스키마 위반 → 세션 불변. |
| **규칙 우선 Hybrid** | 명확한 명령은 정규식 규칙(부정문 우선 판별: "빼지 마" → KEEP), 규칙이 모르는 것만 LLM. LLM 없으면 `knowledge.py`의 안전 대체 목록만 허용, 그 외는 "규칙이 없어요 + 지원 대체 목록" 안내. |
| **낙관적 락 2중** | ① `expectedVersion` 명시 검증(409 `VERSION_CONFLICT`) ② SQLAlchemy `version_id_col`(UPDATE … WHERE version=?) → 동시 커밋 시 StaleDataError → 409. 추가로 `Idempotency-Key` 헤더로 재전송 시 저장된 응답 재생. |
| **요청당 1 트랜잭션** | `Container.uow()` — 명령 처리(분류→변경→단계 이동)가 하나의 커밋. |
| **타이머 = 종료 시각 저장** | `ends_at`만 진실의 원천. 남은 시간은 매 조회 시 계산 → 재시작 후에도 정확. 만료는 폴링 시 감지되어 `TIMER_FINISHED` 이벤트가 정확히 1회 생성. 타이머는 세션 version을 올리지 않음. |
| **수량/시간 날조 금지** | "약간/기호에맞게"는 note로 보존, 단계 시간은 원문에 "N분"이 있을 때만(제육볶음은 8단계 3분 하나). HTML의 "30분 이내"는 상한이라 저장하지 않음. "몇 분 더 볶아?" → 원문에 없으면 "적혀 있지 않다 + 상태 기준(예: '대파 숨이 죽을 때쯤')"으로 답변. |
| **한국어 재료 매칭** | `고추` ≠ `고추장`/`고추가루`, `파` ≠ `양파`를 위해 Hangul look-behind + 복합어 접미사 look-ahead, 긴 별칭 우선. 대체 시 조사 자동 보정(양파가→대파가, 당근을→대파를). |
| **클라이언트는 얇게** | 클라이언트는 입력 수집·전달·표시·TTS·이벤트 폴링만. 의도 해석·검증은 전부 서버. |
| **에코 방지** | TTS는 락으로 단일 재생 + 2초 내 동일 문장 중복 제거, 재생 종료 후 0.25s 대기. 녹음은 `speaker.wait_idle()` 이후에만 시작(PTT이므로 동시 녹음 자체가 없음). |

## 3. 설치

```bash
brew install uv ffmpeg          # ffmpeg는 voice 테스트(say→wav 변환)용, 실사용엔 불필요
cd agent-sungjae
uv sync --extra voice           # 텍스트 모드만이면: uv sync
cp .env.example .env            # 선택
```

* Python 3.12+ (uv가 `.venv`에 3.12 생성). 확인된 버전: FastAPI 0.165, Pydantic 2.14, SQLAlchemy 2.1.9, httpx 0.28, faster-whisper 1.2.1, sounddevice 0.5.6.
* **주의:** PyAV 19는 faster-whisper 1.2.1과 호환되지 않아(`metadata_errors` 인자 제거) `av<19`로 고정했습니다.
* 첫 음성 실행 시 Whisper `small` 모델(~480MB)이 Hugging Face에서 다운로드됩니다.
* 마이크 권한: 처음 녹음 시 터미널 앱에 마이크 권한을 허용해야 합니다 (시스템 설정 › 개인정보 보호 및 보안 › 마이크).

## 4. 서버 실행

```bash
uv run uvicorn server.app.main:app --port 8000 --reload
# Swagger UI: http://127.0.0.1:8000/docs   Health: http://127.0.0.1:8000/health
```

선택적 LLM:
```bash
# Ollama (로컬, 16GB RAM 권장 모델 예: qwen2.5:7b ≈ 4.7GB)
ollama pull qwen2.5:7b
COOKING_AGENT_PROVIDER=ollama uv run uvicorn server.app.main:app --port 8000
# OpenAI (공식 API 키만 지원)
COOKING_AGENT_PROVIDER=openai COOKING_OPENAI_API_KEY=sk-... uv run uvicorn server.app.main:app --port 8000
```
Ollama가 꺼져 있거나 모델이 없어도 서버는 정상 동작하며, `/health`에 `llm: unavailable … rule fallback`으로 표시됩니다.

## 5. Voice Client 실행

```bash
uv run python -m client.main                 # 음성(PTT) 모드: Enter → 말하기 → Enter 또는 1.5초 침묵 시 자동 종료
uv run python -m client.main --text          # 키보드 입력 모드 (STT 없음)
uv run python -m client.main --url https://www.10000recipe.com/recipe/6845428
uv run python -m client.main --no-tts        # 음성 출력 끔
uv run python -m client.main --list-voices   # 한국어 say 음성 목록 (Yuna, Eddy, Flo, …)
```
* 음성 모드에서도 프롬프트에 글자를 입력하면 텍스트로 전송됩니다. `q`로 종료(세션은 저장됨).
* 음성 장치/모델 초기화에 실패하면 자동으로 텍스트 모드로 전환합니다.
* 진행 중인 세션이 있으면 시작 시 "이어서 할까요?"를 물어 복원합니다.
* URL 수집 실패 시 `text`를 입력하면 레시피를 직접 붙여 넣을 수 있습니다(형식은 화면 안내).

## 6. 테스트

```bash
uv run pytest                          # 기본: fixture 기반 105개 (네트워크·마이크 불필요, ~3초)
uv run pytest --run-network            # + 실제 만개의레시피 URL 수집 1개
uv run pytest --run-voice              # + macOS say → faster-whisper 한국어 STT 왕복 5개
```

| 요구 테스트 | 위치 |
|---|---|
| 1 URL 파싱 / 2 구조 검증 | `server/tests/test_parsers.py`, `test_recipe_api.py` |
| 3~6 세션 생성·다음·반복·마지막 완료 | `test_session_flow.py` |
| 7 고추 제외 / 8 양파→대파 / 9 완료 단계 재료 변경 | `test_changes.py` |
| 10 버전 충돌·중복 요청 | `test_concurrency.py` |
| 11 재시작 후 복원 | `test_persistence.py` (새 엔진으로 같은 SQLite 재오픈) |
| 12 타이머 | `test_timers.py` (FakeClock) |
| 13 텍스트 전체 플로우 | `client/tests/test_text_flow.py` (실제 클라이언트 ↔ 실제 앱) |
| 14 LLM 오류 안전 실패 | `test_llm_agents.py` (garbage/스키마 위반/타임아웃/연결 실패) |
| 의도 분류·부정문 | `test_intents.py` (명세 §6 예문 전부) |

## 7. 제육볶음 E2E 데모 (실제로 실행한 결과)

실제 서버(uvicorn) + 실제 URL 수집 + CLI 텍스트 모드:

```
$ uv run python -m client.main --text --no-tts
이름: 제육볶음 만드는법 # 기사식당st 레시피
2인분 · 약 30분 · 출처: https://www.10000recipe.com/recipe/6845428 (10000recipe:jsonld)
재료: 돼지고기앞다리살 1근 · 양파 1개 · 당근 1/3개 · 마늘 1스푼 · 파 약간 · 고추 기호에맞게 · 간장 2스푼 · …
  ⓘ 정확한 수량이 없는 재료: 파, 고추, 후추가루 (원본 표기 그대로 유지)
요리를 시작할까요? > 시작
AI: … 요리를 시작할게요. 총 9단계 중 1단계예요. 팬에 불을 켜지않고 … 돼지앞다리살1근을 넣고 …
> 고추가 없는데 빼도 돼?
AI: 네, 고추는 빼도 괜찮아요. 청양고추로 대체할 수도 있어요. 레시피에서 고추를 제외할까요?
   8단계: 불끄기3분전 나머지 고추와 파등을넣어 …  →  불끄기3분전 나머지 파등을넣어 …
> 응
AI: 고추를 제외하고 진행할게요. 이후 단계에도 반영할게요.
[세션 84e9c0ed | IN_PROGRESS | 단계 1/9 | v3]  변경: 고추 제외
> 양파 대신 대파를 넣을게
AI: 양파 대신 대파를 쓸 수 있어요. 양은 1대 정도로 하세요. 대파는 양파보다 빨리 익고 타기 쉬우니 … 이렇게 바꿀까요?
   6단계: 양파가 반투명해질때쯤 …  →  대파 숨이 죽을 때쯤 …
> 응
AI: 양파 대신 대파를 쓰도록 바꿨어요. 남은 단계는 바뀐 레시피로 안내할게요.
> 3분 타이머 시작
AI: 3분 타이머를 시작했어요.
(— 서버 프로세스 종료 → 재시작 —)
진행 중인 세션이 있어요: 제육볶음 … (IN_PROGRESS, 3/9단계). 이어서 할까요? > 응
AI: 다시 말씀드릴게요. 3단계예요. …
> 다음 단계 ×3
AI: 6단계예요. 대파 숨이 죽을 때쯤 준비된 양념의 2/3을 넣어 볶아줍니다 … ※ 양파 대신 대파를 사용해요.
> 몇 분 더 볶아야 해?
AI: 원본 레시피에는 이 단계의 시간이 적혀 있지 않아요. 지금 단계 설명의 '대파 숨이 죽을 때쯤' 상태를 기준으로 판단해 주세요.
> 돼지고기가 충분히 익었는지 어떻게 알아?
AI: 눈으로 보는 것만으로는 고기 속까지 익었는지 확정할 수 없어요. … 식약처는 중심 온도 75도에서 1분 이상 …
> 다음 단계 ×2
AI: 8단계예요. 불끄기3분전 나머지 파등을넣어 센불로 볶아주시면 끝! ※ 고추는 제외했어요.
> 몇 분 더 볶아야 해?
AI: 원본 레시피 기준으로 이 단계는 약 3분이에요. '3분 타이머 시작'이라고 말하면 타이머를 맞춰 드릴게요.
> 다음 단계 ×2
AI: 모든 단계를 마쳤어요. … 전체 9단계 중 9단계를 진행했어요. 이번 요리에서 바뀐 점은 2가지예요. 1. 고추 제외 2. 양파 → 대파 대체 (대파 1대 정도)
```

명세 예시의 "돼지고기 600g"은 원본에 "1근"으로 되어 있어 **변환 없이 1근 그대로** 안내합니다(수량 날조 금지).

## 8. 구현 상태

### 구현·검증 완료
- 만개의레시피 URL 수집(robots.txt 확인, JSON-LD 우선 + HTML 폴백), 일반 사이트 JSON-LD, 수동 텍스트 입력 경로, 명확한 오류 코드(`RECIPE_FETCH_FAILED`/`DISALLOWED`/`PARSE_FAILED`)
- 레시피 확인(confirm) 후 세션 생성, 세션 스냅샷, READY/IN_PROGRESS/PAUSED/COMPLETED/CANCELLED
- 단계 다음/이전/반복/현재 위치/남은 단계, 일시정지/재개/종료(확인 질문)/취소, 종료 요약
- 재료 제외·대체·인분 변경 + 이후 단계 텍스트·메모 반영, 완료 단계 보호, 이미 사용한 재료 처리
- 규칙 기반 의도 분류(명세 §6 모든 예문, 부정문), 조리 Q&A(중불/센불/약불/뻑뻑/짬/싱거움/탐/물 생김/익힘 안전)
- 낙관적 락·중복 요청 방지·Idempotency-Key, 요청당 트랜잭션, 재시작 복원(서버·클라이언트 모두)
- 타이머 시작/연장/취소/남은 시간/종료 이벤트(폴링), 재시작 후 복원
- 텍스트 CLI 전체 흐름, 서버 다운 시 오류 처리
- STT: faster-whisper small, `say`로 만든 한국어 음성 5개 문장 왕복 인식 일치(파일·numpy 경로), 무음 거부, 인식 ~1.1s(M-series CPU int8)
- TTS: 한국어 음성(Yuna 등) 존재 확인 및 `say -o` 합성 확인

### 구현했으나 이 환경에서 실기기/실서비스로 검증하지 못한 것
- **실제 마이크 녹음(PTT)**: 장치 감지("MacBook Pro 마이크")까지만 확인. 녹음은 사용자 권한·발화가 필요해 직접 실행하지 않았습니다.
- **스피커 실제 재생 / 재생 중 중복·에코 방지 체감 동작**: 코드와 단위 경로만 확인(사용자 스피커로 소리를 내지 않음).
- **Ollama / OpenAI 실제 호출**: 이 Mac에 Ollama 미설치, API 키 없음. 어댑터 계약(요청 형식·응답 검증·실패 처리)은 MockTransport로만 검증.

### 미구현 (MVP 범위 밖 또는 후속)
- 변경 되돌리기(undo), 한 문장에 여러 재료 동시 변경("고추랑 파 빼줘" → 하나씩 안내)
- 실시간/상시 녹음, 웨이크워드, 바지-인(TTS 중 끼어들기)
- 카메라 분석: `agents/base.py`의 `VisionAnalyzer` 인터페이스만 존재(구현·가짜 결과 없음)
- 만개의레시피 외 사이트 전용 파서(JSON-LD 없는 사이트), DB 마이그레이션(Alembic), 인증, AI 안경/모바일/클라우드
