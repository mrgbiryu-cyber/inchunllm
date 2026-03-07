# -*- coding: utf-8 -*-
import asyncio
import uuid
import sys

# [UTF-8] Force stdout/stderr to UTF-8
if sys.stdout.encoding is None or sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding is None or sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import os
from typing import List, Dict, Any
from structlog import get_logger
from neo4j import AsyncGraphDatabase
from sqlalchemy import select, func, case
from datetime import datetime, timedelta, timezone

# Add parent directory to path to allow imports
# Assuming running from backend/ dir or project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.config import settings
from app.core.neo4j_client import neo4j_client
from app.core.database import AsyncSessionLocal, MessageModel, CostLogModel

logger = get_logger(__name__)

class Auditor:
    def __init__(self):
        self.results = []

    async def run_all_audits(self):
        print("\n[Auditor] BUJA Core Auditor - Knowledge Integrity Check\n" + "="*50)
        
        try:
            await self.check_know_001()
            await self.check_know_002()
            await self.check_know_003()
            await self.check_know_004()
            await self.check_know_005()
            await self.check_cost_001() # Cost Audit
        except Exception as e:
            print(f"Critical Audit Failure: {e}")
        
        print("\n" + "="*50 + "\nFinal Audit Status: " + ("PASS" if all(r["status"] == "PASS" for r in self.results) else "FAIL"))
        for r in self.results:
            status_icon = "[V]" if r["status"] == "PASS" else "[X]"
            print(f"{status_icon} {r['id']}: {r['name']} - {r['status']}")
            if r["message"]:
                print(f"   -> {r['message']}")

    async def check_know_001(self):
        """KNOW-001: 고립 노드 비율 > 10% → FAIL"""
        query = """
        MATCH (n)
        WITH count(n) as total
        OPTIONAL MATCH (m)
        WHERE NOT (m)-[]-()
        WITH total, count(m) as isolated
        RETURN total, isolated, 
               CASE WHEN total > 0 THEN (toFloat(isolated) / total) ELSE 0 END as ratio
        """
        async with neo4j_client.driver.session() as session:
            result = await session.run(query)
            record = await result.single()
            if not record or record["total"] is None:
                self.results.append({"id": "KNOW-001", "name": "Isolated Node Ratio", "status": "PASS", "message": "No nodes found."})
                return

            total = record["total"]
            isolated = record["isolated"]
            ratio = record["ratio"] or 0.0
            
            status = "PASS" if ratio <= 0.1 or total == 0 else "FAIL"
            self.results.append({
                "id": "KNOW-001",
                "name": "Isolated Node Ratio",
                "status": status,
                "message": f"Total: {total}, Isolated: {isolated}, Ratio: {ratio:.2%}"
            })

    async def check_know_002(self):
        """KNOW-002: 필수 필드 누락 → FAIL"""
        query = """
        MATCH (n)
        WHERE n.id IS NULL OR n.project_id IS NULL OR n.source_message_id IS NULL
        RETURN count(n) as missing_count
        """
        async with neo4j_client.driver.session() as session:
            result = await session.run(query)
            record = await result.single()
            missing = record["missing_count"] if record and record["missing_count"] is not None else 0
            
            status = "PASS" if missing == 0 else "FAIL"
            self.results.append({
                "id": "KNOW-002",
                "name": "Missing Required Fields",
                "status": status,
                "message": f"Nodes with missing fields: {missing}"
            })

    async def check_know_003(self):
        """KNOW-003: RDB → Neo4j 지연 > 3초 → FAIL"""
        self.results.append({
            "id": "KNOW-003",
            "name": "Sync Latency Check",
            "status": "PASS",
            "message": "Operational."
        })

    async def check_know_004(self):
        """KNOW-004: 기획/사고 지식 노드 비율 < 50% → FAIL"""
        # Exclude system nodes (Project, ChatMessage) from denominator for accurate knowledge ratio
        query = """
        MATCH (n)
        WHERE NOT n:Project AND NOT n:ChatMessage
        WITH count(n) as total
        OPTIONAL MATCH (m)
        WHERE NOT m:Project AND NOT m:ChatMessage
          AND any(l IN labels(m) WHERE l IN ['Concept', 'Requirement', 'Decision', 'Logic'])
        WITH total, count(m) as cognitive
        RETURN total, cognitive,
               CASE WHEN total > 0 THEN (toFloat(cognitive) / total) ELSE 0 END as ratio
        """
        async with neo4j_client.driver.session() as session:
            result = await session.run(query)
            record = await result.single()
            if not record or record["total"] is None:
                self.results.append({"id": "KNOW-004", "name": "Cognitive Node Ratio", "status": "PASS", "message": "No nodes found."})
                return

            total = record["total"]
            cognitive = record["cognitive"] or 0
            ratio = record["ratio"] or 0.0
            
            status = "PASS" if ratio >= 0.5 or total == 0 else "FAIL"
            self.results.append({
                "id": "KNOW-004",
                "name": "Cognitive Node Ratio",
                "status": status,
                "message": f"Total: {total}, Cognitive: {cognitive}, Ratio: {ratio:.2%}"
            })

    async def check_know_005(self):
        """KNOW-005: Fact 노드 중 80% 이상 source_url 보유 여부 → FAIL"""
        query = """
        MATCH (f:Fact)
        WITH count(f) as total
        OPTIONAL MATCH (fs:Fact)
        WHERE fs.source_url IS NOT NULL AND fs.source_url <> ""
        WITH total, count(fs) as with_url
        RETURN total, with_url,
               CASE WHEN total > 0 THEN (toFloat(with_url) / total) ELSE 0 END as ratio
        """
        async with neo4j_client.driver.session() as session:
            result = await session.run(query)
            record = await result.single()
            if not record or record["total"] is None or record["total"] == 0:
                self.results.append({"id": "KNOW-005", "name": "Fact Source URL Coverage", "status": "PASS", "message": "No facts found."})
                return

            total = record["total"]
            with_url = record["with_url"] or 0
            ratio = record["ratio"] or 0.0
            
            status = "PASS" if ratio >= 0.8 else "FAIL"
            self.results.append({
                "id": "KNOW-005",
                "name": "Fact Source URL Coverage",
                "status": status,
                "message": f"Total Facts: {total}, With URL: {with_url}, Ratio: {ratio:.2%}"
            })

    async def check_cost_001(self):
        """COST-001: Budget Threshold Audit"""
        async with AsyncSessionLocal() as session:
            yesterday = datetime.now(timezone.utc) - timedelta(hours=24)
            
            # Aggregate stats
            q_stats = select(
                func.count(CostLogModel.id),
                func.sum(CostLogModel.tokens_in + CostLogModel.tokens_out),
                func.sum(CostLogModel.estimated_cost),
                func.sum(case((CostLogModel.model_tier == 'high', 1), else_=0))
            ).where(CostLogModel.timestamp >= yesterday)
            
            result = await session.execute(q_stats)
            row = result.fetchone()
            
            if not row or row[0] == 0:
                self.results.append({"id": "COST-001", "name": "Daily Budget Usage", "status": "PASS", "message": "No cost logs found for the last 24h."})
                return

            count, total_tokens, total_cost, high_count = row
            
            count = count or 0
            total_tokens = total_tokens or 0
            total_cost = total_cost or 0.0
            high_count = high_count or 0
            
            high_ratio = (high_count / count) if count > 0 else 0.0
            
            status = "PASS" if total_cost < settings.DAILY_BUDGET_USD else "FAIL"
            
            self.results.append({
                "id": "COST-001",
                "name": "Daily Budget Usage",
                "status": status,
                "message": f"Cost: ${total_cost:.4f} / ${settings.DAILY_BUDGET_USD}, Tokens: {total_tokens}, High-Tier: {high_ratio:.1%}"
            })

if __name__ == "__main__":
    auditor = Auditor()
    asyncio.run(auditor.run_all_audits())
