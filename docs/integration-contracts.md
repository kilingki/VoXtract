# 1단계 연동 계약 확인 기록

조사일: 2026-11-21 (KST). 범위는 로컬 저장소·설정의 읽기 조사와 상태 조회 GET이다. 서비스 시작·재설정, 모델 로드·언로드, 추론, 기존 저장소 수정은 수행하지 않았다. 이 문서는 외부 계약의 기준이며 정책은 [결정 기록](decisions.md), 앱 구조는 [아키텍처](architecture.md)를 따른다.

## 근거와 확인 수준

로컬 workspace의 다음 세 저장소를 읽었다. 아래 링크는 조사한 커밋을 고정한다. 소스가 존재한다는 사실은 실행 중인 서비스가 그 버전을 사용한다는 증거가 아니다.

| 저장소 | HEAD | 작업 트리와 주요 근거 |
| --- | --- | --- |
| InferSwap | `2c3d462a457029fca1b0856e5f5d42f040085e19` | `internal/router/base.go` 수정, `internal/router/backend_settle_test.go`·`run.sh` 미추적. [공개 API](https://github.com/kilingki/InferSwap/blob/2c3d462a457029fca1b0856e5f5d42f040085e19/internal/server/server.go#L33), [설정 예시](https://github.com/kilingki/InferSwap/blob/2c3d462a457029fca1b0856e5f5d42f040085e19/config.example.yaml), [연동 스크립트](https://github.com/kilingki/InferSwap/blob/2c3d462a457029fca1b0856e5f5d42f040085e19/tests/test_asr_then_fa.py) |
| qwen-asr-vllm-docker-config | `dc587c52bc843a631e5d496d26dd4c29f3c2fd75` | 추적 파일 변경 없음. [HTTP 계약](https://github.com/kilingki/qwen-asr-vllm-docker-config/blob/dc587c52bc843a631e5d496d26dd4c29f3c2fd75/asr-api/app/main.py#L107), [청크 응답](https://github.com/kilingki/qwen-asr-vllm-docker-config/blob/dc587c52bc843a631e5d496d26dd4c29f3c2fd75/asr-api/app/pipeline.py#L83), [WAV 검사](https://github.com/kilingki/qwen-asr-vllm-docker-config/blob/dc587c52bc843a631e5d496d26dd4c29f3c2fd75/asr-api/app/audio.py#L150) |
| qwen-fa-docker-config | `c6dee6b707992da3f5cf447d9bffea2636ff1431` | 추적 파일 변경 없음. [HTTP·취소 처리](https://github.com/kilingki/qwen-fa-docker-config/blob/c6dee6b707992da3f5cf447d9bffea2636ff1431/fa-api/app/main.py), [입력 스키마](https://github.com/kilingki/qwen-fa-docker-config/blob/c6dee6b707992da3f5cf447d9bffea2636ff1431/fa-api/app/schemas.py), [검증·시간 매핑](https://github.com/kilingki/qwen-fa-docker-config/blob/c6dee6b707992da3f5cf447d9bffea2636ff1431/fa-api/app/aligner.py) |

InferSwap의 미커밋 변경은 backend 상태가 아직 busy이거나 조회 실패이면 같은 quiet generation을 재조회하는 수정이다. HEAD의 보장으로 취급하지 않는다. 조사한 `internal/router/base.go`의 SHA-256은 `cb74a01c84b6db721ef9d6539d6ea9b46b0c5694e8fc9626d3ac135559e4e19d`이다. 미추적 테스트·실행 스크립트를 계약 근거로 사용하거나 실행하지 않았다.

### 로컬 설정과 실행 상태

비밀값을 포함할 수 있는 설정 전체를 복제하지 않고 필요한 키만 읽었다.

| 항목 | 확인한 설정 | 확인의 한계 |
| --- | --- | --- |
| InferSwap `config.yaml` | `listen=:8095`; `qwen-asr` → `http://127.0.0.1:8080`의 `/v1/audio/transcriptions`; `qwen-fa` → `http://127.0.0.1:8090`의 `/align`; 두 모델 `maxBodyBytes=1073741824` | 실행 중 InferSwap 프로세스를 발견하지 못해 이 파일이 실제 실행에 선택됐는지는 미확인 |
| ASR `.env` | `ASR_API_PORT=8080`, `ASR_MODEL=qwen3-asr`, `DEFAULT_LANGUAGE=ko`, 청크 120초·겹침 2초·내부 동시성 2, 내부 요청 제한 600초 | 실행 환경·이미지의 적용값으로 검증하지 못함 |
| FA `.env` | `FA_API_PORT=8090`, `FA_DTYPE=bfloat16`, `FA_BATCH_SIZE=4`, `FA_MAX_CHUNK_SECONDS=180` | 실행 환경·이미지의 적용값으로 검증하지 못함 |
| 상태 GET | InferSwap `/health`, ASR·FA `/control/status`를 각 3초 제한으로 조회 | 세 요청 모두 시간 초과. 시간 초과만으로 서비스의 상태·실패 원인을 단정하지 않음 |
| Docker 상태 | ASR·FA 컨테이너가 모두 `Exited (255)` | 읽기 시점 상태이며 종료 원인은 조사 범위 밖. 컨테이너를 시작하지 않음 |

로컬 InferSwap 설정 파일의 SHA-256은 `8d65e0291ee65d181d9b2ba849a46e11956f9b495f2520652ebeb2a7d60a3fc6`이다. 개발 도구는 WSL Ubuntu의 Python 3.12.3·uv·ffmpeg·ffprobe 존재를 확인했다. 설치된 ffmpeg가 실제 갤럭시 파일을 해독하는지는 샘플로 검증해야 한다.

## InferSwap 공개 계약

근거: `internal/server/server.go:33–101`, `internal/runtime/client.go:705–727`, `README.md:115–196,227–233`.

| 항목 | 소스로 확인한 계약 |
| --- | --- |
| 추론 요청 | `POST /infer?model=<등록 ID 또는 별칭>`. 모델 선택은 query이며 본문의 `model`을 선택에 사용하지 않음 |
| 모델 ID | 조사한 설정의 canonical ID는 `qwen-asr`, `qwen-fa`. ASR multipart의 served name `qwen3-asr`와 구분 |
| 전달 | `model` query 제거, 등록된 inferencePath로 경로 변경. 다른 query와 본문 bytes를 그대로 전달. 공유 파일 경로·컨테이너 경로 전달 불필요 |
| 완료 | 하나의 HTTP 요청으로 대기·필요한 lifecycle·추론 응답까지 수행. 비동기 job ID를 반환하는 API가 아님 |
| 상태 | `GET /health`: 빈 본문 200, 프로세스 생존 의미. `GET /v1/models`: 모델 목록·큐 깊이·GPU와 모델 상태. 작업별 실행·완료 조회가 아님 |
| 인증 | 조사한 공개 Handler에 인증 검증 없음. 외부 프록시·실제 네트워크 인증 유무는 별도 운영 확인 대상 |
| 요청 크기 | 모델별 maxBodyBytes, multipart 전체를 포함. 현재 구현은 `io.ReadAll`로 본문을 메모리에 읽고 전달하므로 1 GiB 설정을 VoXtract 업로드 허용량으로 그대로 쓰지 않음 |
| 취소·중복 | 공개 작업 취소·작업 ID 조회·멱등 키 저장/중복 제거 API 없음. HTTP 취소가 런타임 작업의 강제 종료를 뜻하지 않음 |
| lifecycle | load/unload는 InferSwap이 호출. VoXtract는 모델 로드·언로드를 직접 호출하지 않음 |

확인한 로컬 전역 timeout은 queue 180초, prepare 60초, load/ready 120초, unload 30초, status 5초, drain 180초다. 이 값들은 전체 추론 제한과 다르다. 프록시 설정에서 전체 추론 deadline을 확인하지 못했으며 ASR의 600초는 내부 **청크별 HTTP 요청** 제한이다. 기존 연동 스크립트의 클라이언트 제한 7200초도 운영 SLA나 검증된 처리시간이 아니다.

InferSwap 자체 오류는 `{"error":"...","src":"inferswap","code":"..."}` 형태다. `400 BAD_REQUEST`, `404 NOT_FOUND`, `413 BODY_TOO_LARGE`, `429 QUEUE_FULL`(`Retry-After: 1`), `502 LOAD_FAILED/UNLOAD_FAILED/BACKEND`, `503 RESOURCES/NOT_READY/LIFECYCLE_PENDING/SHUTDOWN`, `504 QUEUE_TIMEOUT/DRAIN_TIMEOUT`가 문서화돼 있다. 런타임 응답은 별도 형태이므로 HTTP 코드 하나로 재시도 안전성을 판단하지 않는다. 네트워크 단절·응답 유실·일반 5xx는 실행 여부 불명으로 취급한다.

## Qwen ASR 요청·응답

근거: `asr-api/app/main.py:107–218`, `config.py:21–42`, `audio.py:150–240`, `pipeline.py:83–198`, `formatter.py:8–20`, `README.md:205–247`.

VoXtract가 사용할 요청은 InferSwap의 `model=qwen-asr`에 대한 multipart다.

| 필드 | 사용할 값과 의미 |
| --- | --- |
| `file` | 미리 준비한 16,000 Hz·모노·signed 16-bit PCM WAV의 bytes. header·frame count로 검사하며 확장자·MIME만으로 통과하지 않음 |
| `model` | served name `qwen3-asr` (조사한 로컬 설정 기준) |
| `response_format` | `verbose_json` |
| `include_chunks` | `true`. 이 확장 필드를 사용해야 FA 입력에 필요한 audio·chunks를 받음 |
| `language` | 초기 채택값 `ko`; 정책 결정 기록 참조 |
| 기타 | segment granularity는 기본값. `word` 요청은 400. prompt는 내부 서버로 전달되지만 MVP에서 사용자 입력으로 제공하지 않음. temperature는 수용 후 무시 |

`include_chunks=true`는 이미 canonical WAV인 입력만 허용하며 런타임이 변환하지 않는다. 최소 한 frame이 필요하다. 다른 형식이나 verbose_json 외 응답 형식과의 조합은 400이다. 필수 form 누락·형식 오류는 FastAPI 기본 검증의 422가 될 수 있다. 모델이 준비되지 않으면 503, 처리 예외는 500(`detail`)이다.

응답에는 `task`, `language`, `duration`, `text`, `segments`, `audio`, `chunks`가 있다. `audio`는 sample_rate·channels·num_samples, 각 chunk는 index·start_sample·end_sample·text·language다. sample 범위는 정수의 반열린 구간 `[start_sample,end_sample)`이며 동일 WAV 기준이다. chunk language는 backend와 요청 모두 언어를 주지 않으면 null이 될 수 있다.

`segments` 시간은 ASR 청크 기반 근사치이고 단어 시간이 아니다. `chunks[].text`는 청크 내부 정리 후, 청크 간 병합·겹침 제거 전 텍스트다. 실제 반복 발화도 런타임 정리 규칙으로 사라질 수 있어 원음이나 무음의 증거로 삼지 않는다. FA 입력 전에 chunk text를 수정·중복 제거하지 않는다. 청크 120초·겹침 2초는 조사한 배포 설정이며 ASR 응답의 실제 범위를 저장·검증한다.

### 문서·구현에 근거한 예제 (실제 추론 결과 아님)

아래는 ASR README의 필드와 FA의 스키마·예제를 기준으로 만든 1초짜리 설명용 응답이다. 전사 본문은 합성 예시이며 런타임을 호출해 얻은 fixture가 아니다.

```json
{
  "task": "transcribe",
  "language": "ko",
  "duration": 1.0,
  "text": "안녕하세요",
  "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "안녕하세요", "words": []}],
  "audio": {"sample_rate": 16000, "channels": 1, "num_samples": 16000},
  "chunks": [{"index": 0, "start_sample": 0, "end_sample": 16000, "text": "안녕하세요", "language": "ko"}]
}
```

## Qwen FA 요청·응답

근거: `fa-api/app/main.py:77–124,160–196`, `schemas.py:29–62`, `aligner.py:96–253`, `README.md:135–180`.

InferSwap의 `model=qwen-fa`에 multipart `file`과 **문자열 form 필드** `payload`를 전송한다. file은 ASR에 보낸 바로 그 canonical WAV, payload는 보존한 ASR JSON을 JSON 문자열로 직렬화한 것이다. JSON 파일 업로드 필드나 파일 경로가 아니다. FA는 audio·chunks만 읽고 다른 필드는 무시한다.

- audio는 16000 Hz·1 channel이어야 하며 num_samples가 실제 WAV frame count와 일치해야 한다.
- 각 index는 중복 없는 0 이상의 정수, 범위는 `0 <= start_sample < end_sample <= num_samples`다. 청크 길이는 설정값 이하여야 하며 현재 180초, 설정 가능한 최대도 180초다. FA 런타임에는 전체 오디오 길이 상한이 없다. VoXtract 제한은 별도로 둔다.
- 빈/공백 chunk text는 `skipped_empty_text`, language=null, items=[]로 남는다. 비어 있지 않은 text에는 지원 language가 필요하다. `ko`는 `Korean`으로 정규화된다. 지원 목록은 Korean/Japanese/Chinese/English/Cantonese/French/German/Italian/Portuguese/Russian/Spanish다.
- 응답은 audio, `time_reference="audio_start"`, `overlap_deduplicated=false`, chunks를 포함한다. 각 chunk는 원래 index·범위·text와 language·status·items를 갖고 입력 순서를 유지한다. item은 text·start_time·end_time이다.
- item 시간 단위는 **초**, 이미 업로드 WAV 시작 기준이다. chunk offset을 다시 더하지 않는다. 예: start_sample=1,888,000인 청크의 로컬 0.120초는 응답에서 118.120초다. 근거: `tests/test_alignment_mapping.py:155–187`.
- 잘못된 WAV·payload·범위·메타데이터·언어는 400, 준비 전은 503, 모델·시간 역전 등 정렬 실패는 500이다. 부분 성공을 반환하거나 배치를 줄여 자동 재시도하지 않는다.
- inference lock은 HTTP 취소 후에도 worker가 끝날 때까지 유지된다. FA가 끝내는 것은 청크별 정렬이며 겹침 중복 제거·문장 재구성·자막 생성은 호출자의 책임이다.

설명용 결과의 item은 다음 형태다. 위 합성 ASR 예제에 대한 성공을 주장하지 않는다.

```json
{
  "audio": {"sample_rate": 16000, "channels": 1, "num_samples": 16000},
  "time_reference": "audio_start",
  "overlap_deduplicated": false,
  "chunks": [{"index": 0, "start_sample": 0, "end_sample": 16000, "text": "안녕하세요", "language": "Korean", "status": "aligned", "items": [{"text": "안녕", "start_time": 0.1, "end_time": 0.4}]}]
}
```

언어별 item의 어절·단어 분할 품질과 전사 text와의 일치는 실제 녹음으로 확인해야 한다. 공백으로 text를 나눈 개수와 items 개수가 같다고 가정하지 않는다.

## 확인되지 않은 것과 후속 검증

| 항목 | 후속 처리 |
| --- | --- |
| 실제 실행 버전·포트·네트워크·인증 | 3단계의 명시된 서비스 운영 범위에서 재확인. 현재 소스·설정을 실행 계약으로 확정하지 않음 |
| 갤럭시 M4A의 실제 코덱과 시간축 | 사용자 허가 샘플로 probe·canonical 변환·전체 ASR→FA를 검증. 변환 지연·시작 offset·원본 재생 동기 검토 |
| 겹치는 청크의 최종 items 병합 | 3단계에서 시간과 텍스트를 함께 사용하는 좁은 병합 규칙을 정하고 경계 반복·진짜 반복·불일치·무음을 검사. 외부 raw 결과는 유지 |
| 외부 실행 여부 불명 상태의 해소 | 공개 /v1/models는 작업별 증거도 active_requests 조회도 아님. 운영자가 runtime의 GET /control/status 등으로 실제 idle을 확인하고 명시적으로 해제하는 절차를 3단계에서 검증. VoXtract에 직접 load/unload 기능을 추가하지 않음 |
| ASR 오류 뒤 내부 청크의 종료 | `pipeline.py:184–198`의 asyncio.gather가 한 청크 예외를 먼저 전파하면 다른 청크 작업이 남을 가능성이 있음. `main.py:179–193`은 상위 작업 종료 시 active_requests를 해제하므로 facade의 active_requests=0만으로 내부 vLLM까지 종료했다고 단정할 수 없음. 소스에 따른 위험 추론이며 실제 실패 주입은 미실행. 3단계에서 이 조건의 종료 증거·운영 복구를 검증해야 함 |
| 내부 결과의 세부 JSON 필드·다운로드 스키마 | architecture.md의 내부 결과 경계를 만족하는 최소 버전을 2·3단계에서 확정. 공통 플러그인 체계는 만들지 않음 |
| 실제 처리 시간·추론 timeout·품질 | 3단계 승인 샘플 검증 전 수치적 보장 없음. timeout·연결 단절 시 자동 재전송 금지 |

## 이번 조사·검증 범위

소스·README·로컬 설정 예시를 교차 확인했다. FA의 기존 fake-model 계약 테스트가 잘못된 WAV·언어·범위, 전역 offset 1회 적용, 겹침 유지, 취소 후 lock과 임시 파일 유지 등을 다루는 것은 확인했으나 외부 저장소 테스트를 실행하지는 않았다. InferSwap의 실제 추론 스크립트와 모델 lifecycle 검사는 실행하지 않았다.

VoXtract 자체 검사 결과는 작업 완료 시 [로드맵의 1단계 상태](roadmap.md#1단계-진행-상태)에 기록한다. 이번 단계는 계약 조사이며 업로드·오디오 준비·추론 어댑터·결과 스키마 코드를 구현하지 않는다.
