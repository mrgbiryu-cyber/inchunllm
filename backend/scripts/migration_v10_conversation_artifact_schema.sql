\set ON_ERROR_STOP on

-- 1) 운영형 v1_0 catalog 기반 스키마 정합화 마이그레이션
--    - 안전하게 재실행 가능(동일 스크립트 반복 실행 시 멱등성 유지)
--    - 필수 테이블 부재시 FAIL, 그 외는 보정/확장

-- 1-1) artifact_type_enum이 존재하면 bm_diagnosis 값만 추가
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE t.typname = 'artifact_type_enum'
      AND n.nspname = current_schema()
  ) THEN
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
  END IF;
END $$;

-- 1-2) 필수 운영 메타/카탈로그 테이블 보장
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

-- 1-3) 기존 v1 테이블/컬럼 존재 보장
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = 'conversation_state') THEN
    RAISE EXCEPTION 'Required table missing: conversation_state';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = 'growth_templates') THEN
    RAISE EXCEPTION 'Required table missing: growth_templates';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = 'artifact_approval_state') THEN
    RAISE EXCEPTION 'Required table missing: artifact_approval_state';
  END IF;

  -- conversation_state 기본값/컬럼 보정
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'conversation_state'
      AND column_name = 'policy_version'
  ) THEN
    ALTER TABLE conversation_state ADD COLUMN policy_version VARCHAR(24) NOT NULL DEFAULT 'v0_legacy';
  ELSE
    UPDATE conversation_state
      SET policy_version = 'v0_legacy'
      WHERE policy_version IS NULL;
    ALTER TABLE conversation_state ALTER COLUMN policy_version SET DEFAULT 'v0_legacy';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'conversation_state'
      AND column_name = 'question_required_count'
  ) THEN
    ALTER TABLE conversation_state ADD COLUMN question_required_count INTEGER NOT NULL DEFAULT 0;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'conversation_state'
      AND column_name = 'question_optional_count'
  ) THEN
    ALTER TABLE conversation_state ADD COLUMN question_optional_count INTEGER NOT NULL DEFAULT 0;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'conversation_state'
      AND column_name = 'question_special_count'
  ) THEN
    ALTER TABLE conversation_state ADD COLUMN question_special_count INTEGER NOT NULL DEFAULT 0;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'conversation_state'
      AND column_name = 'question_total_count'
  ) THEN
    ALTER TABLE conversation_state ADD COLUMN question_total_count INTEGER NOT NULL DEFAULT 0;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'conversation_state'
      AND column_name = 'question_required_limit'
  ) THEN
    ALTER TABLE conversation_state ADD COLUMN question_required_limit INTEGER NOT NULL DEFAULT 0;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'conversation_state'
      AND column_name = 'question_optional_limit'
  ) THEN
    ALTER TABLE conversation_state ADD COLUMN question_optional_limit INTEGER NOT NULL DEFAULT 0;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'conversation_state'
      AND column_name = 'question_special_limit'
  ) THEN
    ALTER TABLE conversation_state ADD COLUMN question_special_limit INTEGER NOT NULL DEFAULT 0;
  END IF;

  -- 추가: 런타임 버전/검증/모드 추적 컬럼 보정
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'conversation_state'
      AND column_name = 'active_mode'
  ) THEN
    ALTER TABLE conversation_state ADD COLUMN active_mode VARCHAR(24) NOT NULL DEFAULT 'NATURAL';
  ELSE
    ALTER TABLE conversation_state ALTER COLUMN active_mode SET DEFAULT 'NATURAL';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'conversation_state'
      AND column_name = 'plan_data_version'
  ) THEN
    ALTER TABLE conversation_state ADD COLUMN plan_data_version INTEGER NOT NULL DEFAULT 0;
  ELSE
    UPDATE conversation_state SET plan_data_version = 0 WHERE plan_data_version IS NULL;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'conversation_state'
      AND column_name = 'summary_revision'
  ) THEN
    ALTER TABLE conversation_state ADD COLUMN summary_revision INTEGER NOT NULL DEFAULT 0;
  ELSE
    UPDATE conversation_state SET summary_revision = 0 WHERE summary_revision IS NULL;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'artifact_approval_state'
      AND column_name = 'summary_revision'
  ) THEN
    ALTER TABLE artifact_approval_state ADD COLUMN summary_revision INTEGER NOT NULL DEFAULT 0;
  END IF;

  -- growth_templates 스키마 보정
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'growth_templates'
      AND column_name = 'source_pdf'
  ) THEN
    ALTER TABLE growth_templates ADD COLUMN source_pdf TEXT;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'growth_templates'
      AND column_name = 'sections_keys_ordered'
  ) THEN
    ALTER TABLE growth_templates ADD COLUMN sections_keys_ordered JSONB;
  END IF;

  -- artifact_approval_state 인덱스/제약 보정
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'uq_artifact_approval_project_artifact'
      AND conrelid = 'artifact_approval_state'::regclass
  ) THEN
    ALTER TABLE artifact_approval_state
      ADD CONSTRAINT uq_artifact_approval_project_artifact UNIQUE (project_id, artifact_type);
  END IF;

  -- threads soft-delete 컬럼 보정 (동일 스크립트 안전 재실행)
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'threads'
      AND column_name = 'is_deleted'
  ) THEN
    ALTER TABLE threads ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT FALSE;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'threads'
      AND column_name = 'deleted_at'
  ) THEN
    ALTER TABLE threads ADD COLUMN deleted_at TIMESTAMP;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_conversation_state_policy_version
  ON conversation_state (policy_version);

CREATE INDEX IF NOT EXISTS idx_artifact_approval_project_artifact
  ON artifact_approval_state (project_id, artifact_type);

CREATE INDEX IF NOT EXISTS idx_growth_templates_artifact_stage
  ON growth_templates (artifact_type, stage);

-- 1-4) 운영 카탈로그 seed
INSERT INTO artifact_type_catalog (artifact_type, description)
VALUES
  ('business_plan', '사업계획서(PDF/HTML) 템플릿 렌더링 대상'),
  ('matching', '매칭 항목 산출물'),
  ('roadmap', '성장 로드맵 산출물'),
  ('bm_diagnosis', 'BM 진단 및 설계 양식(체크리스트/진단표) 산출물')
ON CONFLICT (artifact_type) DO UPDATE
SET description = EXCLUDED.description,
    created_at = NOW();

INSERT INTO approval_step_catalog (artifact_type, steps)
VALUES
  ('business_plan', '["key_figures_approved","certification_path_approved","template_selected","summary_confirmed"]'::jsonb),
  ('roadmap', '["route_approved"]'::jsonb),
  ('matching', '["source_verified"]'::jsonb),
  ('bm_diagnosis', '["bm_checklist_completed","consultant_reviewed"]'::jsonb)
ON CONFLICT (artifact_type) DO UPDATE
SET steps = EXCLUDED.steps,
    created_at = NOW();
