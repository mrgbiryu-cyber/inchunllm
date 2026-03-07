import asyncio
import os
import sys

# 프로젝트 루트를 path에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.services.embedding_service import embedding_service
from app.core.vector_store import PineconeClient
from app.core.neo4j_client import neo4j_client

async def check_embedding():
    print("\n--- 1. Embedding Service Check ---")
    try:
        text = "테스트 임베딩입니다."
        vector = await embedding_service.generate_embedding(text)
        print(f"✅ Embedding Success. Model: {embedding_service.model}")
        print(f"✅ Vector Dimension: {len(vector)}")
        return len(vector)
    except Exception as e:
        print(f"❌ Embedding Failed: {e}")
        return None

async def check_pinecone(expected_dim):
    print("\n--- 2. Pinecone Vector DB Check ---")
    if not settings.PINECONE_API_KEY:
        print("❌ PINECONE_API_KEY missing")
        return

    try:
        client = PineconeClient()
        if not client.index:
            print("❌ Pinecone Index connection failed")
            return
            
        # 인덱스 정보 조회 (동기 방식이라 describe_index_stats 사용)
        stats = client.index.describe_index_stats()
        print(f"✅ Index Stats: {stats}")
        print(f"⚠️ Note: Pinecone Client doesn't directly expose dimension in stats usually, but ensure it matches {expected_dim}")
        
        # 더미 쿼리로 차원 호환성 테스트
        dummy_vector = [0.1] * expected_dim
        results = await client.query_vectors(
            tenant_id="test-tenant",
            vector=dummy_vector,
            top_k=1
        )
        print(f"✅ Pinecone Query Success. Results found: {len(results)}")
        
    except Exception as e:
        print(f"❌ Pinecone Check Failed: {e}")

async def check_neo4j():
    print("\n--- 3. Neo4j Graph DB Check ---")
    try:
        connected = await neo4j_client.verify_connectivity()
        if connected:
            print("✅ Neo4j Connectivity: OK")
            
            # 인덱스 확인
            async with neo4j_client.driver.session() as session:
                result = await session.run("SHOW INDEXES")
                indexes = []
                async for record in result:
                    indexes.append(record["name"])
                print(f"✅ Found Indexes: {indexes}")
        else:
            print("❌ Neo4j Connectivity: Failed")
    except Exception as e:
        print(f"❌ Neo4j Check Failed: {e}")

async def main():
    print("=== System Connection Verification ===")
    dim = await check_embedding()
    if dim:
        await check_pinecone(dim)
    await check_neo4j()

if __name__ == "__main__":
    asyncio.run(main())
