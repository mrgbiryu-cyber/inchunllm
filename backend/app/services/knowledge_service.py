# -*- coding: utf-8 -*-
import asyncio
import json
import sys

# [UTF-8] Force stdout/stderr to UTF-8
if sys.stdout.encoding is None or sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding is None or sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import uuid
import re
import hashlib
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta, timezone
from structlog import get_logger

from app.core.neo4j_client import neo4j_client
from app.core.database import AsyncSessionLocal, MessageModel, CostLogModel
from app.core.config import settings
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from sqlalchemy import select, func, and_

logger = get_logger(__name__)

# Asynchronous Task Queue for Knowledge Extraction
knowledge_queue = asyncio.Queue()

class KnowledgeService:
    def __init__(self):
        self._is_degraded = False
        self._last_degraded_check = datetime.min

    async def check_budget_and_mode(self) -> bool:
        """
        [COST-001] Check budget usage for last 24h and toggle DEGRADED mode.
        """
        now = datetime.now(timezone.utc)
        if now - self._last_degraded_check < timedelta(minutes=5):
            return self._is_degraded

        async with AsyncSessionLocal() as session:
            yesterday = now - timedelta(hours=24)
            query = select(func.sum(CostLogModel.estimated_cost)).where(CostLogModel.timestamp >= yesterday)
            result = await session.execute(query)
            total_cost = result.scalar() or 0.0
            
            if total_cost >= settings.DAILY_BUDGET_USD:
                if not self._is_degraded:
                    logger.warning("🚨 BUDGET EXCEEDED: Switching to DEGRADED mode", total_cost=total_cost)
                self._is_degraded = True
            else:
                self._is_degraded = False
            
            self._last_degraded_check = now
            return self._is_degraded

    def _get_llm(self, tier: str = "low"):
        """
        Returns appropriate LLM based on tier and system state.
        """
        if self._is_degraded:
            tier = "low" # Force low tier in degraded mode
            
        model = settings.LLM_HIGH_TIER_MODEL if tier == "high" else settings.LLM_LOW_TIER_MODEL
        return ChatOpenAI(
            model=model, 
            api_key=settings.OPENROUTER_API_KEY, 
            base_url=settings.OPENROUTER_BASE_URL,
            temperature=0
        )

    async def process_message_pipeline(self, message_id: uuid.UUID):
        """
        Unified pipeline entry with filtering and idempotency.
        """
        # 1. Idempotency Check
        async with AsyncSessionLocal() as session:
            existing = await session.execute(select(CostLogModel).where(CostLogModel.message_id == message_id))
            if existing.scalar_one_or_none():
                logger.info("Message already processed, skipping", message_id=str(message_id))
                return

            # Get message details
            result = await session.execute(select(MessageModel).filter(MessageModel.message_id == message_id))
            msg = result.scalar_one_or_none()
            if not msg:
                logger.error("Message not found for pipeline", message_id=str(message_id))
                return

        # 2. Smart Filtering (Heuristic Gate)
        # Task 2.2: Pass metadata for role-based filtering
        metadata = {"sender_role": msg.sender_role}
        importance, tier = self._evaluate_importance(msg.content, metadata)
        
        if importance == "NONE":
            logger.info("Chatter detected, skipping knowledge extraction", message_id=str(message_id))
            await self._log_cost(msg, "realtime", "none", 0, 0, 0.0, "skip")
            return

        # 3. Budget Check
        await self.check_budget_and_mode()

        # 4. Extraction & Storage
        try:
            # Fetch Context Snapshot for De-duplication
            context_snapshot = await self._get_context_snapshot(msg.project_id)

            logger.info(f"Extracting knowledge ({tier} tier)", message_id=str(message_id))
            extracted, usage = await self._llm_extract(msg, tier, context_snapshot)
            
            if extracted:
                await self._upsert_to_neo4j(msg, extracted)
                await self._log_cost(msg, "realtime", tier, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), self._estimate_cost(tier, usage), "success")
                logger.info("Knowledge stored", message_id=str(message_id))
            else:
                await self._log_cost(msg, "realtime", tier, 0, 0, 0.0, "fail")

        except Exception as e:
            logger.error("Pipeline failed", message_id=str(message_id), error=str(e))

    def _evaluate_importance(self, content: str, metadata: dict = None) -> Tuple[str, str]:
        """
        Task 2.1 & 2.2: Enhanced noise filter + role-based filtering
        Per KG_SANITIZE_IDEMPOTENCY.md
        """
        content_lower = content.lower()
        
        # Task 2.2: Role-based filtering - skip tool and system messages
        if metadata:
            sender_role = metadata.get("sender_role", "")
            if sender_role in ["system", "tool", "tool_call"]:
                return "NONE", "low"
        
        # Task 2.1: Expanded noise keywords (50+ items)
        operational_noise = [
            # Agent operations
            "에이전트", "agent", "생성", "추가", "설정", "변경", "삭제", "제거",
            "create agent", "add agent", "update agent", "delete agent",
            # System operations
            "system_prompt", "tool_allowlist", "repo_root", "allowed_paths",
            "workflow", "orchestration", "job", "worker", "queue", "task",
            # Meta requests
            "어떻게", "how to", "설명", "explain", "알려줘", "tell me",
            "뭐야", "what is", "무엇", "왜", "why",
            # Greetings/chatter
            "안녕", "hi", "hello", "ㅎㅇ", "하이", "헬로우",
            "ㅇㅋ", "ok", "okay", "오케이", "굿", "good", "ㅋㅋ", "ㄱㅅ",
            # Status queries
            "상태", "status", "점검", "check", "확인", "verify",
            "진단", "diagnosis", "리포트", "report",
            # UI/UX operations
            "버튼", "button", "클릭", "click", "화면", "screen", "탭", "tab",
            "새로고침", "refresh", "페이지", "page"
        ]
        
        # Check for operational noise keywords
        if any(noise in content_lower for noise in operational_noise):
            # Task 2.1: Regex pattern matching for agent operations
            agent_patterns = [
                r"에이전트\s*[를을]\s*(생성|추가|만들)",
                r"agent\s*(create|add|new)",
                r"system.?prompt",
                r"설정\s*변경",
                r"config\s*update"
            ]
            if any(re.search(pattern, content_lower) for pattern in agent_patterns):
                return "NONE", "low"
        
        # High-value signals (domain knowledge, decisions, requirements)
        high_signals = [
            "결정", "확정", "하기로", "이걸로", "채택", "변경사항", "추가사항", "폐기", 
            "금지", "반드시", "필수", "절대", "하지마", "규칙", "정책", "원칙",
            "pass", "fail", "점검결과", "감사결과", "auditor", "know-", "cost-",
            "스키마", "마이그레이션", "dual-write", "neo4j", "rdb", "redis", "큐", "비동기",
            "중요사항", "핵심", "우선순위", "critical", "must", "should"
        ]
        
        if any(sig in content_lower for sig in high_signals):
            return "HIGH", "high"

        # Short chatter filter
        if len(content) < settings.COST_FILTER_MIN_CHARS:
            chatter = ["ㅇㅋ", "알았어", "ㅋㅋ", "굿", "오케이", "ㅇㅇ", "하이", "안녕"]
            if any(c in content_lower for c in chatter) or len(content) < 5:
                return "NONE", "low"

        return "MEDIUM", "low"

    async def _get_context_snapshot(self, project_id: uuid.UUID) -> Dict[str, Any]:
        """
        Fetch a small snapshot of existing knowledge for LLM de-duplication.
        """
        if not project_id:
            return {"known_concepts": [], "known_requirements": [], "known_decisions": [], "known_tasks": []}
            
        p_id_str = str(project_id)
        async with neo4j_client.driver.session() as session:
            # Fetch top 10 of each type
            queries = {
                "known_concepts": "MATCH (p:Project {id: $p_id})-[:HAS_KNOWLEDGE]->(n:Concept) RETURN n.id as id, n.title as title LIMIT 10",
                "known_requirements": "MATCH (p:Project {id: $p_id})-[:HAS_KNOWLEDGE]->(n:Requirement) RETURN n.id as id, n.title as title, n.severity as severity, n.status as status LIMIT 10",
                "known_decisions": "MATCH (p:Project {id: $p_id})-[:HAS_KNOWLEDGE]->(n:Decision) RETURN n.id as id, n.title as title, n.status as status LIMIT 10",
                "known_tasks": "MATCH (p:Project {id: $p_id})-[:HAS_KNOWLEDGE]->(n:Task) RETURN n.id as id, n.name as name, n.status as status LIMIT 10"
            }
            
            results = {}
            for key, q in queries.items():
                res = await session.run(q, {"p_id": p_id_str})
                results[key] = [dict(record) async for record in res]
            
            return results

    async def _llm_extract(self, msg: MessageModel, tier: str, context: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        llm = self._get_llm(tier)
        
        system_prompt = """You are a SUPREME knowledge extraction engine. 
Your goal is to build a high-density COGNITIVE graph. 
DO NOT simply summarize. TRANSFORM the message into specific cognitive entities.

=====================STRATEGIC GOAL (KNOW-004)=====================
- MINIMUM 50% of your nodes MUST be: Decision, Requirement, Concept, or Logic.
- Avoid low-value 'History' or 'Fact' nodes unless they contain critical evidence.
- If a message contains a preference, it is a Requirement.
- If a message contains a choice, it is a Decision.
- If a message explains a term or system, it is a Concept.

=====================EXCLUDE (Task 2.3 - DO NOT EXTRACT THESE)=====================
NEVER extract knowledge from messages containing ONLY:
- System operations: "create agent", "add agent", "update configuration", "delete agent"
- Meta requests: "how to", "explain", "what is", "why", "tell me about"
-  Agent management: system_prompt changes, tool_allowlist updates, repo_root settings
- UI operations: button clicks, page refresh, tab navigation
- Status queries: "check status", "진단", "report", "리스트"
- Greetings/chatter: "hello", "hi", "안녕", "ㅇㅋ", "ok"

If the ENTIRE message is about these topics: Return {"nodes": [], "reason": "Operational message - no domain knowledge"}
If the message CONTAINS operational content AND domain knowledge: Extract ONLY the domain knowledge.

=====================OUTPUT CONTRACT=====================
- Output MUST be valid JSON only. No markdown. No commentary.
- Output MUST strictly follow the schema: { "nodes": [...], "relationships": [...], "meta": {...} }
- You MUST create at least ONE node per message.
- Prefer the most specific node type possible.
- If information is ambiguous or unverified, map it to Concept and add tag "unverified".
- Never invent facts. Never hallucinate sources.
- Do NOT include chain-of-thought or reasoning steps.
=====================ALLOWED NODE TYPES=====================
Project, Concept, Requirement, Decision, Task, History, Fact, File, Logic
=====================ALLOWED RELATIONSHIPS=====================
HAS_CONCEPT, HAS_REQUIREMENT, HAS_DECISION, HAS_TASK, RESULTED_IN, BASED_ON, RELATES_TO, IMPLEMENTS_BY, GOVERNS, SUPPORTS, REFUTES
=====================DE-DUPLICATION RULE (MANDATORY)=====================
- Always check OPTIONAL CONTEXT before creating any new node.
- If an extracted item already exists in the graph (same or near-same meaning/title), DO NOT create a duplicate node.
- Reuse the existing node's id from OPTIONAL CONTEXT and only create new relationships if needed.
- If uncertain whether it is the same node: Create a Concept node, Add tag "unverified", Add a warning in meta.warnings about possible duplication.
=====================RELATIONSHIP DIRECTION RULES (MUST FOLLOW)=====================
- Decision (from) BASED_ON -> Requirement | Concept | Fact (to)
- Requirement (from) GOVERNS -> Project | Task | Agent (to)
- Fact (from) SUPPORTS / REFUTES -> Concept | Requirement | Decision | Project (to)
- Task (from) RESULTED_IN -> History (to)
- Project (from) HAS_* -> Concept | Requirement | Decision | Task (to)
- Concept (from) IMPLEMENTS_BY -> File | Logic (to)
- Concept (from) RELATES_TO -> Concept (to)
Never invert from_id and to_id for these relationships.
=====================FACT RULES (NON-NEGOTIABLE)=====================
- Fact nodes REQUIRE a valid source_url.
- If source_url is missing: DO NOT create a Fact node. Create a Concept node with tag "unverified". Add a warning explaining why Fact was downgraded.
=====================POLICY / DECISION CONFLICT AWARENESS=====================
- If you extract a Requirement or Decision that may conflict with existing known_requirements or known_decisions in OPTIONAL CONTEXT:
- Do NOT resolve or override silently. Keep both items. Add a warning in meta.warnings describing the suspected conflict (existing id/title vs new item).
- If uncertainty is high, tag the new item with "unverified".
=====================FIELD REQUIREMENTS=====================
- Every node MUST include: id (UUID-like string), project_id, source_message_id
- embedding_id may be null.
Failure to follow this contract is considered an extraction failure."""

        user_prompt = f"""PROJECT CONTEXT
- project_id: {msg.project_id or "system-master"}

MESSAGE CONTEXT
- source_message_id: {msg.message_id}
- sender_role: {msg.sender_role}
- timestamp: {msg.timestamp.isoformat()}

MESSAGE CONTENT
{msg.content}

OPTIONAL CONTEXT (Existing Knowledge Graph Snapshot)
- known_concepts: {json.dumps(context.get('known_concepts', []), ensure_ascii=False)}
- known_requirements: {json.dumps(context.get('known_requirements', []), ensure_ascii=False)}
- known_decisions: {json.dumps(context.get('known_decisions', []), ensure_ascii=False)}
- known_tasks: {json.dumps(context.get('known_tasks', []), ensure_ascii=False)}

            EXTRACTION GOALS: Identify Decisions, Requirements, Concepts, Tasks, History, Facts. Map to nodes. Reuse IDs. Meaningful relationships. Warn on ambiguity/conflict.
            MANDATORY: Define at least one relationship (e.g. RELATED_TO, HAS_CONCEPT) between the extracted nodes."""

        try:
            response = await llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt)
            ])
            
            content = response.content.strip()
            
            # [Task: Extraction Quality Check] Log raw extraction JSON
            print(f"DEBUG: [Extraction] Raw JSON from LLM (first 500 chars): {content[:500]}")
            
            # Clean up potential markdown formatting
            if content.startswith("```"):
                content = re.sub(r"^```[a-z]*\n", "", content)
                content = re.sub(r"\n```$", "", content)
            
            data = json.loads(content)
            
            # [v5.0 CRITICAL] Log nodes and relationships count
            node_count = len(data.get("nodes", []))
            rel_count = len(data.get("relationships", []))
            print(f"DEBUG: [Extraction] Extracted {node_count} nodes and {rel_count} relationships")
            
            # [Task: Extraction Quality Check] Warn if empty relationship list
            if rel_count == 0 and node_count > 0:
                print(f"WARNING: [Extraction] Nodes extracted but NO relationships found! LLM failed to follow prompt.")
                print(f"DEBUG: [Extraction] Nodes: {[n.get('title', n.get('id')) for n in data.get('nodes', [])]}")

            usage = response.response_metadata.get("token_usage", {"prompt_tokens": 0, "completion_tokens": 0})
            return data, usage
        except Exception as e:
            logger.error("LLM extraction error", error=str(e), content=response.content if 'response' in locals() else "N/A")
            return None, {}

    def _estimate_cost(self, tier: str, usage: dict) -> float:
        p_tokens = usage.get("prompt_tokens", 0)
        c_tokens = usage.get("completion_tokens", 0)
        rate = 0.01 if tier == "high" else 0.001
        return (p_tokens + c_tokens) / 1000.0 * rate

    async def _log_cost(self, msg: MessageModel, e_type: str, tier: str, t_in: int, t_out: int, cost: float, status: str):
        async with AsyncSessionLocal() as session:
            log = CostLogModel(
                project_id=msg.project_id,
                message_id=msg.message_id,
                extraction_type=e_type,
                model_tier=tier,
                model_name=settings.LLM_HIGH_TIER_MODEL if tier == "high" else settings.LLM_LOW_TIER_MODEL,
                tokens_in=t_in,
                tokens_out=t_out,
                estimated_cost=cost,
                status=status
            )
            session.add(log)
            await session.commit()

    def _get_embeddable_text(self, node: Dict) -> str:
        """
        노드를 임베딩 가능한 텍스트로 변환
        
        Args:
            node: 지식 노드 딕셔너리
        
        Returns:
            임베딩할 텍스트
        """
        parts = []
        
        # 타입
        n_type = node.get("type", "")
        if n_type:
            parts.append(f"Type: {n_type}")
        
        # 제목/이름
        if "title" in node:
            parts.append(f"Title: {node['title']}")
        elif "name" in node:
            parts.append(f"Name: {node['name']}")
        
        # 설명/내용
        props = node.get("properties", {})
        if "description" in props:
            parts.append(f"Description: {props['description']}")
        if "content" in props:
            parts.append(f"Content: {props['content']}")
        if "summary" in props:
            parts.append(f"Summary: {props['summary']}")
        
        return "\n".join(parts) if parts else ""

    async def _upsert_to_neo4j(self, msg: MessageModel, extracted: Any):
        if not isinstance(extracted, dict):
            return

        project_id = str(msg.project_id) if msg.project_id else "system-master"
        source_message_id = str(msg.message_id)
        
        # [Task: Neo4j Node ID Trace] Verify Project ID matching
        # Force normalize if needed (though project_id coming from DB should be UUID)
        # We use str(msg.project_id) or "system-master"
        
        logger.info("AUDIT: _upsert_to_neo4j called", project_id=project_id, source_message_id=source_message_id)

        async with neo4j_client.driver.session() as session:
            # [CRITICAL UPDATE v5.0] Force Transaction for ACID compliance
            # Use write_transaction for atomic operations
            
            async def _upsert_tx(tx, p_id, src_msg_id, nodes, rels):
                # 0. Ensure Project exists
                await tx.run("""
                    MERGE (p:Project {id: $project_id})
                    ON CREATE SET p.name = 'Auto-Created Project', p.timestamp = datetime()
                """, {"project_id": p_id})

                # 1. Upsert Nodes & Build ID Mapping
                created_count = 0
                node_id_map = {}  # [v5.0 CRITICAL] LLM ID -> Real Neo4j ID
                
                for node in nodes:
                    llm_id = node.get("id")
                    n_type = node.get("type", "Concept")
                    content_key = node.get("title") or node.get("name") or node.get("content", "")
                    if content_key:
                        hash_input = f"{p_id}:{n_type}:{content_key}".encode('utf-8')
                        content_hash = hashlib.sha256(hash_input).hexdigest()[:16]
                        n_id = f"kg-{content_hash}"
                        if llm_id:
                            node_id_map[llm_id] = n_id
                    else:
                        n_id = node.get("id") or str(uuid.uuid4())
                    
                    props = node.get("properties", {})
                    props.update({
                        "id": n_id,
                        "project_id": p_id,
                        "source_message_id": src_msg_id,
                        "created_at": node.get("created_at") or datetime.now(timezone.utc).isoformat()
                    })
                    
                    if n_type in ['Concept', 'Requirement', 'Decision', 'Logic', 'Task']:
                        props["is_cognitive"] = True
                        
                    # [Fix] Strict Title Fallback
                    n_title = node.get("title") or node.get("name")
                    if not n_title and node.get("content"):
                         n_title = node.get("content")[:50] + "..."
                    if not n_title:
                         n_title = f"Untitled Node-{str(uuid.uuid4())[:8]}"
                    
                    if "title" not in props: props["title"] = n_title
                    if "name" not in props: props["name"] = n_title
                    
                    await tx.run(f"MERGE (n:{n_type} {{id: $n_id}}) SET n += $props", {"n_id": n_id, "props": props})
                    created_count += 1
                
                print(f"DEBUG: [Neo4j] Node ID mapping created: {len(node_id_map)} entries")
                
                # 2. Upsert Relationships
                rel_count = 0
                print(f"DEBUG: [Neo4j] Attempting to create {len(rels)} relationships")
                for rel in rels:
                    rel_type = rel.get("type", "RELATES_TO").replace(" ", "_").upper()
                    # [v5.0 CRITICAL FIX] LLM uses various field names: source_id, target_id, source, target, from_id, to_id
                    from_id_raw = (rel.get("source_id") or rel.get("from_id") or 
                              rel.get("start_node_id") or rel.get("source"))
                    to_id_raw = (rel.get("target_id") or rel.get("to_id") or 
                            rel.get("end_node_id") or rel.get("target"))
                    
                    if not from_id_raw or not to_id_raw:
                        print(f"WARNING: [Neo4j] Skipping relationship with missing IDs: {rel}")
                        continue
                    
                    # [v5.0 CRITICAL] Map LLM IDs to real Neo4j IDs
                    from_id = node_id_map.get(from_id_raw, from_id_raw)
                    to_id = node_id_map.get(to_id_raw, to_id_raw)
                    
                    print(f"DEBUG: [Neo4j] Creating {rel_type}: {from_id[:8]}... -> {to_id[:8]}...")
                    print(f"       [ID Mapping] {from_id_raw[:8]}... => {from_id[:8]}...")
                        
                    result = await tx.run(f"""
                        MATCH (a {{id: $from_id}})
                        WITH a
                        MATCH (b {{id: $to_id}})
                        MERGE (a)-[r:{rel_type}]->(b)
                        SET r.source_message_id = $source_message_id,
                            r.project_id = $project_id
                        RETURN a.id as source, b.id as target
                    """, {
                        "from_id": from_id, 
                        "to_id": to_id, 
                        "source_message_id": src_msg_id,
                        "project_id": p_id
                    })
                    
                    created = await result.single()
                    if created:
                        print(f"       ✅ Relationship created successfully")
                    else:
                        print(f"       ❌ Failed - Nodes not found in DB!")
                    rel_count += 1
                
                print(f"DEBUG: [Neo4j] Successfully created {rel_count} relationships")
                
                # 3. Connect to Project
                await tx.run("""
                    MATCH (p:Project {id: $project_id})
                    WITH p
                    MATCH (n {source_message_id: $source_message_id})
                    WHERE n <> p
                    MERGE (p)-[:HAS_KNOWLEDGE]->(n)
                """, {"project_id": p_id, "source_message_id": src_msg_id})
                
                return created_count, rel_count

            # Execute Transaction
            try:
                cnt_nodes, cnt_rels = await session.execute_write(_upsert_tx, project_id, source_message_id, extracted.get("nodes", []), extracted.get("relationships", []))
                logger.info(f"[Neo4j] AUDIT: Transaction COMMITTED. Merged {cnt_nodes} nodes and {cnt_rels} relationships.", project_id=project_id)
            except Exception as e:
                logger.error(f"[Neo4j] AUDIT: Transaction FAILED: {e}")
                raise e
        
        # 4. [신규] Vector DB에 임베딩 저장
        from app.services.embedding_service import embedding_service
        from app.core.vector_store import PineconeClient
        
        vector_client = PineconeClient()
        
        for node in extracted.get("nodes", []):
            n_id = node.get("id", "")
            n_type = node.get("type", "Concept")
            
            # 임베딩 대상 텍스트 생성
            embed_text = self._get_embeddable_text(node)
            
            if not embed_text:
                logger.warning("No embeddable text for node", node_id=n_id, node_type=n_type)
                continue
            
            try:
                # 임베딩 생성
                embedding = await embedding_service.generate_embedding(embed_text)
                
                # Vector DB 저장
                await vector_client.upsert_vectors(
                    tenant_id=project_id,
                    vectors=[{
                        "id": n_id,
                        "values": embedding,
                        "metadata": {
                            "type": n_type,
                            "project_id": project_id,
                            "node_id": n_id,  # [v5.0 Critical] Neo4j ID for frontend navigation
                            "title": node.get("title") or node.get("name") or (embed_text[:50] + "..." if embed_text else "Untitled"), # [v4.2 FIX] Fallback title
                            "text": embed_text[:4000],  # [v4.2] Store original text (truncated)
                            "source": "knowledge",
                            "source_message_id": source_message_id,
                            "is_cognitive": n_type in ['Concept', 'Decision', 'Requirement', 'Logic', 'Task'],
                            "created_at": datetime.now(timezone.utc).isoformat()
                        }
                    }],
                    namespace="knowledge"
                )
                
                # Neo4j에 embedding_id 저장
                async with neo4j_client.driver.session() as session:
                    await session.run("""
                        MATCH (n {id: $n_id})
                        SET n.embedding_id = $n_id,
                            n.has_embedding = true
                    """, {"n_id": n_id})
                
                logger.debug(
                    "Embedding saved to Vector DB",
                    node_id=n_id,
                    node_type=n_type,
                    vector_dim=len(embedding)
                )
                
            except Exception as e:
                logger.error(
                    "Failed to save embedding to Vector DB",
                    node_id=n_id,
                    node_type=n_type,
                    error=str(e)
                )

    async def process_batch_pipeline(self, project_id: str, message_ids: List[uuid.UUID]):
        """
        [9.2.2] Merging Batch Extraction.
        Combines multiple messages into a single LLM call to save tokens.
        """
        if not message_ids: return

        async with AsyncSessionLocal() as session:
            # 1. Idempotency Check (9.2.4)
            query_done = select(CostLogModel.message_id).where(
                and_(CostLogModel.message_id.in_(message_ids), CostLogModel.status == 'success')
            )
            done_ids = (await session.execute(query_done)).scalars().all()
            
            to_process = [m_id for m_id in message_ids if m_id not in done_ids]
            if not to_process:
                logger.info("All messages in batch already processed", count=len(message_ids))
                return

            # 2. Load Message Contents
            query_msgs = select(MessageModel).where(MessageModel.message_id.in_(to_process)).order_by(MessageModel.timestamp.asc())
            msgs = (await session.execute(query_msgs)).scalars().all()
            if not msgs: return

        # 3. Budget Check
        await self.check_budget_and_mode()
        tier = "low" # Batch is always low tier per 9.1.4

        # 4. Extract
        try:
            combined_text = "\n---\n".join([f"[{m.sender_role}]: {m.content}" for m in msgs])
            
            p_id_uuid = None
            if project_id != "global" and project_id != "system-master":
                try: p_id_uuid = uuid.UUID(project_id)
                except: pass
                
            context_snapshot = await self._get_context_snapshot(p_id_uuid)
            
            logger.info("Extracting knowledge from merged batch", project_id=project_id, msg_count=len(msgs))
            extracted, usage = await self._llm_extract_merged(combined_text, tier, context_snapshot, project_id, [str(m.message_id) for m in msgs])
            
            if extracted:
                # 5. Upsert to Neo4j
                await self._upsert_batch_to_neo4j(project_id, extracted)
                
                # 6. Log Cost for each message
                total_cost = self._estimate_cost(tier, usage)
                cost_per_msg = total_cost / len(msgs)
                t_in_per_msg = usage.get("prompt_tokens", 0) // len(msgs)
                t_out_per_msg = usage.get("completion_tokens", 0) // len(msgs)
                
                for m in msgs:
                    await self._log_cost(m, "batch", tier, t_in_per_msg, t_out_per_msg, cost_per_msg, "success")
                logger.info("Batch knowledge stored", project_id=project_id)
            else:
                for m in msgs:
                    await self._log_cost(m, "batch", tier, 0, 0, 0.0, "fail")

        except Exception as e:
            logger.error("Batch pipeline failed", project_id=project_id, error=str(e))

    async def _llm_extract_merged(self, combined_text: str, tier: str, context: Dict[str, Any], project_id: str, message_ids: List[str]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        llm = self._get_llm(tier)
        
        system_prompt = """You are a SUPREME knowledge extraction engine for BATCH processing. 
TRANSFORM the conversation segment into specific cognitive entities.

=====================BATCH CONTRACT=====================
- USER input contains multiple messages separated by ---.
- You must create a unified graph representing the entire segment.
- Reuse existing IDs where possible.
- Minimum 50% cognitive nodes (Decision, Requirement, Concept, Logic).

=====================OUTPUT CONTRACT=====================
- Output MUST be valid JSON only. No markdown.
- Schema: { "nodes": [...], "relationships": [...], "meta": {...} }
- Every node MUST include: id (UUID-like), project_id, source_message_id (pick one from input for batch or use a unique one)
"""
        user_prompt = f"""PROJECT: {project_id}
MESSAGE IDs: {', '.join(message_ids)}

CONTENT:
{combined_text}

CONTEXT:
{json.dumps(context, ensure_ascii=False)}"""

        try:
            response = await llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
            content = response.content.strip()
            
            # [v5.0 DEBUG] Log raw extraction
            print(f"DEBUG: [Batch Extraction] Raw JSON from LLM (first 500 chars): {content[:500]}")
            
            if content.startswith("```"):
                content = re.sub(r"^```[a-z]*\n", "", content)
                content = re.sub(r"\n```$", "", content)
                
            data = json.loads(content)
            
            # [v5.0 CRITICAL] Log extraction counts
            node_count = len(data.get("nodes", []))
            rel_count = len(data.get("relationships", []))
            print(f"DEBUG: [Batch Extraction] Extracted {node_count} nodes and {rel_count} relationships")
            
            if rel_count == 0 and node_count > 0:
                print(f"WARNING: [Batch Extraction] Nodes extracted but NO relationships found! LLM failed to follow prompt.")
                print(f"DEBUG: [Batch Extraction] Nodes: {[n.get('title', n.get('id')) for n in data.get('nodes', [])]}")
            
            usage = response.response_metadata.get("token_usage", {"prompt_tokens": 0, "completion_tokens": 0})
            return data, usage
        except Exception as e:
            logger.error("Batch extraction error", error=str(e))
            print(f"ERROR: [Batch Extraction] Failed to parse LLM response: {e}")
            return None, {}

    async def _upsert_batch_to_neo4j(self, project_id: str, extracted: Dict[str, Any]):
        p_id = project_id if project_id != "global" and project_id != "system-master" else "system-master"
        async with neo4j_client.driver.session() as session:
            await session.run("MERGE (p:Project {id: $p_id}) ON CREATE SET p.name = 'Auto Project'", {"p_id": p_id})
            
            for node in extracted.get("nodes", []):
                n_type = node.get("type", "Concept")
                content_key = node.get("title") or node.get("name") or node.get("content", "")
                if content_key:
                    hash_input = f"{project_id}:{n_type}:{content_key}".encode('utf-8')
                    content_hash = hashlib.sha256(hash_input).hexdigest()[:16]
                    n_id = f"kg-{content_hash}"
                else:
                    n_id = node.get("id") or str(uuid.uuid4())
                props = node.get("properties", {})
                props.update({
                    "id": n_id,
                    "project_id": p_id,
                    "source_message_id": node.get("source_message_id") or "BATCH_" + str(uuid.uuid4())[:8],
                    "created_at": datetime.now(timezone.utc).isoformat()
                })
                # [KNOW-004] Boost cognitive
                if n_type in ['Concept', 'Requirement', 'Decision', 'Logic', 'Task']:
                    props["is_cognitive"] = True
                
                # Set title/name from props if not present in root (LLM might vary)
                # [Fix] Strict Title Fallback (Batch)
                n_title = node.get("title") or node.get("name")
                if not n_title and node.get("content"):
                        n_title = node.get("content")[:50] + "..."
                if not n_title:
                        n_title = f"Untitled Node-{str(uuid.uuid4())[:8]}"

                if "title" not in props: props["title"] = n_title
                if "name" not in props: props["name"] = n_title
                    
                await session.run(f"MERGE (n:{n_type} {{id: $n_id}}) SET n += $props", {"n_id": n_id, "props": props})
                await session.run("MATCH (p:Project {id: $p_id}), (n {id: $n_id}) MERGE (p)-[:HAS_KNOWLEDGE]->(n)", {"p_id": p_id, "n_id": n_id})

            # [v5.0 CRITICAL] Build Node ID mapping (LLM ID -> Real Neo4j ID)
            node_id_map = {}
            for node in extracted.get("nodes", []):
                llm_id = node.get("id")
                n_type = node.get("type", "Concept")
                content_key = node.get("title") or node.get("name") or node.get("content", "")
                if content_key:
                    hash_input = f"{project_id}:{n_type}:{content_key}".encode('utf-8')
                    content_hash = hashlib.sha256(hash_input).hexdigest()[:16]
                    real_id = f"kg-{content_hash}"
                    if llm_id:
                        node_id_map[llm_id] = real_id
            
            print(f"DEBUG: [Batch Neo4j] Node ID mapping created: {len(node_id_map)} entries")
            
            rels = extracted.get("relationships", [])
            print(f"DEBUG: [Batch Neo4j] Attempting to create {len(rels)} relationships")
            
            for rel in rels:
                rel_type = rel.get("type", "RELATES_TO").replace(" ", "_").upper()
                # [v5.0 CRITICAL FIX] LLM uses various field names: source_id, target_id, source, target, from_id, to_id
                from_id_raw = (rel.get("source_id") or rel.get("from_id") or 
                          rel.get("start_node_id") or rel.get("source"))
                to_id_raw = (rel.get("target_id") or rel.get("to_id") or 
                        rel.get("end_node_id") or rel.get("target"))
                
                if not from_id_raw or not to_id_raw:
                    print(f"WARNING: [Batch Neo4j] Skipping relationship with missing IDs: {rel}")
                    continue
                
                # [v5.0 CRITICAL] Map LLM IDs to real Neo4j IDs
                from_id = node_id_map.get(from_id_raw, from_id_raw)
                to_id = node_id_map.get(to_id_raw, to_id_raw)
                
                print(f"DEBUG: [Batch Neo4j] Creating {rel_type}: {from_id[:12]}... -> {to_id[:12]}...")
                print(f"       [ID Mapping] {from_id_raw[:12]}... => {from_id[:12]}...")
                    
                # Verify nodes exist before creating relationship
                result = await session.run(f"""
                    MATCH (a {{id: $from_id}}), (b {{id: $to_id}})
                    MERGE (a)-[r:{rel_type}]->(b)
                    SET r.project_id = $p_id
                    RETURN a.id as source, b.id as target
                """, {"from_id": from_id, "to_id": to_id, "p_id": p_id})
                
                created = await result.single()
                if created:
                    print(f"       ✅ Relationship created successfully")
                else:
                    print(f"       ❌ Failed - Nodes not found in DB!")
        
        # [신규] 배치 노드 임베딩 생성 및 Vector DB 저장
        from app.services.embedding_service import embedding_service
        from app.core.vector_store import PineconeClient
        
        vector_client = PineconeClient()
        nodes = extracted.get("nodes", [])
        
        if nodes:
            # 배치로 임베딩 생성 (효율적)
            embed_texts = []
            node_ids = []
            
            for node in nodes:
                # f-string 중첩 방지: 문자열 먼저 생성
                n_type = node.get("type", "Concept")
                n_title = node.get("title") or node.get("name", "")
                hash_input = f"{p_id}:{n_type}:{n_title}".encode()
                n_id = f"kg-{hashlib.sha256(hash_input).hexdigest()[:16]}"
                
                embed_text = self._get_embeddable_text(node)
                
                if embed_text:
                    embed_texts.append(embed_text)
                    node_ids.append((n_id, node))
            
            if embed_texts:
                try:
                    # 배치 임베딩 생성
                    embeddings = await embedding_service.generate_batch_embeddings(embed_texts)
                    
                    # Vector DB에 배치 저장
                    vectors = []
                    for i, (n_id, node) in enumerate(node_ids):
                        if i < len(embeddings) and embeddings[i]:
                            vectors.append({
                                "id": n_id,
                                "values": embeddings[i],
                                "metadata": {
                                    "type": node.get("type", "Concept"),
                                    "project_id": p_id,
                                    "node_id": n_id,  # [v5.0 Critical] Neo4j ID for frontend navigation
                                    "title": node.get("title") or node.get("name") or (embed_texts[i][:50] + "..." if embed_texts[i] else "Untitled"), # [v4.2 FIX] Fallback title
                                    "text": embed_texts[i][:4000],  # [v4.2] Store original text (truncated)
                                    "source": "knowledge",
                                    "is_cognitive": node.get("type") in ['Concept', 'Decision', 'Requirement', 'Logic', 'Task'],
                                    "created_at": datetime.now(timezone.utc).isoformat()
                                }
                            })
                    
                    if vectors:
                        await vector_client.upsert_vectors(
                            tenant_id=p_id,
                            vectors=vectors,
                            namespace="knowledge"
                        )
                        
                        # Neo4j에 embedding_id 업데이트
                        async with neo4j_client.driver.session() as session:
                            for n_id, _ in node_ids:
                                await session.run("""
                                    MATCH (n {id: $n_id})
                                    SET n.embedding_id = $n_id,
                                        n.has_embedding = true
                                """, {"n_id": n_id})
                        
                        logger.info(
                            "Batch embeddings saved to Vector DB",
                            project_id=p_id,
                            count=len(vectors)
                        )
                
                except Exception as e:
                    logger.error(
                        "Failed to save batch embeddings to Vector DB",
                        project_id=p_id,
                        error=str(e)
                    )

knowledge_service = KnowledgeService()

async def knowledge_worker():
    logger.info("Knowledge worker (Cost-Aware + Merging Batch) started")
    pending_batch = {} # project_id -> list of message_ids
    last_activity = {} # project_id -> timestamp

    while True:
        try:
            try:
                # Wait for message with short timeout to check for batch inactivity
                message_id = await asyncio.wait_for(knowledge_queue.get(), timeout=2.0)
                
                async with AsyncSessionLocal() as session:
                    res = await session.execute(select(MessageModel).filter(MessageModel.message_id == message_id))
                    msg = res.scalar_one_or_none()
                    if msg:
                        # Task 2.2: Pass metadata for role-based filtering
                        metadata = {"sender_role": msg.sender_role}
                        importance, _ = knowledge_service._evaluate_importance(msg.content, metadata)
                        p_id = str(msg.project_id or "system-master")
                        
                        if importance == "HIGH":
                            await knowledge_service.process_message_pipeline(message_id)
                        else:
                            if p_id not in pending_batch: pending_batch[p_id] = []
                            pending_batch[p_id].append(message_id)
                            last_activity[p_id] = datetime.now(timezone.utc)
                            
                            # [v5.0 DEBUG] Process immediately if batch size >= 2 (for faster testing)
                            if len(pending_batch[p_id]) >= 2:
                                m_ids = pending_batch.pop(p_id)
                                del last_activity[p_id]
                                logger.info("Batch size threshold reached, processing immediately", project_id=p_id, count=len(m_ids))
                                await knowledge_service.process_batch_pipeline(p_id, m_ids)
                
                knowledge_queue.task_done()
            except asyncio.TimeoutError:
                # 9.2.2 Inactivity Check (30 seconds)
                now = datetime.now(timezone.utc)
                for p_id in list(pending_batch.keys()):
                    if (now - last_activity.get(p_id, now)).total_seconds() >= settings.BATCH_INTERVAL_SEC:
                        m_ids = pending_batch.pop(p_id)
                        del last_activity[p_id]
                        logger.info("Inactivity triggered batch process", project_id=p_id, count=len(m_ids))
                        await knowledge_service.process_batch_pipeline(p_id, m_ids)
        except Exception as e:
            logger.error("Worker loop error", error=str(e))
            await asyncio.sleep(5)
