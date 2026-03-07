# CODEX DM Handoff (2026-03-04)

## 0) 목적
- Codex 세션이 끊겨도, 다음 세션에서 바로 이어서 디버깅/검증할 수 있도록 최신 상태와 과거 이슈를 한 문서에 정리한다.
- 이 문서는 "코드 수정 지시"가 아니라 "현황/근거/재현" 중심 DM 기록이다.

## 1) 기준 시각 / 환경
- 기준 시각: 2026-03-04 14:35:54 UTC
- 루트 경로: `/root/inchenmml`
- 브랜치: `main`
- 작업트리: 매우 dirty 상태(수정/신규 파일 다수). 기존 변경은 절대 임의 되돌리지 말 것.

## 2) 사용자 보고 이슈 타임라인 (요약)
1. 자유대화 전환 실패 케이스 존재.
2. 진행률 UI 대신 `{"type":"PIPELINE_PROGRESS"...}` JSON이 채팅에 raw text로 노출.
3. 최종에서 `network error`로 종료되어 검증/생성 완료 실패.
4. PDF 승인 2/4에서 다운로드 시 `409` 에러 문구가 노출됨.
5. 승인 자동 진행(남은 단계 자동 보완) 동작이 체감상 안 됨.
6. 결과 문서가 마크다운 렌더링되지 않고 raw text처럼 보임.
7. 새로고침/방 이동 후 결과 버튼(PDF/결과보기) 상태 유지 이슈.
8. "이어서" 복구가 중간에 끊기거나 질문 수집으로 되돌아가는 케이스.
9. 입력창 힌트가 `undefined ...`로 보이던 문제 제보.
10. 계정 간 채팅방 내용 공유 의심.
11. 신규 test2 계정에서 초기 진입 시 `GET /api/v1/projects/{id}/threads` -> 403.
12. 403 재발 + 자유대화 전환 재실패 재현 제보.

## 3) 현재 코드에서 확인된 반영 사항 (근거 파일)
### 3.1 프론트 렌더링/진행표시
- `ReactMarkdown + remark-gfm` 적용됨.
  - 파일: `frontend/src/components/chat/ChatInterface.tsx`
  - 근거: import 존재, `renderMarkdownMessageContent`에서 `<ReactMarkdown remarkPlugins={[remarkGfm]}>` 사용.
- `PIPELINE_PROGRESS` 신호 파싱/분리 로직 존재.
  - 동일 파일의 `parseStreamSignal`, `extractSignalsFromBuffer`, `stripSignalPayload`.
- 입력창 placeholder는 `텍스트를 입력해주세요.` 로 설정되어 있음.
  - 동일 파일 하단 textarea placeholder.

### 3.2 PDF 409 사용자 문구
- PDF 다운로드 시 409이면 사용자 문구를
  - `PDF 다운로드를 위해 남은 단계를 진행해주세요.`
  로 변환하는 처리 존재.
  - 파일: `frontend/src/components/chat/ChatInterface.tsx`
  - 위치: `openArtifactUrl`의 `catch` 블록.

### 3.3 백엔드 markdown -> html 변환
- 백엔드에 markdown 변환 함수가 존재하며 `tables` 확장 포함.
  - 파일: `backend/app/services/growth_v1_controls.py`
  - 함수: `_to_html`
  - 확장: `extra, tables, fenced_code, sane_lists, nl2br`
- 아티팩트 API의 `format=html`은 `HTMLResponse` 반환.
  - 파일: `backend/app/api/v1/projects.py`

### 3.4 템플릿 5분기(현재 구현)
- 분기 축:
  - `business_plan: 예비/초기/성장/공통`
  - `bm_diagnosis: 공통`
- artifact 타입 분기:
  - 파일: `backend/app/services/v32_stream_message_refactored.py`
  - 함수: `_detect_target_artifact`
  - 키워드(`bm진단`, `bm 진단`, `진단양식`, `비엠진단`)면 `bm_diagnosis`, 아니면 `business_plan`.
- 사업계획서 stage 분기:
  - 파일: `backend/app/services/growth_v1_controls.py`
  - 함수: `set_question_type_from_profile`, `render_business_plan_with_template`, `_use_common_business_plan_template`.

