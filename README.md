# VoXtract

음성·미디어 입력을 로컬 PC에서 전사하고 단어별 시간을 정렬하는 개인용 웹 애플리케이션이다. 초기 MVP는 갤럭시 녹음 업로드와 Qwen ASR → Qwen FA로 시작한다. 이후 다른 오디오·MP4 등 동영상 파일, YouTube 링크 입력, 작업별 ASR 런타임 선택으로 확장한다. 기존 InferSwap과 추론 런타임은 별도 저장소·프로세스로 유지한다.

현재는 **녹음 업로드 및 대기 작업 관리**를 제공한다. 접수된 작업은 앱을 재시작해도 유지되며 화면에서 삭제할 수 있다. ASR→FA 실행, 결과와 내보내기, 스마트폰 외부 접속은 후속 단계다.

## 구조

```text
backend/app/
  main.py             # HTTP 앱, 정적 화면, 상태 및 작업 API
  __main__.py         # 단일 프로세스 실행
  services/           # 업로드 조정·오디오 검증
  integrations/       # 향후 검증된 InferSwap API 어댑터
  storage/            # 비공개 파일 기반 대기 큐
frontend/             # 빌드 없는 HTML/CSS/JavaScript
data/                 # 비공개 녹음 원본과 작업 메타데이터 (Git 제외)
tests/                # 실제 HTTP 및 저장·복구 검사
docs/                 # 설계·계획·결정 기록
```

프로젝트 루트에서 실행하며 같은 서버가 프런트엔드를 제공한다.

## 개발 환경 및 실행

권장 환경은 로컬 PC의 Ubuntu/WSL2, Python 3.12, uv이다. 업로드 검증에는 서버의 PATH에서 `ffprobe`와 `ffmpeg`를 실행할 수 있어야 한다. Windows 네이티브 Python에서도 같은 실행 명령을 사용할 수 있으며, 이번 검증 환경은 WSL2이다.

```sh
uv sync --locked
uv run --locked python -m backend.app
```

브라우저에서 [http://127.0.0.1:8000](http://127.0.0.1:8000)을 연다. 종료는 Ctrl+C. 기본 바인딩은 PC 로컬 접속용이다. 스마트폰 연결과 HTTPS는 후속 단계다.

PC에 있는 M4A(AAC) 또는 WAV(PCM) 녹음을 골라 업로드한다. 오디오 길이는 0초 초과·2시간 이하여야 한다. 접수한 파일과 작업 정보는 `data/jobs/`에 저장한다. 파일 크기와 저장 총량의 고정 상한 없이 디스크 여유 1 GiB를 확보한다.

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

검사는 임시 저장소와 loopback HTTP 서버에서 진행한다. 생성한 WAV·M4A로 접수와 조회, 2시간 경계, 잘못된 입력, 중단·디스크 부족, 재시작 복구, 삭제, 데이터 경로 비공개를 검사한다. 실제 갤럭시에서 확인한 범위는 [로드맵](docs/roadmap.md#2단계-진행-상태)에 기록한다.

## 설계 문서

- [아키텍처](docs/architecture.md): 책임·흐름·실패와 복구
- [로드맵](docs/roadmap.md): 구현 순서와 완료 조건
- [결정 기록](docs/decisions.md): 기술 선택과 미결정 항목
- [연동 계약 기록](docs/integration-contracts.md): 1단계에서 조사한 InferSwap·ASR·FA 계약, 근거 버전과 미검증 항목
- [개발 규칙](AGENTS.md): 사람과 AI Agent의 작업 기준

## 라이선스

[MIT License](LICENSE) · Copyright (c) 2026 kilingki
