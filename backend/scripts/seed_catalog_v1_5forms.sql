\set ON_ERROR_STOP on

-- 1) 안전한 enum 확장: artifact_type_enum
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_enum e
    JOIN pg_type t ON t.oid = e.enumtypid
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE t.typname = 'artifact_type_enum'
      AND n.nspname = current_schema()
      AND e.enumlabel = 'bm_diagnosis'
  ) THEN
    ALTER TYPE artifact_type_enum ADD VALUE 'bm_diagnosis';
  END IF;
EXCEPTION
  WHEN undefined_object THEN
    -- 운영 DB가 enum 기반으로 아직 마이그레이션 안 된 경우 무시하고 catalog로 진행
    NULL;
END $$;

-- 2) 기본 카탈로그 테이블(없으면 생성)
CREATE TABLE IF NOT EXISTS artifact_type_catalog (
  artifact_type TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS approval_step_catalog (
  artifact_type TEXT PRIMARY KEY,
  steps JSONB NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 3) 필수 테이블/컬럼 방어 확인(미존재 시 강제 실패)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'conversation_state') THEN
    RAISE EXCEPTION 'Required table missing: conversation_state';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'growth_templates') THEN
    RAISE EXCEPTION 'Required table missing: growth_templates';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = 'growth_templates'
      AND column_name = 'source_pdf'
  ) THEN
    ALTER TABLE growth_templates ADD COLUMN source_pdf TEXT;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = 'growth_templates'
      AND column_name = 'sections_keys_ordered'
  ) THEN
    ALTER TABLE growth_templates ADD COLUMN sections_keys_ordered JSONB;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'artifact_approval_state') THEN
    RAISE EXCEPTION 'Required table missing: artifact_approval_state';
  END IF;
END $$;

-- 4) 운영 카탈로그 seed
INSERT INTO artifact_type_catalog (artifact_type, description)
VALUES
  ('business_plan', '사업계획서(PDF/HTML) 템플릿 렌더링 대상'),
  ('matching', '매칭 항목 산출물'),
  ('roadmap', '성장 로드맵 산출물'),
  ('bm_diagnosis', 'BM 진단 및 설계 양식(체크리스트/진단표) 산출물')
ON CONFLICT (artifact_type) DO UPDATE
SET description = EXCLUDED.description;

INSERT INTO approval_step_catalog (artifact_type, steps)
VALUES
  ('business_plan', ' ["key_figures_approved", "certification_path_approved", "template_selected", "summary_confirmed"] '::jsonb),
  ('roadmap', ' ["route_approved"] '::jsonb),
  ('matching', ' ["source_verified"] '::jsonb),
  ('bm_diagnosis', ' ["bm_checklist_completed", "consultant_reviewed"] '::jsonb)
ON CONFLICT (artifact_type) DO UPDATE
SET steps = EXCLUDED.steps;