## 4) 아직 미해결 가능성이 높은 지점 (다음 세션 우선 확인)
1. 마크다운이 여전히 raw text로 보이는 현상
- 코드상 ReactMarkdown 적용되어 있어도, 실제 사용 경로가 다른 컴포넌트일 가능성.
- `projects/[projectId]/execute/page.tsx` 와 `chat/ChatInterface.tsx` 모두 렌더 경로 확인 필요.
- 운영 반영 누락(구버전 프론트 프로세스) 가능성도 큼.

2. `GET /projects/{id}/threads` 403 (특히 신규 계정)
- 프로젝트 접근권한(RBAC) 실패일 가능성 높음.
- `get_project_threads` 초반 `await _get_project_or_recover(project_id, current_user)`에서 막히면 403 발생.
- 계정/프로젝트 매핑 및 소유권 부여 로직 점검 필요.

3. 계정 간 thread 공유 의심
- 현재는 `STANDARD_USER`에 대해 `owner_user_id == current_user.id` 필터가 걸려 있음.
- 다만 legacy 데이터에서 `owner_user_id` 비어 있는 thread 백필(`_backfill_thread_owner_for_user`)이 충분히 되지 않으면 과거 데이터 혼선 가능.

4. 자유대화 전환 실패/분류 루프
- 의도 라우팅 + consultation_mode/state reset 경로 재검증 필요.
- 파일: `backend/app/services/intent_router.py`, `v32_stream_message_refactored.py`.

5. network error 이후 이어서 진행
- 프론트에는 `pendingRetryRef` 기반 재시도 안내/복구 코드가 있으나, 서버 스트림 중간 종료 시 상태 일관성 점검 필요.

## 5) E2E 재현 시나리오 (다음 세션 즉시 수행)
1. 계정 분리 검증
- `test` 로그인 -> thread 목록/메시지 확인
- `test2` 로그인 -> 동일 project 진입 시 목록이 분리되는지 확인
- API 직접 확인:
  - `GET /api/v1/projects/{project_id}/threads`
  - 403 여부/응답 body의 detail 확인

2. 분류 -> 예 -> 생성 플로우
- 입력: 사업계획서 요청 -> 분류 카드 확인 -> `예`
- 기대: 진행 UI(로딩/단계) + 결과 카드 + HTML/PDF 버튼 생성
- 실제: raw JSON 노출/빈 말풍선 여부 체크

3. 결과보기 마크다운 렌더
- 표(`|---|`) 포함 응답으로 결과보기 클릭
- 기대: 표 렌더링된 메시지
- 실제: raw text면 어떤 경로(채팅/execute 페이지)인지 구분해 캡처

4. PDF 승인
- 승인 2/4 상태에서 PDF 클릭
- 기대: 사용자 문구 `PDF 다운로드를 위해 남은 단계를 진행해주세요.`
- 승인 완료 후 PDF 정상 다운로드 재확인

5. 중단 복구
- 스트림 중 네트워크 끊김 유도 후 동일 문구 재전송
- 기대: "이어 재시도" 문구 + 정상 완료

## 6) 운영/재시작 명령 (참고)
- 개발 모드 전체 기동: `bash scripts/run_fullstack.sh dev`
- 개발 모드 중지: `bash scripts/stop_fullstack.sh dev`
- 상태 점검: `bash scripts/check_fullstack.sh`
- 백엔드 로그: `.run/backend.log`
- 프론트 로그: `.run/frontend.log`

## 7) 테스트 계정 (사용자 제공)
- `test / !@ssw5740`
- `test2 / !@ssw5740`

## 8) 다음 Codex 세션 시작 체크리스트
1. `git status --short`로 dirty 상태 확인 (기존 변경 보존).
2. `bash scripts/run_fullstack.sh dev`로 동일 환경 재기동.
3. 브라우저에서 test/test2로 403/스레드 분리부터 먼저 확인.
4. 분류->예->생성 플로우에서 `PIPELINE_PROGRESS` raw 노출 재현 여부 확인.
5. 결과보기/markdown/PDF 승인 메시지까지 순서대로 캡처.
6. 원인 파일을 좁힌 뒤 최소 수정으로 패치.

## 9) 핵심 파일 인덱스
- `frontend/src/components/chat/ChatInterface.tsx`
- `frontend/src/app/projects/[projectId]/execute/page.tsx`
- `backend/app/api/v1/projects.py`
- `backend/app/api/dependencies.py`
- `backend/app/services/v32_stream_message_refactored.py`
- `backend/app/services/intent_router.py`
- `backend/app/services/growth_v1_controls.py`
- `backend/app/services/growth_support_service.py`
- `backend/app/services/templates/template_form_mapping.py`

