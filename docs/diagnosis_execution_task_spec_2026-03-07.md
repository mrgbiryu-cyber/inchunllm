# 기업 진단 플로우 전환 실행 태스크 명세서

작성일: 2026-03-07

## 1. 문서 목적

이 문서는 `inchunllm` 로컬 코드베이스에서 기업 진단 플로우 전환 작업을 수행한 뒤,
현재 운영 중인 실서버 환경에 안전하게 업데이트하기 위한 실행 태스크 문서이다.

목표는 다음 3가지를 동시에 만족하는 것이다.

1. BM 진단 선행 -> 사업계획서 추천/확정 -> 추가 생성/다른 업체 진단 흐름으로 전환
2. 기존 DB 기반 템플릿 선택 구조와 충돌 없이 사업계획서 4종을 계속 사용
3. 실서버 반영 후 서버 측 Codex가 이 문서만 보고 수정/검증을 이어갈 수 있도록 가이드 제공

## 2. 전환 목표

### 2.1 최종 사용자 플로우

1. 사용자 최초 입력
   - 파일 업로드
   - 직접 입력
   - 회사 URL 입력
2. 시스템이 초기 기업 프로필 생성
3. `BM진단 보고서` 자동 생성
4. BM 진단 기준 부족 항목만 추가 질문
5. 시스템이 사업계획서 템플릿 추천
6. 사용자 확인
   - 긍정: 추천 템플릿으로 사업계획서 생성
   - 부정: 대안 템플릿 제시
   - 특정 다른 양식 요청: 해당 양식으로 생성
7. 사업계획서 생성 후 후속 질문
   - 추가 사업계획서 생성 여부
   - 다른 업체 추가 BM 진단 여부
8. 결과 보기/수정/확정/PDF/아카이빙

### 2.2 템플릿 운용 원칙

- `BM진단 보고서`는 선행 공통 진단서로 승격
- 사업계획서 4종은 기존 DB 템플릿을 그대로 사용
- 자동 추천은 `stage` 문자열 단독이 아니라 최종적으로 `template_id`로 귀결
- 실제 생성 전에 반드시 사용자 확인 단계를 둔다

## 3. 현재 구조 기준 전환 포인트

### 3.1 유지할 구조

- DB `growth_templates` 테이블
- `artifact_type=business_plan` 기반 템플릿 렌더 흐름
- `active_template_id` 기반 선택 구조
- 관리자 템플릿 관리 API

### 3.2 변경할 구조

- `bm_diagnosis`를 부가 문서가 아닌 선행 진단 artifact로 승격
- 사업계획서 자동 선택 로직을 BM 진단 결과 기반 추천 로직으로 교체
- 파일 업로드 후 knowledge ingestion을 preview 기반에서 chunk 기반으로 전환
- 최초 입력 채널을 파일/직접입력/URL 3종으로 표준화

## 4. 핵심 수정 태스크

### P0. 플로우 전환 핵심

#### P0-1. BM 진단 선행 플로우 추가

목표:
- 모든 진단 세션에서 BM 진단을 먼저 생성한다.

작업:
- 최초 입력 채널 3종 정리
  - 파일 업로드
  - 직접 입력
  - 회사 URL 입력
- 초기 기업 프로필 생성 로직 추가
- BM 진단 생성 호출을 선행 단계로 재배치

대상 파일:
- `backend/app/services/growth_support_service.py`
- `backend/app/services/intent_router.py`
- `frontend/src/components/chat/ChatInterface.tsx`

완료 조건:
- BM 진단이 사업계획서 생성보다 먼저 생성된다.

#### P0-2. 사업계획서 추천 어댑터 추가

목표:
- BM 진단 결과에서 기존 DB 템플릿 중 하나를 추천한다.

원칙:
- `stage`만으로 결정하지 않는다.
- 최종 출력은 `template_id`, `template_name`, `reason`, `alternatives[]`

추천 후보:
- 예비창업패키지
- 초기 창업 사업계획서
- 창업도약패키지
- 예비사회적기업

작업:
- BM 진단 결과 -> 추천 템플릿 규칙 설계
- 추천 결과를 기존 `select_growth_template` 흐름에 연결
- 자동 선택 직전 사용자 확인 메시지 추가

대상 파일:
- `backend/app/services/growth_v1_controls.py`
- `backend/app/api/v1/projects.py`
- `backend/app/services/intent_router.py`

완료 조건:
- 시스템이 `template_id`를 추천하고, 사용자 확인 후 해당 사업계획서를 생성한다.

#### P0-3. 업로드 버튼 실동작 점검/수정

목표:
- 업로드 버튼 클릭 후 파일 업로드가 실제로 완료되고 사용자에게 정상 피드백이 노출된다.

