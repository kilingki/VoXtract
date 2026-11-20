# 결정 기록

## 채택한 초기 구성

| 선택 | 근거 | 대안과 현재 기각 사유 |
| --- | --- | --- |
| Python 3.12+ / FastAPI | Python 서비스와 연동 용이, 요청 검증·API 명세를 점진 추가 가능 | stdlib HTTP는 향후 업로드·검증 직접 구성 필요. Flask도 가능하나 API 중심 흐름에 FastAPI 선택. Django는 규모가 큼 |
| Uvicorn 한 프로세스 | 앱과 향후 단일 실행기를 함께 관리 | 다중 worker·별도 큐 서비스는 동시성·운영 부담 증가 |
| 표준 HTML/CSS/JavaScript | 파일 선택·fetch·반응형 UI를 빌드 없이 구성, PWA 확장 가능 | React/Vue/Vite는 현재 UI에 추가 빌드·의존성 관리 필요. 화면 상태 복잡화 시 재평가 |
| 같은 출처의 UI/API | 서버·포트 하나, CORS 설정 불필요 | 별도 프런트엔드 서버 불필요 |
| uv / pyproject.toml / uv.lock | 기존 WSL에 uv 사용 가능, 의존성 잠금으로 재현성 관리 | pip+venv도 가능하나 별도 핀 생성 절차 필요. Poetry는 추가 도구 부담 |
| unittest와 실제 HTTP 검사 | 추가 테스트 패키지 없이 실행 골격 검증 | pytest/httpx는 실제 필요가 생길 때 평가 |
| 파일 기반 작업 (설계) | 단일 사용자·순차 처리에 DB 운영 불필요 | SQLite는 조회·동시성 요구 증가 시 검토. Redis/Celery는 현재 과도함 |
| 상태 폴링 우선 (설계) | 긴 작업 상태 조회와 재접속에 단순함 | WebSocket/SSE는 실시간 전송 필요 시 검토 |

FastAPI 실행·정적 제공은 [공식 서버 실행](https://fastapi.tiangolo.com/deployment/manually/)과 [정적 파일 문서](https://fastapi.tiangolo.com/tutorial/static-files/)를 참고했다. 의존성 범위는 의도 없는 주요 버전 변경을 줄이기 위한 초기 선택이며 실제 설치 버전은 uv.lock이 기준이다.

## 미결정 사항

- InferSwap 주소·인증·모델 ID·라우트·요청/응답·동기/비동기 방식, 런타임 버전과 지원 계약.
- 오디오 전달 방식, 갤럭시 녹음 포맷, 길이·크기 제한, 디코딩·리샘플링 필요성. ffmpeg 등은 확인 후 도입.
- ASR→FA 텍스트 정규화·언어 처리·단어 정의·시간 단위, 결과 스키마와 버전.
- 외부 작업 조회·취소·멱등성, 타임아웃·재시도 상한·단계 재개 기준.
- 디스크 용량, 보존 기간, 수동 삭제, 임시 파일 청소 시점.
- MVP 내보내기 형식 (TXT/JSON 등 필요한 최소 조합), SRT/VTT 지원 여부.
- Tailscale HTTPS와 Windows/WSL 연결 경로, 대상 모바일 브라우저 버전, PWA 설치·캐시 세부 정책.

새 결정은 실제 계약이나 검증 근거와 함께 이 문서를 갱신한다. 중요한 미결정 사항은 구현 편의를 위해 추정하여 고정하지 않는다.
