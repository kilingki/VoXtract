# VoXtract

[English](README.md) · **한국어**

VoXtract는 삼성 갤럭시 음성 녹음 파일 등을 내 PC에서 전사하는 웹 앱입니다. 녹음 파일을 올리고 전사 내용을 검색하거나, 시간 표시를 눌러 원본의 해당 부분을 들을 수 있습니다.

**앱 UI는 한국어입니다.**

## 주요 기능

- 최대 2시간 분량의 M4A(AAC) 또는 WAV(PCM) 녹음을 업로드할 수 있습니다. 서버를 재시작해도 저장한 녹음과 작업이 유지됩니다.
- Qwen3-ASR로 녹음을 전사한 뒤 Qwen3-ForcedAligner로 전사문의 단어를 원본 오디오 시간에 맞춥니다.
- 전사문을 검색하거나 시간 표시를 눌러 이동할 수 있습니다. 재생 중에는 해당 텍스트가 강조됩니다.
- 결과를 오프라인에서 읽을 수 있는 단일 HTML, UTF-8 TXT, 시간 정보와 스키마 버전을 담은 JSON으로 내려받을 수 있습니다.
- 개인 Tailscale 네트워크를 통해 갤럭시에서 접속하고 Chrome으로 홈 화면에 설치할 수 있습니다.

## 빠른 시작

### 웹 앱 실행

검증 환경은 WSL2의 Ubuntu입니다. Python 3.12 이상, `uv`, FFmpeg가 필요합니다. 서버에서 `ffmpeg`와 `ffprobe`를 PATH로 실행할 수 있어야 합니다.

```sh
git clone https://github.com/kilingki/VoXtract.git
cd VoXtract
uv sync --locked
uv run --locked python -m backend.app
```

브라우저에서 [http://127.0.0.1:8000](http://127.0.0.1:8000)을 열고 녹음 파일을 올립니다. 앱은 기본적으로 루프백 주소에서 실행되며 녹음 원본, 작업 정보, 결과를 `data/jobs/`에 저장합니다. 디스크 여유 공간은 1 GiB를 확보합니다.

### 녹음 전사

**전사하려면 아래 세 서비스가 모두 실행 중이어야 하며**, 두 Qwen 런타임을 InferSwap에 등록해야 합니다. 추론 환경을 준비하기 전에도 웹 앱을 열고 녹음을 업로드할 수 있습니다.

| 서비스 | 역할 |
| --- | --- |
| [InferSwap](https://github.com/kilingki/InferSwap) | 추론 요청을 전달하고 GPU 모델의 로드·언로드를 관리합니다. |
| [Qwen3-ASR 런타임](https://github.com/kilingki/qwen-asr-vllm-docker-config) | 녹음을 전사합니다. |
| [Qwen3-ForcedAligner 런타임](https://github.com/kilingki/qwen-fa-docker-config) | 전사문을 원본 오디오 시간에 맞춥니다. |

각 저장소의 설치·실행 안내를 따릅니다. VoXtract가 이미 실행 중이면 종료한 뒤, VoXtract를 실행하는 PC 또는 WSL에서 접속 가능한 InferSwap URL을 지정해 다시 실행합니다. InferSwap의 주소가 예시와 다르면 바꿉니다.

```sh
VOXTRACT_INFERSWAP_URL=http://127.0.0.1:8095 uv run --locked python -m backend.app
```

기본 InferSwap 모델 ID는 `qwen-asr`와 `qwen-fa`이고, ASR의 served name은 `qwen3-asr`입니다. 등록값이 다르면 [환경 변수 예시](.env.example)를 참고합니다.

1. M4A 또는 WAV 파일을 올리거나 작업 목록에서 기존 녹음을 선택합니다.
2. **전사 시작**을 누릅니다. 작업 목록에 준비, 전사, 정렬 진행 상태가 표시됩니다.
3. **결과 보기**를 열어 전사문을 검색하고 원본을 재생하거나 결과를 내려받습니다.

### 검증 환경

갤럭시에서 녹음한 약 52분 분량의 한국어 파일로 전체 흐름을 검증했습니다. 테스트 PC는 Windows와 WSL2 Ubuntu, Intel Core i5-12600K, RAM 64 GB, NVIDIA GeForce RTX 3090(VRAM 24 GB) 환경입니다.

## 갤럭시에서 사용하기

1. Windows PC와 갤럭시를 같은 개인 Tailscale 네트워크에 연결합니다. Windows에서 `http://127.0.0.1:8000/api/health`가 응답하는지 확인합니다.
2. Windows 관리자 터미널에서 아래 명령을 실행합니다. HTTPS 활성화 안내가 나오면 tailnet에서 HTTPS를 활성화합니다. `serve status`에 표시되는 HTTPS 주소에는 tailnet의 접근 규칙이 적용됩니다. [Tailscale Serve 문서](https://tailscale.com/docs/features/tailscale-serve)

   ```powershell
   tailscale serve --bg http://127.0.0.1:8000
   tailscale serve status
   ```

3. 갤럭시 Chrome에서 HTTPS 주소를 열고 **홈 화면에 추가 → 설치**를 선택합니다. 이후 홈 화면의 아이콘으로 VoXtract를 열 수 있습니다.

녹음 업로드, 작업 목록 조회, 원본 재생에는 PC와 VoXtract 서버가 실행 중이어야 합니다. 내려받은 HTML은 오프라인에서 읽을 수 있습니다.

## 개발

VoXtract는 환경 변수로 설정합니다. Bash에서 `.env` 파일을 사용하려면 [.env.example](.env.example)을 `.env`로 복사해 수정한 뒤, 앱을 실행하기 전에 아래 명령으로 불러옵니다.

```sh
set -a
. ./.env
set +a
```

저장소 루트에서 검사를 실행합니다.

```sh
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m compileall -q backend tests
```

테스트는 임시 저장소, 생성한 오디오 파일, 모의 InferSwap 서비스를 사용합니다.

- [개발 규칙](AGENTS.md)

## 라이선스

[MIT](LICENSE) · Copyright (c) 2026 kilingki
