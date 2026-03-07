import uuid
from typing import List, Dict, Any, Optional
try:
    from pinecone import Pinecone
except Exception:
    try:
        from pinecone_client import Pinecone
    except Exception:
        raise RuntimeError(
            "Pinecone SDK import failed. Install `pinecone` (`pip install pinecone`) "
            "or keep `pinecone-client` for legacy compatibility."
        )
from app.core.config import settings

class PineconeClient:
    """
    Client for interacting with Pinecone Vector Database.
    Enforces tenant_id isolation in all operations.
    """
    
    def __init__(self):
        if not settings.PINECONE_API_KEY:
            # For development without keys, we can warn or mock
            print("⚠️ PINECONE_API_KEY not set. Vector store will not function.")
            self.client = None
            self.index = None
            return

        self.client = Pinecone(api_key=settings.PINECONE_API_KEY)
        self.index_name = settings.PINECONE_INDEX_NAME
        self.index = self.client.Index(self.index_name)

    async def upsert_vectors(
        self, 
        tenant_id: str, 
        vectors: List[Dict[str, Any]], 
        namespace: str = "default"
    ):
        """
        Upsert vectors with tenant_id metadata enforcement.
        
        Args:
            tenant_id: Tenant identifier for isolation
            vectors: List of dicts with 'id', 'values', 'metadata'
            namespace: Pinecone namespace (optional)
        """
        if not self.index:
            return

        # Enforce tenant_id in metadata
        for vec in vectors:
            if "metadata" not in vec:
                vec["metadata"] = {}
            vec["metadata"]["tenant_id"] = tenant_id

        # Upsert
        # Note: In async app, this should ideally be run in threadpool as Pinecone client is sync
        # For now, we call it directly.
        self.index.upsert(vectors=vectors, namespace=namespace)

    async def query_vectors(
        self,
        tenant_id: str,
        vector: List[float],
        top_k: int = 5,
        filter_metadata: Optional[Dict] = None,
        namespace: str = "default"
    ) -> List[Dict]:
        """
        Query vectors with tenant_id filter enforcement.
        """
        if not self.index:
            return []

        # Construct filter
        # [Task: Vector Filter Debug] In AIBizPlan v5.0, we use 'project_id' as the isolation key for knowledge,
        # but 'tenant_id' argument name is used here. We need to be careful.
        # If the caller passes project_id as tenant_id, then we are filtering by tenant_id field in Pinecone.
        # HOWEVER, in knowledge_service.py: upsert_vectors(tenant_id=project_id, ...)
        # And inside upsert_vectors: vec["metadata"]["tenant_id"] = tenant_id (which is project_id)
        # So 'tenant_id' field in Pinecone metadata actually holds 'project_id' value for knowledge vectors.
        # BUT, knowledge_service.py also sets vec["metadata"]["project_id"] = project_id explicitly.
        
        # Let's verify what we are filtering on.
        query_filter = {"tenant_id": tenant_id}
        if filter_metadata:
            query_filter.update(filter_metadata)
            
        # [Task: Vector Filter Debug] Log the exact filter
        import structlog
        logger = structlog.get_logger(__name__)
        logger.info(f"AUDIT: query_vectors called", filter=query_filter, namespace=namespace, top_k=top_k)
        
        # [Verification] Check for key consistency
        if 'project_id' in query_filter and 'tenant_id' in query_filter:
             if query_filter['project_id'] != query_filter['tenant_id']:
                 logger.warning("AUDIT: Mismatch between project_id and tenant_id in filter", project_id=query_filter.get('project_id'), tenant_id=query_filter.get('tenant_id'))

        results = self.index.query(
            vector=vector,
            top_k=top_k,
            filter=query_filter,
            include_metadata=True,
            namespace=namespace
        )
        
        # structlog.get_logger(__name__).debug(f"[Vector Debug] Found {len(results.matches)} matches.")

        return [
            {
                "id": match.id,
                "score": match.score,
                "metadata": match.metadata
            }
            for match in results.matches
        ]