우선 점검:
- `effectiveProjectId` 미설정
- 인증 헤더/토큰
- 프론트 `baseURL` 및 프록시
- 업로드 응답 상태와 사용자 메시지

대상 파일:
- `frontend/src/components/chat/ChatInterface.tsx`
- `frontend/src/lib/axios-config.ts`
- `backend/app/api/v1/files.py`

완료 조건:
- 분할/전체 화면에서 업로드 버튼 동작
- 성공/중복/실패 상태가 사용자에게 명확히 표시

#### P0-4. 문서 ingestion 파이프라인 전환

목표:
- 업로드 문서를 preview가 아닌 문서 chunk 단위로 파싱/저장/임베딩한다.

현재 문제:
- 업로드 후 message preview 기반으로 queue 처리될 위험이 있다.
- 원문 chunk, evidence, source linkage가 약하다.

작업:
- 문서 파싱 결과를 별도 저장 구조로 분리
- `document -> parsed_text -> chunks -> embeddings -> Pinecone -> Neo4j evidence`
- 파일 기반 ingestion과 채팅 기반 knowledge extraction 분리

대상 파일:
- `backend/app/api/v1/files.py`
- `backend/app/services/document_parser_service.py`
- `backend/app/services/knowledge_service.py`
- `backend/app/core/database.py`

완료 조건:
- 문서 원문 chunk 단위로 벡터/지식그래프에 반영된다.

#### P0-5. PDF 파서 개선 검토

목표:
- PDF 파싱 품질을 높이되, 실서버 안정성을 해치지 않는 방식으로 도입한다.

검토 대상:
- `https://github.com/opendataloader-project/opendataloader-pdf`

작업:
- born-digital PDF 기준 PoC
- 현재 `PyPDFLoader`와 결과 비교
- 실패 시 fallback 유지
- 스캔 PDF는 별도 OCR fallback 정책 문서화

대상 파일:
- `backend/app/services/document_parser_service.py`
- 별도 PoC script 또는 diagnostics 문서

완료 조건:
- 교체 여부 또는 병행 운용 여부를 근거와 함께 확정

### P1. 대화/생성 품질

#### P1-1. BM 보강 질문 로직

목표:
- BM 진단에 필요한 최소 질문만 던진다.

규칙:
- 기업 단계 선판별
- 업로드/직접입력/URL에서 확보한 값 재질문 금지
- 기업 수준과 맞지 않는 수치 질문 금지

대상 파일:
- `backend/app/services/intent_router.py`
- `backend/app/services/growth_support_service.py`
- `backend/app/services/response_builder.py`

#### P1-2. 후속 플로우

목표:
- 첫 사업계획서 생성 후 다음 행동을 자연스럽게 유도한다.

후속 질문:
- 추가 사업계획서를 더 만들까요?
- 다른 업체를 추가 BM 진단할까요?

대상 파일:
- `backend/app/services/intent_router.py`
- `frontend/src/components/chat/ChatInterface.tsx`

#### P1-3. 반복 질문/루프 차단

목표:
- 첫 질문으로 회귀하는 루프와 동일 질문 반복을 막는다.

작업:
- 최근 질문 fingerprint
- disambiguation/retry/approval 흐름 재점검
- 파일/기초 정보/세션 메모리 재활용

대상 파일:
- `backend/app/services/intent_router.py`
- `backend/app/services/v32_stream_message_refactored.py`
- `backend/app/services/mes_sync.py`

### P2. UI/편집/표현

#### P2-1. 결과 편집/확정/PDF/아카이빙

목표:
- 웹 기반 실시간 편집기와 최종 확정본 아카이빙 제공

작업:
- draft/final artifact 모델 설계
- autosave
- 최종 확정
- 확정본 PDF 출력
- 아카이빙

#### P2-2. Light Mode 및 시작 UX 개선

목표:
- 밝은 배경, 큰 안내 문구, 존댓말 원칙, 기초정보 입력 화면 제공

대상 파일:
- `frontend/src/app/globals.css`
- `frontend/src/app/(main)/chat/page.tsx`
- `frontend/src/components/chat/ChatInterface.tsx`

#### P2-3. 출처 시각화/정량 시각화

목표:
- 출처 색상/툴팁과 점수 그래프 제공

작업:
- 내부 자료 기반: 초록
- 외부 LLM 기반: 보라
- BM/기초진단 score 시각화

## 5. 사용자 수정 요청사항 반영 체크리스트

### P1

- [ ] 파일 업로드 후 AI 자동 분석 -> 기초진단 + BM 보고서 -> 부족 내용만 질의응답 -> 담당직원 수정 -> PDF 흐름 반영
- [ ] 기업 단계(초기/성장/도약) 선판별 후 수준별 질문 제공
- [ ] 세션 내 대화 이력/기초 정보/파일 정보 재질문 금지