-- 5) growth_templates seed (5개 템플릿)
INSERT INTO growth_templates (
    id,
    name,
    artifact_type,
    stage,
    version,
    source_pdf,
    sections_keys_ordered,
    template_body,
    is_active,
    is_default
)
VALUES
(
  '11111111-1111-4111-8111-111111111111',
  'pre_startup_2025',
  'business_plan',
  '예비',
  'v1_0',
  '[별첨 1] 2025년도 예비창업패키지 사업계획서 양식.pdf',
  '["general_status","summary_overview","problem_1_market_status_and_issues","problem_2_need_for_development","solution_1_development_plan","solution_2_differentiation_competitiveness","solution_3_gov_fund_execution_plan_stage1","solution_4_gov_fund_execution_plan_stage2","solution_5_schedule_within_agreement","scaleup_1_competitor_analysis_and_entry_strategy","scaleup_2_business_model_revenue","scaleup_3_funding_investment_strategy","scaleup_4_roadmap_and_social_value_plan","scaleup_5_schedule_full_phases","team_1_founder_capability","team_2_team_members_and_hiring_plan","team_3_assets_facilities_and_partners"]'::jsonb,
  $tpl$
# 예비창업패키지 예비창업자 사업계획서 (2025)

> **작성 유의사항**
> - 글자 색상은 검정색으로 작성하세요.
> - 파란색 안내 문구는 삭제 후 작성하세요.
> - 생년월일, 성별 등 민감 개인정보는 마스킹 처리 후 제출하세요.
> - 목차 페이지는 제출 시 삭제하세요.
> - 사업계획서는 10페이지 이내(목차 제외)로 작성하세요.

---

## 목차

1. [문제 인식 (Problem)](#1-문제-인식-problem)
2. [실현 가능성 (Solution)](#2-실현-가능성-solution)
3. [성장 전략 (Scale-up)](#3-성장-전략-scale-up)
4. [팀 구성 (Team)](#4-팀-구성-team)

---

## 일반 현황

| 항목 | 내용 |
|------|------|
| 창업아이템명 | |

**신청인 정보**

| 성명 | 연락처 | 이메일 |
|------|--------|--------|
| | | |

| 생년월일 | 성별 |
|---------|------|
| (마스킹 처리) | (마스킹 처리) |

**창업 단계**

- ☐ 아이디어 단계
- ☐ 시제품 제작 단계
- ☐ 시장 검증 단계
- ☐ 사업화 단계

**팀 구성 현황**

| 성명 | 직위 | 역할 | 보유 역량 |
|------|------|------|----------|
| | | | |
| | | | |
| | | | |

{{ sections_markdown.general_status }}

---

## 사업계획서 요약

**창업아이템 개요**

> (창업 아이템의 핵심 내용을 간략히 서술하세요.)

{{ sections_markdown.summary_overview }}

| 항목 | 내용 |
|------|------|
| 문제 인식 (Problem) | |
| 실현 가능성 (Solution) | |
| 성장 전략 (Scale-up) | |
| 팀 구성 (Team) | |

> 📷 *제품/서비스 이미지 또는 다이어그램 삽입*

---

## 1. 문제 인식 (Problem)

### 1-1. 창업 아이템의 필요성

> (해결하고자 하는 문제/불편함, 시장의 Pain Point를 서술하세요.)

{{ sections_markdown.problem_2_need_for_development }}

**시장 분석**

> (목표 시장 규모, 고객 분석 등을 서술하세요.)

{{ sections_markdown.problem_1_market_status_and_issues }}

---

## 2. 실현 가능성 (Solution)

### 2-1. 사업화 및 구체화 계획

> (제품/서비스의 구체적인 구현 방안과 차별성을 서술하세요.)

{{ sections_markdown.solution_1_development_plan }}

{{ sections_markdown.solution_2_differentiation_competitiveness }}

**추진 일정**

| 구분 | 내용 | 기간 | 세부 사항 |
|------|------|------|----------|
| | | | |
| | | | |
| | | | |
| | | | |

{{ sections_markdown.solution_5_schedule_within_agreement }}

### 2-2. 정부지원사업비 집행 계획

**예산 구성**

| 구분 | 정부지원금 | 자부담 | 합계 |
|------|-----------|--------|------|
| 금액 (원) | | | |
| 비율 (%) | | | |

**1단계 지출 계획**

| 비목 | 세목 | 산출근거 | 금액 (원) |
|------|------|---------|----------|
| 재료비 | | | |
| 외주용역비 | | | |
| 수수료 | | | |
| 기타 | | | |
| **합계** | | | |

{{ sections_markdown.solution_3_gov_fund_execution_plan_stage1 }}

**2단계 지출 계획**

| 비목 | 세목 | 산출근거 | 금액 (원) |
|------|------|---------|----------|
| 재료비 | | | |
| 외주용역비 | | | |
| 수수료 | | | |
| 기타 | | | |
| **합계** | | | |

{{ sections_markdown.solution_4_gov_fund_execution_plan_stage2 }}

---

## 3. 성장 전략 (Scale-up)

### 3-1. 경쟁사 분석 및 시장 진입 전략

> (경쟁사 현황 분석 및 자사의 차별화 전략을 서술하세요.)

{{ sections_markdown.scaleup_1_competitor_analysis_and_entry_strategy }}

### 3-2. BM 및 자금 조달

> (비즈니스 모델 및 향후 자금 조달 계획을 서술하세요.)

{{ sections_markdown.scaleup_2_business_model_revenue }}

{{ sections_markdown.scaleup_3_funding_investment_strategy }}

**사업 로드맵**

| 단계 | 기간 | 목표 | 주요 활동 |
|------|------|------|----------|
| 단기 (1년) | | | |
| 중기 (3년) | | | |
| 장기 (5년) | | | |

{{ sections_markdown.scaleup_5_schedule_full_phases }}

### 3-3. 사회적 가치

> (창업 아이템이 창출하는 사회적 가치를 서술하세요.)

{{ sections_markdown.scaleup_4_roadmap_and_social_value_plan }}

---

## 4. 팀 구성 (Team)

### 4-1. 대표자 역량

> (대표자의 관련 경력, 교육, 전문성 등을 서술하세요.)

{{ sections_markdown.team_1_founder_capability }}

### 4-2. 추가 채용 계획

| 직무 | 인원 | 채용 시기 | 요구 역량 |
|------|------|----------|----------|
| | | | |
| | | | |

{{ sections_markdown.team_2_team_members_and_hiring_plan }}

### 4-3. 외부 협력 계획

> (멘토, 협력기관, 투자사 등 외부 협력 네트워크를 서술하세요.)

{{ sections_markdown.team_3_assets_facilities_and_partners }}
$tpl$,
  TRUE,
  TRUE
),
(
  '22222222-2222-4222-8222-222222222222',
  'early_startup_2023',
  'business_plan',
  '초기',
  'v1_0',
  '[별첨1] 2023년 초기창업패키지 창업기업 사업계획서 양식.pdf',
  '["application_status","general_status","summary_overview","problem_1_background_and_necessity","problem_2_target_market_and_requirements","solution_1_preparation_status","solution_2_realization_and_detail_plan","scaleup_1_business_model_and_results","scaleup_2_market_entry_and_strategy","scaleup_3_schedule_and_fund_plan_roadmap","scaleup_4_schedule_and_fund_plan_within_agreement","scaleup_5_budget_execution_plan","team_1_org_and_capabilities","team_2_current_hires_and_hiring_plan","team_3_external_partners","team_4_esg_mid_long_term_plan"]'::jsonb,
  $tpl$
# 창업사업화 지원사업 사업계획서 [초기단계] (2023)

---

## 신청서 및 일반 현황

**사업분야 (해당 항목 체크)**

| 분야 | | 분야 | | 분야 | |
|------|---|------|---|------|---|
| ☐ 제조업 | | ☐ 에너지 | | ☐ ICT | |
| ☐ 바이오 | | ☐ 문화·콘텐츠 | | ☐ 농식품 | |
| ☐ 환경 | | ☐ 소재·부품 | | ☐ 기타 | |

**기술 분야**

> (주력 기술 분야를 기재하세요.)

{{ sections_markdown.general_status }}

**프로젝트 예산**

| 구분 | 정부지원금 | 대응자금 | 합계 |
|------|-----------|----------|------|
| 금액 (원) | | | |
| 비율 (%) | | | |

**기업 기본 정보**

| 항목 | 내용 | 항목 | 내용 |
|------|------|------|------|
| 기업명 | {{ company_name }} | 대표자 | |
| 설립일 | | 주소 | |
| 종사자 수 | | 최근 매출액 | |

---

## 사업계획서 요약

**아이템명**

> OOO 기술이 적용된 OOO (구체적으로 작성)

{{ sections_markdown.summary_overview }}

**핵심 특장점**

| 특장점 | 내용 |
|--------|------|
| 1. | |
| 2. | |
| 3. | |

**사업 요약**

| 항목 | 내용 |
|------|------|
| 문제 인식 | |
| 솔루션 | |
| 성장 전략 | |
| 팀 역량 | |

---

## 1. 문제 인식 (Problem)

### 1-1. 배경 및 필요성

> (해결하고자 하는 문제와 시장 현황을 서술하세요.)

{{ sections_markdown.problem_1_background_and_necessity }}

### 1-2. 목표 시장 및 고객 분석

> (타겟 시장 규모, 고객 세분화, 주요 고객 불편사항을 서술하세요.)

{{ sections_markdown.problem_2_target_market_and_requirements }}

---

## 2. 실현 가능성 (Solution)

### 2-1. 개발 현황

| 항목 | 현황 |
|------|------|
| 진행 현황 | |
| 기술 현황 | |
| 인프라 현황 | |

> (기술 개발 진행 상황을 상세히 서술하세요.)

{{ sections_markdown.solution_1_preparation_status }}

### 2-2. 구체화 계획 및 경쟁력

> (구현 계획 및 경쟁사 대비 차별점을 서술하세요.)

{{ sections_markdown.solution_2_realization_and_detail_plan }}

**경쟁사 비교**

| 구분 | 자사 | 경쟁사A | 경쟁사B |
|------|------|--------|--------|
| 강점 | | | |
| 약점 | | | |
| 차별화 포인트 | | | |

---

## 3. 성장 전략 (Scale-up)

### 3-1. BM 및 성과 목표

**달성 목표**

| 구분 | 특허 | 매출 | 고용 | 투자 |
|------|------|------|------|------|
| 1년차 | | | | |
| 2년차 | | | | |
| 3년차 | | | | |

{{ sections_markdown.scaleup_1_business_model_and_results }}

### 3-2. 시장 전략 및 로드맵

**국내 진출 계획**

> (국내 시장 진출 전략을 서술하세요.)

{{ sections_markdown.scaleup_2_market_entry_and_strategy }}

**글로벌 진출 계획**

> (해외 시장 진출 전략을 서술하세요.)

{{ sections_markdown.scaleup_2_market_entry_and_strategy }}

**사업 로드맵**

| 단계 | 기간 | 주요 목표 | 세부 활동 |
|------|------|----------|----------|
| 단기 | | | |
| 중기 | | | |
| 장기 | | | |

{{ sections_markdown.scaleup_3_schedule_and_fund_plan_roadmap }}

### 3-3. 추진 일정 및 예산

**월별 추진 일정**

| 업무 구분 | 1월 | 2월 | 3월 | 4월 | 5월 | 6월 | 7월 | 8월 | 9월 | 10월 | 11월 | 12월 |
|----------|-----|-----|-----|-----|-----|-----|-----|-----|-----|------|------|------|
| | | | | | | | | | | | | |
| | | | | | | | | | | | | |

**자금 사용 계획**

| 비목 | 세목 | 산출근거 | 금액 (원) |
|------|------|---------|----------|
| | | | |
| | | | |
| **합계** | | | |

{{ sections_markdown.scaleup_5_budget_execution_plan }}

---

## 4. 팀 구성 (Team)

### 4-1. 기업 역량

**대표자 역량**

| 항목 | 내용 |
|------|------|
| 이름 | |
| 주요 경력 | |
| 관련 학력/자격 | |

{{ sections_markdown.team_1_org_and_capabilities }}

**팀원 역량**

| 이름 | 직위 | 주요 경력 | 담당 역할 |
|------|------|----------|----------|
| | | | |
| | | | |

{{ sections_markdown.team_2_current_hires_and_hiring_plan }}

### 4-2. 협력 네트워크

| 구분 | 기관명 | 협력 내용 | 기여도 |
|------|--------|----------|--------|
| 멘토 | | | |
| 협력기관 | | | |
| 투자사 | | | |

{{ sections_markdown.team_3_external_partners }}

---

## 가점 및 면제 기준

### 가점 (추가 점수)

| 구분 | 조건 | 가점 | 해당 여부 |
|------|------|------|---------|
| 투자 유치 | 1억원 이상 투자 유치 | | ☐ |
| 수상 이력 | 정부 주관 창업경진대회 입상 | | ☐ |

### 서류 평가 면제

> ※ 다음 해당자는 서류 평가를 면제받을 수 있습니다.
> - K-Startup 그랜드챌린지 최종 선발자
> - 기타 요건 충족자 (공고문 확인)

- ☐ 서류평가 면제 대상 해당 없음
- ☐ 서류평가 면제 대상 해당 (근거: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; )

---

## 첨부 서류

- ☐ 개인정보 수집·이용 동의서
- ☐ 사업자 확인서
- ☐ 기타 증빙 서류
$tpl$ ,
  TRUE,
  TRUE
),
(
  '33333333-3333-4333-8333-333333333333',
  'scaleup_package',
  'business_plan',
  '성장',
  'v1_0',
  '창업도약패키지_사업화지원_사업계획서.pdf',
  '["general_status","product_service_summary","problem_1_tasks_to_solve","problem_2_competitor_gap_tasks","problem_3_customer_needs_tasks","solution_1_dev_improve_plan_and_schedule","solution_2_customer_requirements_response","solution_3_competitiveness_strengthening","scaleup_1_fund_need_and_financing","scaleup_2_market_entry_and_results_domestic","scaleup_3_market_entry_and_results_global","scaleup_4_exit_strategy_investment_ma_ipo_gov","team_1_founder_and_staff_capabilities_and_hiring","team_2_partners_and_collaboration","team_3_rnd_capability_and_security","team_4_social_value_and_performance_sharing"]'::jsonb,
  $tpl$
# 창업도약패키지 사업화지원 사업계획서

> **작성 유의사항**
> - 본 서식은 창업도약패키지 사업화지원 신청을 위한 공식 양식입니다.
> - 파란색 안내 문구는 삭제 후 내용을 작성하세요.
> - 예산 항목은 별첨 예산 계산 기준(부록 1, 2)을 참고하세요.

---

## 일반 현황

**기술 분야 (해당 항목 체크)**

| 분야 | | 분야 | | 분야 | |
|------|---|------|---|------|---|
| ☐ 제조 | | ☐ ICT·SW | | ☐ 바이오·헬스 | |
| ☐ 에너지·환경 | | ☐ 농식품 | | ☐ 문화·콘텐츠 | |
| ☐ 기계·소재 | | ☐ 소비재 | | ☐ 기타 | |

**대표자 정보**

| 항목 | 내용 | 항목 | 내용 |
|------|------|------|------|
| 기업명 | {{ company_name }} | 대표자 | |
| 사업자번호 | | 설립일 | |
| 주소 | | 연락처 | |

**주요 성과 현황**

| 구분 | 고용 (명) | 매출 (백만원) | 수출 (천달러) | 투자 (백만원) |
|------|----------|-------------|-------------|-------------|
| 현재 | | | | |
| 목표 | | | | |

**예산 계획**

| 구분 | 정부지원금 | 자부담 | 합계 |
|------|-----------|--------|------|
| 금액 (원) | | | |
| 비율 (%) | | | |

**팀 구성 현황**

| 성명 | 직위 | 역할 | 주요 역량 |
|------|------|------|----------|
| | | | |
| | | | |
| | | | |

{{ sections_markdown.general_status }}

---

## 제품·서비스 개요

| 항목 | 내용 |
|------|------|
| 제품/서비스명 | |
| 제품/서비스 소개 | |
| 차별화 포인트 | |
| 개발 진행 단계 | |
| 목표 시장 | |

> 📷 *제품/서비스 이미지 삽입*

{{ sections_markdown.product_service_summary }}

---

## 1. 문제 인식 (Problem)

### 1-1. 해결할 과제 및 Pain Point

> (시장 문제점, 기존 솔루션의 한계, 고객이 겪는 불편함을 서술하세요.)

{{ sections_markdown.problem_1_tasks_to_solve }}

### 1-2. 경쟁사 대비 개선점

> (기존 제품/서비스와 비교하여 본 아이템이 갖는 개선점을 서술하세요.)

| 구분 | 기존 제품/서비스 | 본 아이템 |
|------|----------------|----------|
| 핵심 차이점 | | |
| 기술적 우위 | | |
| 고객 혜택 | | |

{{ sections_markdown.problem_2_competitor_gap_tasks }}

### 1-3. 고객 니즈 충족 방안

> (목표 고객의 핵심 니즈와 충족 전략을 서술하세요.)

{{ sections_markdown.problem_3_customer_needs_tasks }}

---

## 2. 실현 가능성 (Solution)

### 2-1. 개발 계획 및 일정

**추진 일정**

| 구분 | 세부 내용 | 기간 | 산출물 |
|------|----------|------|--------|
| 1단계 | | | |
| 2단계 | | | |
| 3단계 | | | |

> (개발 계획의 구체적인 내용을 서술하세요.)

{{ sections_markdown.solution_1_dev_improve_plan_and_schedule }}

### 2-2. 고객 요구사항 대응

> (고객 피드백 수집 방법 및 제품 개선 계획을 서술하세요.)

{{ sections_markdown.solution_2_customer_requirements_response }}

### 2-3. 시장 경쟁력 강화 방안

> (기술, 특허, 파트너십 등을 통한 경쟁력 강화 전략을 서술하세요.)

{{ sections_markdown.solution_3_competitiveness_strengthening }}

---

## 3. 성장 전략 (Scale-up)

### 3-1. 자금 조달 및 집행 계획

**예산 집행 계획**

| 비목 | 세목 | 산출근거 | 금액 (원) |
|------|------|---------|----------|
| 재료비 | | | |
| 외주용역비 | | | |
| 인건비 | | | |
| 기타 | | | |
| **합계** | | | |

*(부록 1, 2의 예산 계산 기준 참고)*

{{ sections_markdown.scaleup_1_fund_need_and_financing }}

### 3-2. 시장 진입 및 성과 창출

**국내 시장 전략**

> (국내 타겟 시장 및 진입 전략을 서술하세요.)

{{ sections_markdown.scaleup_2_market_entry_and_results_domestic }}

**글로벌 시장 전략**

> (해외 진출 목표 국가 및 전략을 서술하세요.)

{{ sections_markdown.scaleup_3_market_entry_and_results_global }}

**성과 목표**

| 구분 | 1년차 | 2년차 | 3년차 |
|------|------|------|------|
| 매출액 (백만원) | | | |
| 수출액 (천달러) | | | |
| 고용 (명) | | | |
| 투자 유치 (백만원) | | | |

### 3-3. EXIT 전략

> ※ 해당 항목에 체크하세요.
- ☐ **M&A** — 전략적 인수합병을 통한 Exit 계획
- ☐ **IPO** — 기업공개(상장)를 통한 Exit 계획
- ☐ **기타** — (구체적 방안 서술)

> (구체적인 EXIT 전략을 서술하세요.)

{{ sections_markdown.scaleup_4_exit_strategy_investment_ma_ipo_gov }}

---

## 4. 팀 구성 (Team)

### 4-1. 대표자 및 핵심 인력 전문성

**대표자 역량**

| 항목 | 내용 |
|------|------|
| 이름 | |
| 학력 | |
| 주요 경력 (최근 순) | |
| 관련 성과 | |

{{ sections_markdown.team_1_founder_and_staff_capabilities_and_hiring }}

**핵심 인력 역량**

| 이름 | 직위 | 전문 분야 | 주요 경력 |
|------|------|----------|----------|
| | | | |
| | | | |

{{ sections_markdown.team_2_partners_and_collaboration }}

### 4-2. 기술 개발 및 보호 역량

> (기술 개발 역량, 특허 보유 현황 및 기술 보호 전략을 서술하세요.)

| 항목 | 내용 |
|------|------|
| 보유 특허 | |
| 출원 중 특허 | |
| 기술 보호 전략 | |

{{ sections_markdown.team_3_rnd_capability_and_security }}

### 4-3. 사회적 가치 실천 계획

> (기업의 사회적 책임(CSR) 및 ESG 실천 계획을 서술하세요.)

{{ sections_markdown.team_4_social_value_and_performance_sharing }}

---

## 가점 및 패스트트랙 체크리스트

**가점 해당 여부**

| 항목 | 조건 | 해당 여부 |
|------|------|---------|
| 투자 유치 | 1억원 이상 | ☐ 해당 / ☐ 미해당 |
| 정부 포상 | 장관급 이상 수상 | ☐ 해당 / ☐ 미해당 |
| 기타 | (구체적 기재) | ☐ 해당 / ☐ 미해당 |

**패스트트랙 자격 해당 여부**

- ☐ 해당 없음
- ☐ 해당 (근거 서류 첨부: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; )

---

## 부록

### 부록 1. 예산 계산 기준 (인건비)

> (인건비 산출 기준 및 방식을 기재하세요.)

&nbsp;

### 부록 2. 예산 계산 기준 (경비)

> (기타 경비 산출 기준 및 방식을 기재하세요.)

&nbsp;
$tpl$,
  TRUE,
  TRUE
),
(
  '44444444-4444-4444-8444-444444444444',
  'social_pre_cert_plan',
  'business_plan',
  '공통',
  'v1_0',
  '예비사회적기업 신청 사업계획서.pdf',
  '["company_overview","cert_eligibility_1_social_purpose_type","cert_eligibility_2_org_form_and_governance","cert_eligibility_3_employment_plan","cert_eligibility_4_articles_and_rules","plan_1_business_purpose","plan_2_business_content_and_revenue","plan_3_business_capability","plan_4_business_goals","post_designation_1_year_plan","post_designation_2_year_plan","post_designation_3_year_plan","other_execution_plan"]'::jsonb,
  $tpl$
# 예비사회적기업 사업계획서

---

## 기업 개요

| 항목 | 내용 |
|------|------|
| 기업명 | {{ company_name }} |
| 대표자 | |
| 소재지 | |
| 설립일 | |
| 주요사업 | |
| 연락처 | |

{{ sections_markdown.company_overview }}

---

## 인증요건 충족 계획

### 사회적 목적 유형 선택

> ※ 해당하는 유형에 체크(☑)하세요.

- ☐ **사회서비스 제공형** — 취약계층에게 사회서비스 또는 일자리를 제공
- ☐ **일자리 제공형** — 취약계층에게 일자리를 제공하는 것을 주된 목적으로 하는 조직
- ☐ **지역사회 공헌형** — 지역사회에 공헌하는 것을 주된 목적으로 하는 조직
- ☐ **혼합형** — 취약계층 일자리 제공과 사회서비스 제공이 혼합된 유형
- ☐ **기타형** — 사회적 목적의 실현 여부를 위의 항목에 포함되지 않는 경우

{{ sections_markdown.cert_eligibility_1_social_purpose_type }}

### 조직 형태

| 항목 | 내용 |
|------|------|
| 현재 조직 형태 | |
| 법적 실체 여부 | ☐ 있음 &nbsp; ☐ 없음 |
| 정관/규약 보유 여부 | ☐ 있음 &nbsp; ☐ 없음 |

{{ sections_markdown.cert_eligibility_2_org_form_and_governance }}

### 고용 계획

| 구분 | 현재 | 6개월 후 | 1년 후 |
|------|------|---------|--------|
| 전체 고용인원 (명) | | | |
| 취약계층 고용인원 (명) | | | |
| 취약계층 비율 (%) | | | |

{{ sections_markdown.cert_eligibility_3_employment_plan }}

### 의사결정 구조

> (민주적 의사결정 구조 및 이해관계자 참여 방식을 서술하세요.)

{{ sections_markdown.cert_eligibility_2_org_form_and_governance }}

### 정관 변경 계획

> (인증 요건을 충족하기 위한 정관 변경 계획이 있는 경우 서술하세요.)

{{ sections_markdown.cert_eligibility_4_articles_and_rules }}

---

## 사회적 목적 실현을 위한 사업계획

### 1. 사업 목적

> (사회적 미션 및 해결하고자 하는 구체적 사회문제를 서술하세요.)

{{ sections_markdown.plan_1_business_purpose }}

### 2. 사업 내용

| 항목 | 내용 |
|------|------|
| 주요 제품/서비스 | |
| 수익 창출 모델 | |
| 주요 고객(수혜자) | |

> (제품/서비스 내용 및 수익 모델을 상세히 서술하세요.)

{{ sections_markdown.plan_2_business_content_and_revenue }}

### 3. 사업 역량

| 항목 | 내용 |
|------|------|
| 대표자 배경 및 관심계기 | |
| 인력 전문성 | |
| 자원 확보 방안 | |

{{ sections_markdown.plan_3_business_capability }}

### 4. 사업 목표

| 구분 | 1년차 | 2년차 | 3년차 |
|------|------|------|------|
| 매출액 (만원) | | | |
| 고용인원 (명) | | | |
| 사회적 목적 지표 | | | |

{{ sections_markdown.plan_4_business_goals }}

---

## 지정 이후 단계별 세부 추진 계획

| 단계 | 기간 | 추진 내용 | 비고 |
|------|------|----------|------|
| 1단계 | | | |
| 2단계 | | | |
| 3단계 | | | |

> (시설, 마케팅, 투자 등 구체적인 로드맵을 서술하세요.)

{{ sections_markdown.post_designation_1_year_plan }}

{{ sections_markdown.post_designation_2_year_plan }}

{{ sections_markdown.post_designation_3_year_plan }}

{{ sections_markdown.other_execution_plan }}
$tpl$,
  TRUE,
  TRUE
),
(
  '55555555-5555-4555-8555-555555555555',
  'bm_diagnosis_form',
  'bm_diagnosis',
  '공통',
  'v1_0',
  'BM진단 및 설계양식.pdf',
  '["company_profile_core","business_and_financials","cert_ip_rnd_invest_esg","support_items_checklist","notes_and_consultant"]'::jsonb,
  $tpl$
# BM 진단서

---

## 기업 기본 현황

| 항목 | 내용 | 항목 | 내용 | 항목 | 내용 |
|------|------|------|------|------|------|
| 기업명 | {{ company_name }} | 대표자명 | | 사업자등록번호 | |
| 설립년도 | | 소재지 | | 연락처 | |
| 홈페이지 | | 법인형태 | | 기업유형 | {{ company_type }} |
| 주요업종 | | 주력사업내용 | | 종사자수 | |
| 고용형태 | | 최근매출 | | 주요수익원 | |
| 정부사업 참여이력 | | 기업인증보유현황 | | 지재권보유현황 | |
| 연구개발전담부서 | | 투자현황 | | R&D 현황 | |

---

## 지원 항목

> 각 항목별 우선순위/등급(★)을 체크하세요.

| 번호 | 항목 | 등급 | 해당여부 |
|------|------|------|---------|
| 1 | 중소기업 | ★ | ☐ |
| 2 | 여성기업 | ★ | ☐ |
| 3 | 장애인기업 | ★ | ☐ |
| 4 | 협동조합 | ★ | ☐ |
| 5 | 예비사회적기업 | ★★ | ☐ |
| 6 | 소셜벤처 | ★★ | ☐ |
| 7 | 창업기업 | ★★ | ☐ |
| 8 | 성과공유기업 | ★★ | ☐ |
| 9 | 벤처기업 | ★★★ | ☐ |
| 10 | 이노비즈(기술) | ★★ | ☐ |
| 11 | 메인비즈(경영) | ★★ | ☐ |
| 12 | 녹색기업 | ★★★ | ☐ |
| 13 | 사회적기업 | ★★★★ | ☐ |
| 14 | R&D | ★★★★ | ☐ |
| 15 | ESG관련인증 | ★★★ | ☐ |
| 16 | 우수사회적기업 | ★★★★ | ☐ |
| 17 | 혁신기업 | ★★★★ | ☐ |
| 18 | 공공우수제품지정 | ★★★★★ | ☐ |
| 19 | 강소기업 | ★★★★★ | ☐ |
| 20 | 글로벌강소기업 | ★★★★★ | ☐ |

---

## 의견

> (BM 진단 결과 및 종합 의견을 작성하세요.)

&nbsp;

&nbsp;

&nbsp;

&nbsp;

&nbsp;

&nbsp;

&nbsp;

---

- 작성일: 2024년 &nbsp;&nbsp;&nbsp; 월 &nbsp;&nbsp;&nbsp; 일
- 작성자: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; (인 또는 서명)

---

## BM 분석 (별첨)

> (비즈니스 모델 분석 내용을 작성하세요.)

&nbsp;

&nbsp;

&nbsp;

&nbsp;
$tpl$,
  TRUE,
  TRUE
)
ON CONFLICT (artifact_type, stage, version)
DO UPDATE SET
  name = EXCLUDED.name,
  source_pdf = EXCLUDED.source_pdf,
  sections_keys_ordered = EXCLUDED.sections_keys_ordered,
  template_body = EXCLUDED.template_body,
  is_active = EXCLUDED.is_active,
  is_default = EXCLUDED.is_default;
