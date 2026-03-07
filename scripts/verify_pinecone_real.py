import asyncio
import sys
import os
import uuid
from datetime import datetime, timezone

# Adjust path
sys.path.append(os.getcwd())

from app.core.database import AsyncSessionLocal, MessageModel, ThreadModel
from app.services.knowledge_service import knowledge_service
from app.core.vector_store import PineconeClient
from app.services.embedding_service import embedding_service
from sqlalchemy import select, text

# [UTF-8]
if sys.stdout.encoding is None or sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

async def verify_ingestion_and_search():
    print("🧪 Starting End-to-End Ingestion & Search Verification...")
    
    project_id = "system-master"
    test_msg_id = str(uuid.uuid4())
    test_content = f"BUJA v5.0 Integrity Test: This is a verification node created at {datetime.now(timezone.utc).isoformat()}. It ensures Pinecone and Neo4j are synced."
    
    print(f"\n[1] Ingesting Test Message (ID: {test_msg_id})...")
    
    # 1. Create Message in RDB
    async with AsyncSessionLocal() as session:
        msg = MessageModel(
            message_id=test_msg_id,
            project_id=None, # system-master maps to None or "system-master" depending on logic, let's use string if model allows or None if UUID enforced.
            # MessageModel.project_id is GUID. system-master is special. 
            # In `create_project`, system-master has ID "system-master" (string) but DB column is GUID.
            # Wait, `backend/app/models.py` says `project_id = Column(GUID(), ...)`? 
            # Let's check `_normalize_project_id`.
            sender_role="user",
            content=test_content,
            timestamp=datetime.now(timezone.utc)
        )
        # However, for system-master, we usually pass project_id="system-master" in API, but RDB stores NULL or specific GUID?
        # Let's try to mock the `msg` object directly for `knowledge_service` without saving to RDB to avoid constraint issues if system-master GUID is tricky.
        pass

    # Mock Message Object
    class MockMessage:
        message_id = test_msg_id
        project_id = "system-master" # Force string for logic
        sender_role = "user"
        content = test_content
        timestamp = datetime.now(timezone.utc)
        
    msg = MockMessage()
    
    # 2. Trigger Extraction Pipeline
    print("   Triggering knowledge extraction pipeline...")
    await knowledge_service.process_message_pipeline(msg.message_id) 
    # Wait, process_message_pipeline fetches from DB. We must insert into DB or bypass.
    # Let's bypass and call `_llm_extract` and `_upsert_to_neo4j` directly?
    # No, better to simulate real flow if possible.
    # But `process_message_pipeline` requires DB record.
    
    # Let's bypass DB fetch and call internal methods
    print("   Bypassing DB fetch, calling _upsert_to_neo4j directly with mock extraction...")
    
    # Mock Extraction Result
    extracted = {
        "nodes": [
            {
                "id": f"test-node-{uuid.uuid4()}",
                "type": "Fact",
                "title": "BUJA Verification Node",
                "content": test_content,
                "properties": {"source": "script"}
            }
        ],
        "relationships": []
    }
    
    await knowledge_service._upsert_to_neo4j(msg, extracted)
    print("   ✅ Upsert completed.")

    # 3. Verify Pinecone Search
    print("\n[2] Verifying Pinecone Search...")
    pc = PineconeClient()
    
    # Generate Embedding for query
    query_text = "BUJA Verification"
    query_vec = await embedding_service.generate_embedding(query_text)
    
    print(f"   Querying for '{query_text}' with filter project_id='system-master'...")
    results = await pc.query_vectors(
        tenant_id="system-master",
        vector=query_vec,
        top_k=5,
        namespace="knowledge"
    )
    
    found = False
    for match in results:
        print(f"   - Match: {match['id']} (Score: {match['score']})")
        meta = match['metadata']
        print(f"     Title: {meta.get('title')}")
        if "BUJA Verification Node" in (meta.get('title') or ""):
            found = True
            
    if found:
        print("\n✅ SUCCESS: Test node found in Pinecone!")
    else:
        print("\n❌ FAILURE: Test node NOT found in Pinecone.")

if __name__ == "__main__":
    asyncio.run(verify_ingestion_and_search())
