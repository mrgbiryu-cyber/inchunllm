# -*- coding: utf-8 -*-
"""
Conversation Chunking Service
대화 청킹 및 요약 서비스
"""
import uuid
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from structlog import get_logger

from app.core.database import get_messages_from_rdb
from app.core.neo4j_client import neo4j_client
from app.core.vector_store import PineconeClient
from app.services.embedding_service import embedding_service
from app.services.knowledge_service import knowledge_service
from app.core.config import settings
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

logger = get_logger(__name__)


class ConversationChunkingService:
    """
    대화 청킹 서비스
    
    역할:
    1. 대화 메시지를 주기적으로 청킹 (시간/개수/주제 변경 기준)
    2. LLM으로 요약 생성
    3. Neo4j에 ConversationChunk 노드 저장
    4. Vector DB에 임베딩 저장
    """
    
    def __init__(self):
        self.pending_chunks = {}  # project_id -> {messages: [], last_activity: datetime}
        
        # LLM 초기화 (요약용)
        self.llm = ChatOpenAI(
            model="google/gemini-2.0-flash-001",  # 요약용 저렴한 모델
            api_key=settings.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            temperature=0.1,  # 요약은 창의성 불필요
        )
    
    async def add_message_to_pending(
        self, 
        project_id: str, 
        thread_id: str, 
        message: Dict[str, Any]
    ):
        """
        대기 중인 청크에 메시지 추가
        
        Args:
            project_id: 프로젝트 ID
            thread_id: 스레드 ID
            message: 메시지 딕셔너리
        """
        key = f"{project_id}:{thread_id}"
        
        if key not in self.pending_chunks:
            self.pending_chunks[key] = {
                "project_id": project_id,
                "thread_id": thread_id,
                "messages": [],
                "last_activity": datetime.now(timezone.utc)
            }
        
        self.pending_chunks[key]["messages"].append(message)
        self.pending_chunks[key]["last_activity"] = datetime.now(timezone.utc)
        
        # 청킹 트리거 확인
        if await self.should_trigger_chunking(key):
            await self.create_chunk(key)
    
    async def should_trigger_chunking(self, key: str) -> bool:
        """
        청킹 트리거 조건 확인
        
        조건 (OR):
        1. 시간 기반: 마지막 메시지 후 5분 경과
        2. 메시지 개수: 10개 이상 누적
        3. 주제 변경: TOPIC_SHIFT 인텐트 감지 시
        
        Returns:
            True: 청킹 실행, False: 대기
        """
        if key not in self.pending_chunks:
            return False
        
        chunk_data = self.pending_chunks[key]
        messages = chunk_data["messages"]
        last_activity = chunk_data["last_activity"]
        
        # 조건 1: 시간 기반 (5분)
        if (datetime.now(timezone.utc) - last_activity).total_seconds() >= 300:
            logger.info("Chunking triggered by time", key=key)
            return True
        
        # 조건 2: 메시지 개수 (10개)
        if len(messages) >= 10:
            logger.info("Chunking triggered by message count", key=key, count=len(messages))
            return True
        
        # 조건 3: 주제 변경 (마지막 메시지 확인)
        if messages and messages[-1].get("intent") == "TOPIC_SHIFT":
            logger.info("Chunking triggered by topic shift", key=key)
            return True
        
        return False
    
    async def create_chunk(self, key: str):
        """
        청킹 실행
        
        1. 대기 중인 메시지들 가져오기
        2. 정크 필터링 (기존 knowledge_service 로직 재사용)
        3. LLM 요약
        4. Neo4j 저장
        5. Vector DB 저장
        """
        chunk_data = self.pending_chunks.pop(key, {})
        messages = chunk_data.get("messages", [])
        project_id = chunk_data.get("project_id")
        thread_id = chunk_data.get("thread_id")
        
        if not messages or not project_id:
            return
        
        logger.info(
            "Creating conversation chunk",
            project_id=project_id,
            thread_id=thread_id,
            message_count=len(messages)
        )
        
        # 1. 정크 필터링 (기존 로직 재사용)
        filtered_messages = []
        for msg in messages:
            importance, _ = knowledge_service._evaluate_importance(
                msg.get("content", ""), 
                {"sender_role": msg.get("sender_role", "user")}
            )
            if importance != "NONE":  # 정크 아니면 포함
                filtered_messages.append(msg)
        
        if not filtered_messages:
            logger.info(
                "All messages filtered as junk, skipping chunk",
                project_id=project_id,
                original_count=len(messages)
            )
            return
        
        # 2. LLM 요약
        summary, summary_tokens = await self._summarize_conversation(filtered_messages)
        
        if not summary:
            logger.warning(
                "Failed to summarize conversation",
                project_id=project_id,
                message_count=len(filtered_messages)
            )
            return
        
        # 3. 청크 ID 생성
        chunk_id = f"conv-{uuid.uuid4().hex[:16]}"
        
        # 4. Neo4j 저장
        await self._save_chunk_to_neo4j(
            chunk_id=chunk_id,
            project_id=project_id,
            thread_id=thread_id,
            messages=filtered_messages,
            summary=summary,
            summary_tokens=summary_tokens
        )
        
        # 5. Vector DB 저장
        await self._save_chunk_to_vector_db(
            chunk_id=chunk_id,
            project_id=project_id,
            thread_id=thread_id,
            messages=filtered_messages,
            summary=summary
        )
        
        logger.info(
            "Conversation chunk created successfully",
            chunk_id=chunk_id,
            project_id=project_id,
            filtered_message_count=len(filtered_messages),
            summary_length=len(summary)
        )
    
    async def _summarize_conversation(
        self, 
        messages: List[Dict]
    ) -> tuple[str, Dict[str, int]]:
        """
        대화를 요약하되, 정보 손실 최소화
        
        Args:
            messages: 메시지 리스트
        
        Returns:
            (summary, tokens): 요약 텍스트와 토큰 정보
        """
        system_prompt = """당신은 대화 요약 전문가입니다.

목표: 대화 내용을 200-300 토큰으로 압축하되, 핵심 정보는 유지하세요.

필수 포함 사항:
1. 사용자의 주요 요구사항/목표
2. 결정된 사항 (기술 스택, 설계 방향 등)
3. 중요한 변경 사항 (시간 변경, 요구사항 수정 등)
4. 주요 질문과 답변

제외 사항:
- 인사, 감탄사 (안녕, ㅋㅋ, 고마워 등)
- 시스템 메시지
- 반복적인 확인 메시지

출력 형식:
- 간결한 한국어
- 명사형 종결 (예: "~을 원함", "~로 결정")
- 시간순 정렬
- 핵심 키워드 강조"""
        
        conversation_text = "\n".join([
            f"[{msg.get('timestamp', '')}] {msg.get('sender_role', 'user')}: {msg.get('content', '')}"
            for msg in messages
        ])
        
        user_prompt = f"""아래 대화를 요약하세요:

{conversation_text}

요약:"""
        
        try:
            response = await self.llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt)
            ])
            
            summary = response.content.strip()
            tokens = response.response_metadata.get("token_usage", {})
            
            return summary, tokens
            
        except Exception as e:
            logger.error(
                "Failed to summarize conversation",
                error=str(e),
                message_count=len(messages)
            )
            return "", {}
    
    async def _save_chunk_to_neo4j(
        self,
        chunk_id: str,
        project_id: str,
        thread_id: str,
        messages: List[Dict],
        summary: str,
        summary_tokens: Dict[str, int]
    ):
        """
        청크를 Neo4j에 저장
        
        ConversationChunk 노드 생성:
        - chunk_id, project_id, thread_id
        - 시간 범위 (start_time, end_time)
        - 메시지 개수
        - 요약 텍스트
        - 토큰 정보
        """
        start_time = messages[0].get("timestamp") if messages else datetime.now(timezone.utc)
        end_time = messages[-1].get("timestamp") if messages else datetime.now(timezone.utc)
        
        original_tokens = sum(len(m.get("content", "")) // 2 for m in messages)  # 간단 추정
        compression_ratio = summary_tokens.get("total_tokens", 0) / original_tokens if original_tokens > 0 else 0
        
        async with neo4j_client.driver.session() as session:
            # 1. ConversationChunk 노드 생성
            await session.run("""
                CREATE (chunk:ConversationChunk {
                    chunk_id: $chunk_id,
                    project_id: $project_id,
                    thread_id: $thread_id,
                    chunk_start_time: datetime($start_time),
                    chunk_end_time: datetime($end_time),
                    message_count: $message_count,
                    first_message_id: $first_message_id,
                    last_message_id: $last_message_id,
                    summary: $summary,
                    original_tokens: $original_tokens,
                    summary_tokens: $summary_tokens,
                    compression_ratio: $compression_ratio,
                    is_junk_filtered: true,
                    has_embedding: false,
                    created_at: datetime()
                })
            """, {
                "chunk_id": chunk_id,
                "project_id": project_id,
                "thread_id": thread_id,
                "start_time": start_time.isoformat() if isinstance(start_time, datetime) else str(start_time),
                "end_time": end_time.isoformat() if isinstance(end_time, datetime) else str(end_time),
                "message_count": len(messages),
                "first_message_id": messages[0].get("message_id", "") if messages else "",
                "last_message_id": messages[-1].get("message_id", "") if messages else "",
                "summary": summary,
                "original_tokens": original_tokens,
                "summary_tokens": summary_tokens.get("total_tokens", 0),
                "compression_ratio": compression_ratio
            })
            
            # 2. Project와 연결
            await session.run("""
                MATCH (p:Project {id: $project_id}), (chunk:ConversationChunk {chunk_id: $chunk_id})
                MERGE (p)-[:HAS_CONVERSATION_CHUNK]->(chunk)
            """, {"project_id": project_id, "chunk_id": chunk_id})
            
            logger.debug(
                "Chunk saved to Neo4j",
                chunk_id=chunk_id,
                project_id=project_id
            )
    
    async def _save_chunk_to_vector_db(
        self,
        chunk_id: str,
        project_id: str,
        thread_id: str,
        messages: List[Dict],
        summary: str
    ):
        """
        청크를 Vector DB에 저장 (임베딩 생성)
        """
        # 임베딩 대상 텍스트 생성
        embed_text = self._create_chunk_embed_text(summary, messages)
        
        if not embed_text:
            logger.warning(
                "No embeddable text for chunk",
                chunk_id=chunk_id
            )
            return
        
        try:
            # 임베딩 생성
            embedding = await embedding_service.generate_embedding(embed_text)
            
            # Vector DB 저장
            vector_client = PineconeClient()
            await vector_client.upsert_vectors(
                tenant_id=project_id,
                vectors=[{
                    "id": chunk_id,
                    "values": embedding,
                    "metadata": {
                        "type": "ConversationChunk",
                        "project_id": project_id,
                        "thread_id": thread_id,
                        "summary": summary[:200],  # Pinecone 메타데이터 크기 제한
                        "start_time": messages[0].get("timestamp").isoformat() if messages else "",
                        "end_time": messages[-1].get("timestamp").isoformat() if messages else "",
                        "message_count": len(messages),
                        "source": "conversation",
                        "created_at": datetime.now(timezone.utc).isoformat()
                    }
                }],
                namespace="conversation"
            )
            
            # Neo4j에 has_embedding 업데이트
            async with neo4j_client.driver.session() as session:
                await session.run("""
                    MATCH (chunk:ConversationChunk {chunk_id: $chunk_id})
                    SET chunk.has_embedding = true
                """, {"chunk_id": chunk_id})
            
            logger.debug(
                "Chunk embedding saved to Vector DB",
                chunk_id=chunk_id,
                vector_dim=len(embedding)
            )
            
        except Exception as e:
            logger.error(
                "Failed to save chunk embedding to Vector DB",
                chunk_id=chunk_id,
                error=str(e)
            )
    
    def _create_chunk_embed_text(
        self, 
        summary: str, 
        messages: List[Dict]
    ) -> str:
        """
        청크를 임베딩 가능한 텍스트로 변환
        """
        parts = [
            f"대화 요약: {summary}",
            f"메시지 개수: {len(messages)}",
        ]
        
        if messages:
            parts.append(f"시간 범위: {messages[0].get('timestamp')} ~ {messages[-1].get('timestamp')}")
        
        # 주요 키워드 추출 (선택)
        keywords = self._extract_keywords(messages)
        if keywords:
            parts.append(f"주요 키워드: {', '.join(keywords)}")
        
        return "\n".join(parts)
    
    def _extract_keywords(self, messages: List[Dict], top_k: int = 5) -> List[str]:
        """
        메시지에서 주요 키워드 추출 (간단한 빈도 기반)
        """
        # 간단한 키워드 추출 (실제로는 더 정교한 방법 사용 가능)
        word_freq = {}
        stop_words = {"의", "가", "이", "은", "는", "을", "를", "에", "와", "과", "도", "로", "으로", "에서"}
        
        for msg in messages:
            content = msg.get("content", "")
            words = content.split()
            for word in words:
                if word not in stop_words and len(word) > 1:
                    word_freq[word] = word_freq.get(word, 0) + 1
        
        # 상위 top_k 키워드 반환
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        return [word for word, _ in sorted_words[:top_k]]


# 싱글톤 인스턴스
conversation_chunking_service = ConversationChunkingService()