### P2

- [ ] 전체화면/분할화면 Enter 전송 통일
- [ ] 기업 현황 기반 구체적 제안 강화
- [ ] Light Mode 적용
- [ ] 시작 안내 간결화
- [ ] 존댓말 사용 원칙 반영
- [ ] 채팅 시작 전 기업 기초 정보 입력 화면 제공

### P3

- [ ] 러시아어 등 외국어 출력 금지
- [ ] BM 보고서 제목 `[BM진단 보고서]` 고정
- [ ] `정부지원사업비 집행 계획` 제거
- [ ] 사회적 가치 서술 한국어 전용

## 6. 로컬 개발 -> 실서버 반영 시 충돌 포인트

### 6.1 환경 차이

로컬과 실서버는 다음 차이로 인해 동일 코드가 그대로 동작하지 않을 수 있다.

- 백엔드 URL / 프론트 프록시 구성 차이
- Redis / Neo4j / Pinecone / OpenRouter 연결 정보 차이
- 로컬 `.env`와 실서버 `.env` 값 차이
- 서버의 DB 템플릿 데이터 상태 차이
- 파일 저장 경로 및 권한 차이
- 현재 실서버에 이미 실행 중인 background worker/knowledge worker 상태 차이

### 6.2 충돌 위험 항목

#### A. 템플릿 매핑 충돌

위험:
- 로컬 기준 추천 규칙이 실서버 DB의 실제 `template_id` 구성과 다를 수 있음

대응:
- 실서버에서 먼저 `/admin/templates` 목록 확인
- 추천 규칙은 템플릿 이름/코드/메타데이터 기준으로 검증
- 하드코딩된 template name 또는 id 사용 금지

#### B. 업로드 경로/권한 충돌

위험:
- 실서버에서 `data/uploads` 쓰기 권한 또는 경로 구조가 다를 수 있음

대응:
- 실서버 반영 전 업로드 경로 writable 여부 확인
- 정리 정책(TTL/cleanup)과 충돌 여부 확인

#### C. 문서 파서 의존성 충돌

위험:
- `langchain_community`, `unstructured`, `opendataloader-pdf` 등 의존성이 서버에 다를 수 있음

대응:
- 로컬 PoC 후 requirements 차이 검토
- 실서버 적용 전 parser feature flag 또는 fallback 유지

#### D. KG/벡터 적재 충돌

위험:
- 실서버 Pinecone/Neo4j namespace/tenant/project_id 규칙이 로컬과 다를 수 있음

대응:
- 실제 upsert namespace와 project_id normalization 점검
- 문서 chunk ingestion을 기존 message ingestion과 분리 배포

#### E. 프론트 API 경로 충돌

위험:
- 로컬은 `127.0.0.1:8000`, 실서버는 reverse proxy `/api/v1` 경로일 수 있음

대응:
- 실서버 `NEXT_PUBLIC_API_URL` 또는 프록시 설정 점검
- 업로드 포함 모든 multipart 요청 검증

## 7. 실서버 Codex 작업 가이드

실서버의 Codex는 아래 순서로 작업한다.

1. 템플릿 현황 확인
   - `/admin/templates`
   - `artifact_type=business_plan`
   - `is_active`, `is_default`, 실제 `template_id`
2. 업로드 실동작 재현
   - 표준 사용자
   - 관리자
   - 분할화면/전체화면
3. 문서 ingestion 실제 경로 확인
   - 업로드 후 DB message 저장
   - queue 유입
   - Neo4j 반영
   - Pinecone 반영
4. BM 진단 선행 흐름 적용
5. 추천 템플릿 확인 대화 적용
6. 사업계획서 추가 생성 / 다른 업체 BM 진단 후속 플로우 적용
7. 사용자 수정 요청 8개 체크리스트 검증

### 7.1 실서버 Skills 세팅

실서버에서는 로컬 `/mnt/d/project/_skills` 접근 가능 여부가 환경마다 다를 수 있으므로,
반드시 아래 순서로 skills 컨텍스트를 준비한다.

#### A. 시작 규칙

1. 먼저 GitHub skills repo 동기화
   - `https://github.com/mrgbiryu-cyber/skills.git`
2. 아래 문서를 먼저 읽는다
   - `~/.skills/README.md`
   - `~/.skills/LLM_SKILLS_USAGE_GUIDE.md`
   - `~/.skills/LLM_SKILLS_ROUTING.md`
   - `./PROJECT_SKILLS.md` (존재 시)
