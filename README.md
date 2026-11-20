# VoXtract

음성·미디어 입력을 로컬 PC에서 전사하고 단어별 시간을 정렬하는 개인용 웹 애플리케이션이다. 초기 MVP는 갤럭시 녹음 업로드와 Qwen ASR → Qwen FA로 시작한다. 이후 다른 오디오·MP4 등 동영상 파일, YouTube 링크 입력, 작업별 ASR 런타임 선택으로 확장한다. 기존 InferSwap과 추론 런타임은 별도 저장소·프로세스로 유지한다.

현재는 **실행 가능한 초기 골격**이다. 안내 화면, 앱 상태 확인, HTTP 스모크 검사만 제공한다. 업로드·작업 관리·ASR/FA·결과·내보내기·PWA·Tailscale·미디어/링크 입력·런타임 선택은 미구현이다. 상태 확인 성공은 추론 서비스 준비 상태를 뜻하지 않는다.

## 구조

```text
backend/app/
  main.py             # HTTP 앱, 정적 화면, /api/health
  __main__.py         # 단일 프로세스 실행
  services/           # 향후 작업 생명주기·ASR→FA
  integrations/       # 향후 검증된 InferSwap API 어댑터
  storage/            # 향후 파일 기반 저장
frontend/             # 빌드 없는 HTML/CSS/JavaScript
data/                 # 향후 비공개 원본·작업·결과 (Git 제외)
tests/                # 실제 HTTP 스모크 검사
docs/                 # 설계·계획·결정 기록
```

예약 패키지에는 구현이 없다. 프로젝트 루트에서 실행하며 같은 서버가 프런트엔드를 제공한다.

## 개발 환경 및 실행

권장 환경은 로컬 PC의 Ubuntu/WSL2, Python 3.12, uv이다. Node.js, Docker, DB, 유료 서비스는 필요 없다. Windows 네이티브 Python에서도 같은 실행 명령을 사용할 수 있으나 이번 검증 환경은 WSL2이다.

```sh
uv sync --locked
uv run --locked python -m backend.app
```

브라우저에서 [http://127.0.0.1:8000](http://127.0.0.1:8000)을 연다. 종료는 Ctrl+C. 기본 바인딩은 PC 로컬 접속용이다. 스마트폰 연결과 HTTPS는 후속 단계다.

환경변수는 선택 사항이며 기본값으로 실행할 수 있다. .env 자동 로딩은 하지 않는다. WSL/bash에서 직접 작성한 파일을 명시적으로 읽는다.

```sh
cp .env.example .env
set -a
. ./.env
set +a
uv run --locked python -m backend.app
```

Windows PowerShell에서는 직접 지정한다.

```powershell
$env:VOXTRACT_HOST = "127.0.0.1"
$env:VOXTRACT_PORT = "8000"
uv run --locked python -m backend.app
```

uv.lock은 재현 가능한 설치를 위해 보관한다. 의도적으로 의존성을 바꿀 때만 `uv lock`으로 갱신한다. 직접 의존성은 FastAPI와 Uvicorn 두 개다.

## 검증

```sh
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m compileall -q backend tests
```

검사는 임시 loopback 포트에 실제 서버를 실행하여 상태 API, HTML/CSS/JS 제공, 데이터 경로 비공개를 확인하고 종료한다. 실제 추론과 스마트폰 호환성은 검증 대상이 아니다.

## 설계 문서

- [아키텍처](docs/architecture.md): 책임·흐름·실패와 복구
- [로드맵](docs/roadmap.md): 구현 순서와 완료 조건
- [결정 기록](docs/decisions.md): 기술 선택과 미결정 항목
- [연동 계약 기록](docs/integration-contracts.md): 1단계에서 조사한 InferSwap·ASR·FA 계약, 근거 버전과 미검증 항목
- [개발 규칙](AGENTS.md): 사람과 AI Agent의 작업 기준

## 라이선스

[MIT License](LICENSE) · Copyright (c) 2026 kilingki
