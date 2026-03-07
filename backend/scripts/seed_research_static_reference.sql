\set ON_ERROR_STOP on

-- 안전한 정적 레퍼런스 시드 (v1_0 운영용)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema = current_schema()
      AND table_name = 'research_static_reference'
  ) THEN
    RAISE EXCEPTION 'Required table missing: research_static_reference';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'research_static_reference'
      AND column_name = 'payload_json'
  ) THEN
    RAISE EXCEPTION 'Required column missing: research_static_reference.payload_json';
  END IF;

  CREATE EXTENSION IF NOT EXISTS pgcrypto;
  ALTER TABLE research_static_reference
    ALTER COLUMN id SET DEFAULT gen_random_uuid();
END $$;

INSERT INTO research_static_reference (domain, industry_code, tag, title, source_url, source_text, payload_json, is_active)
VALUES
  ('market_size', 'IT', 'market_baseline', 'IT 산업 시장규모 지표(산업통계 기준 예시)', 'https://www.kostat.go.kr/market-it', '직접 수집/적재한 IT 산업 성장 및 시장규모 레퍼런스(요약값)', '{"metric":"매출규모","unit":"억원","period":"2024","value":0}', TRUE),
  ('market_size', NULL, 'market_overview', '산업별 시장규모(공통)', 'https://www.k-startup.go.kr', '신규 시장 진입성, 잠재시장, 타깃산업의 매출총규모 추정', '{"metric":"잠재시장","unit":"억원","period":"2024","value":0}', TRUE),

  ('industry_trends', 'IT', 'trend_domestic', 'IT 산업 동향(국내 트렌드)', 'https://www.msit.go.kr', '국내 정책/기술 동향, 규제 및 기술성숙도 동향(요약본)', '{"trend_type":"domestic","update_cycle":"quarter"}', TRUE),
  ('industry_trends', NULL, 'trend_global', '글로벌 산업 트렌드(핵심)', 'https://www.oecd.org', '해외 시장 트렌드 및 인프라/규제 변화 관점', '{"trend_type":"global","update_cycle":"quarter"}', TRUE),

  ('competitor_info', 'IT', 'competitor_list', '경쟁사 동향(대표사례)', 'https://www.competitive.co.kr', '기능, 가격, 진입장벽, 차별화 포인트 3개 가이드 항목', '{"coverage":"domestic","scope":"대표 경쟁사"}', TRUE),
  ('competitor_info', NULL, 'competitor_checklist', '경쟁사 분석 체크리스트', 'https://www.kedglobal.com', '차별화 포인트, 약점, 기회 요소로 매핑 가능한 체크 항목', '{"coverage":"cross","scope":"산업 공통"}', TRUE),

  ('policy_support', 'IT', 'policy_support', '정책지원사업 요건(IT 중심)', 'https://www.k-startup.go.kr', '정책지원사업 공통 요건·심사 기준 예시', '{"eligibility":"기본기준","support_type":"정책자금"}', TRUE),
  ('policy_support', NULL, 'policy_check', '인증/지원 요건 공통 체크리스트', 'https://www.mss.go.kr', '지원사업 신청 전 필수 점검 항목(예시)', '{"eligibility":"공통","support_type":"매핑형"}', TRUE)
ON CONFLICT (domain, industry_code, tag, title)
DO UPDATE SET
  source_url = EXCLUDED.source_url,
  source_text = EXCLUDED.source_text,
  payload_json = EXCLUDED.payload_json,
  is_active = EXCLUDED.is_active;