3. 프로젝트 전용 지침도 함께 읽는다
   - `./docs/diagnosis_execution_task_spec_2026-03-07.md`
   - `./PROJECT_SKILLS.md`
   - `./codex/rules.md`

#### B. 실서버 기본 준비 명령

```bash
if [ ! -d ~/.skills ]; then
  git clone https://github.com/mrgbiryu-cyber/skills.git ~/.skills
else
  git -C ~/.skills pull --ff-only
fi
```

#### C. 최소 로드 스킬

실서버 작업에서 우선 사용하는 스킬은 아래 3개다.

1. `debugging-checklist`
   - 업로드 버튼/파싱/KG 적재/프록시 오류 재현 확인용
2. `refactor-roadmap`
   - BM 선행 플로우 -> 추천 템플릿 -> 후속 플로우 전환 순서 제어용
3. `api-contract-checker`
   - 업로드 API, artifact 조회 API, 템플릿 선택 API의 계약 변경 점검용

프로젝트 로컬 스킬은 필요 시 추가 로드한다.

- `debugging-playbook`
  - 백엔드 장애/연결 문제/스크립트 진단
- `backend-refactor`
  - `intent_router.py`, `growth_support_service.py`, `knowledge_service.py` 대규모 수정 시

#### D. 스킬 fallback 규칙

- `~/.skills`에 실제 스킬 패키지가 없으면:
  - 내장 Codex skills (`debugging-checklist`, `refactor-roadmap`, `api-contract-checker`) 우선 사용
  - 프로젝트 로컬 `codex/skills/*`를 직접 읽어 수동 적용
- `tw-skills-*` 명령이 없으면:
  - git/file workflow만 사용
  - 필요한 스킬 문서만 직접 열어 적용

#### E. 실서버 Codex 시작용 작업 순서

실서버 Codex는 첫 작업에서 아래 순서를 따른다.

1. skills 동기화
2. 필수 문서 읽기
3. 템플릿 DB 상태 확인
4. 업로드 버튼 재현
5. ingestion 경로 재현
6. BM 진단 선행 플로우 수정
7. 추천 template 선택/확인 플로우 수정
8. 후속 플로우 수정
9. 사용자 수정 요청 8개 검증

#### F. 실서버 Codex용 시작 프롬프트 가이드

실서버 Codex는 작업 시작 시 아래 요지를 반드시 유지한다.

- BM 진단을 먼저 생성한다
- 사업계획서 4종은 DB 템플릿을 유지한다
- 추천은 `template_id` 기준으로 한다
- 자동 생성 전 사용자 확인을 거친다
- 업로드 후 문서 chunk ingestion을 확인한다
- 실서버 환경 차이로 인한 프록시/경로/권한 충돌을 먼저 점검한다

### 실서버 Codex 필수 확인 항목

- [ ] 템플릿 row와 실제 추천 규칙이 일치하는가
- [ ] 업로드 후 파서 예외가 사용자에게 노출되는가
- [ ] chunk ingestion이 preview가 아니라 원문 기준으로 처리되는가
- [ ] BM 진단이 사업계획서 생성 전에 항상 실행되는가
- [ ] 추천 확인 없이 사업계획서가 자동 생성되지 않는가
- [ ] 추가 사업계획서 생성/다른 업체 BM 진단 후속 질문이 동작하는가

## 8. 검증 시나리오

### 시나리오 1. 파일 업로드 기반

1. 파일 업로드
2. BM 진단 생성
3. 부족 항목 질문
4. 추천 템플릿 제안
5. 사용자 긍정
6. 사업계획서 생성
7. 추가 사업계획서 생성 여부 질문

### 시나리오 2. URL 기반

1. 회사 URL 입력
2. 크롤링/추출
3. BM 진단 생성
4. 부족 정보 보완
5. 대안 템플릿 요청
6. 다른 사업계획서 생성

### 시나리오 3. 다른 업체 재진단

1. 기존 업체 BM + 사업계획서 생성 완료
2. 사용자 “다른 업체 진단”
3. 새 세션/새 company profile 시작
4. 기존 업체 정보와 혼선 없이 분리

## 9. 종료 조건

다음 조건이 충족되면 본 작업은 완료로 본다.

- [ ] BM 진단 선행 플로우 동작
- [ ] DB 템플릿 매핑 충돌 없이 사업계획서 추천/선택/생성 동작
- [ ] 업로드 버튼과 업로드 후 사용자 피드백 정상 동작
- [ ] 문서 chunk ingestion이 실제로 KG/벡터 적재에 반영
- [ ] PDF 파서 개선안 검토 완료
- [ ] 사용자 수정 요청 8개 전부 체크 완료
- [ ] 실서버 Codex가 본 문서만으로 후속 수정/검증을 이어갈 수 있음
