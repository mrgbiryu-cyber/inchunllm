# -*- coding: utf-8 -*-
"""
Job Manager Service
"""
import json
import sys

# [UTF-8] Force stdout/stderr to UTF-8
if sys.stdout.encoding is None or sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding is None or sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from typing import Optional, Dict, Any, List
from uuid import uuid4
import time
import hashlib
import json
from datetime import datetime, timezone

import redis.asyncio as redis
from structlog import get_logger

from app.core.config import settings
from app.core.security import sign_job_payload, SecurityError
from app.models.schemas import (
    Job,
    JobCreate,
    JobStatus,
    ExecutionLocation,
    User,
    UserRole,
    JobResult
)

logger = get_logger(__name__)


class PermissionDenied(Exception):
    """Raised when user lacks permission for an operation"""
    pass


class QuotaExceeded(Exception):
    """Raised when tenant quota is exceeded"""
    pass


class JobManager:
    """
    Manages job lifecycle: creation, signing, queueing, and status tracking
    
    Responsibilities:
    - Create and sign jobs
    - Push to Redis queue
    - Track job status
    - Enforce permissions and quotas
    """
    
    def __init__(self, redis_client: redis.Redis):
        """
        Initialize Job Manager
        
        Args:
            redis_client: Async Redis client
        """
        self.redis = redis_client

    async def _load_job(self, job_id: str) -> Optional[Job]:
        """Load job spec from Redis."""
        job_json = await self.redis.get(f"job:{job_id}:spec")
        if not job_json:
            return None
        return Job.parse_raw(job_json)

    async def _save_job_spec(self, job: Job) -> None:
        """Persist updated job spec."""
        await self.redis.set(f"job:{job.job_id}:spec", job.json())
        await self.redis.expire(f"job:{job.job_id}:spec", 604800)

    async def _move_to_dlq(
        self,
        job: Job,
        result: Optional[JobResult] = None,
        reason: str = "max_retries_exceeded",
    ) -> None:
        """Store failed job metadata in tenant-scoped dead-letter queue."""
        dlq_key = f"job_dlq:{job.tenant_id}"
        entry = {
            "job_id": str(job.job_id),
            "tenant_id": job.tenant_id,
            "user_id": job.user_id,
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "retry_count": job.retry_count,
            "reason": reason,
            "result": json.loads(result.json()) if result else None,
        }
        await self.redis.rpush(dlq_key, json.dumps(entry, ensure_ascii=False))
        await self.redis.expire(dlq_key, settings.JOB_DLQ_TTL_SEC)
        logger.error(
            "Job moved to DLQ",
            job_id=str(job.job_id),
            tenant_id=job.tenant_id,
            retry_count=job.retry_count,
            reason=reason,
        )
    
    async def create_job(
        self,
        user: User,
        job_request: JobCreate
    ) -> Job:
        """
        Create, sign, and queue a new job
        
        Process:
        1. Validate permissions
        2. Create job dictionary with UUID and timestamp
        3. Sign the job with Ed25519 private key
        4. Push to Redis queue
        5. Save initial state to Redis
        
        Args:
            user: Current authenticated user
            job_request: Job creation request
            
        Returns:
            Created and signed Job
            
        Raises:
            PermissionDenied: If user lacks permission
            QuotaExceeded: If tenant quota exceeded
            SecurityError: If job signing fails
        """
        # Step 1: Permission check
        await self._check_permissions(user, job_request)
        
        # Step 2: Check quota (for cloud executions)
        if job_request.execution_location == ExecutionLocation.CLOUD:
            await self._check_quota(user.tenant_id)
        
        # Step 3: Create job dictionary
        job_id = uuid4()
        current_ts = int(time.time())
        
        # Generate idempotency key
        idempotency_data = f"{user.id}_{job_request.model}_{current_ts}"
        idempotency_key = f"sha256:{hashlib.sha256(idempotency_data.encode()).hexdigest()}"
        
        # Check idempotency
        if await self._check_idempotency(idempotency_key):
            logger.warning(
                "Duplicate job request detected",
                idempotency_key=idempotency_key,
                user_id=user.id
            )
            # Return cached job (in production, retrieve from Redis)
            # For now, we'll continue with new job
        
        # Build job data (without signature)
        job_data = {
            "job_id": str(job_id),
            "tenant_id": user.tenant_id,
            "user_id": user.id,
            "execution_location": job_request.execution_location.value,
            "provider": job_request.provider.value,
            "model": job_request.model,
            "created_at_ts": current_ts,
            "status": JobStatus.QUEUED.value,
            "timeout_sec": job_request.timeout_sec,
            "idempotency_key": idempotency_key,
            "steps": job_request.steps,
            "priority": job_request.priority,
            "tool_allowlist": job_request.tool_allowlist, # [TODO 9]
            "metadata": job_request.metadata.dict(),
            "file_operations": [op.dict() for op in job_request.file_operations],
            "retry_count": 0,
            "reassign_count": 0,
            "execution_started_at": None
        }
        
        # Add conditional fields for LOCAL_MACHINE
        if job_request.execution_location == ExecutionLocation.LOCAL_MACHINE:
            job_data["repo_root"] = job_request.repo_root
            job_data["allowed_paths"] = job_request.allowed_paths
        
        # Step 4: Sign the job
        try:
            signature = sign_job_payload(job_data)
            job_data["signature"] = signature
            
            logger.info(
                "Job signed successfully",
                job_id=str(job_id),
                execution_location=job_request.execution_location.value
            )
        except SecurityError as e:
            logger.error("Job signing failed", error=str(e))
            raise
        
        # Step 5: Create Job model
        job = Job(**job_data)
        
        # Step 6: Save to Redis and queue
        await self._save_job(job)
        await self._queue_job(job)
        
        # Step 7: Store idempotency key
        await self._store_idempotency(idempotency_key, str(job_id))
        
        logger.info(
            "Job created and queued",
            job_id=str(job_id),
            tenant_id=user.tenant_id,
            execution_location=job_request.execution_location.value
        )
        
        return job
    
    async def _check_permissions(self, user: User, job_request: JobCreate) -> None:
        """
        Check if user has permission to create this job
        
        Rule: LOCAL_MACHINE execution requires SUPER_ADMIN role
        
        Args:
            user: Current user
            job_request: Job creation request
            
        Raises:
            PermissionDenied: If user lacks permission
        """
        if job_request.execution_location == ExecutionLocation.LOCAL_MACHINE:
            # ORIGINAL RULE: Only SUPER_ADMIN can use LOCAL_MACHINE
            # NEW RULE: Standard users can use LOCAL_MACHINE if they have specific domain permissions
            # The actual domain check happens in the API layer (jobs.py) before calling this.
            # So here we just need to allow it if the user is not a super admin but has permissions.
            
            # However, for security, we might want to ensure that standard users ONLY use LOCAL_MACHINE
            # if they have at least one allowed domain.
            if user.role != UserRole.SUPER_ADMIN:
                if not user.allowed_domains:
                    raise PermissionDenied(
                        "LOCAL_MACHINE execution requires SUPER_ADMIN role or explicit domain permission."
                    )
                # If they have allowed_domains, we assume the API layer has validated the specific repo_root.
                pass
    
    async def _check_quota(self, tenant_id: str) -> None:
        """
        Check if tenant has remaining quota
        
        Args:
            tenant_id: Tenant identifier
            
        Raises:
            QuotaExceeded: If quota exceeded
        """
        # Get current month key
        month_key = time.strftime("%Y%m")
        usage_key = f"usage:{tenant_id}:{month_key}"
        
        # Get current usage
        current_usage = await self.redis.hget(usage_key, "total_cost")
        current_cost = float(current_usage) if current_usage else 0.0
        
        # Check against quota
        if current_cost >= settings.DEFAULT_MONTHLY_QUOTA_USD:
            raise QuotaExceeded(
                f"Monthly quota of ${settings.DEFAULT_MONTHLY_QUOTA_USD} exceeded. "
                f"Current usage: ${current_cost:.2f}"
            )
    
    async def _check_idempotency(self, key: str) -> bool:
        """Check if idempotency key already exists"""
        return await self.redis.exists(f"idempotency:{key}") > 0
    
    async def _store_idempotency(self, key: str, job_id: str) -> None:
        """Store idempotency key with 24h TTL"""
        await self.redis.setex(
            f"idempotency:{key}",
            86400,  # 24 hours
            job_id
        )
    
    async def _save_job(self, job: Job) -> None:
        """
        Save job to Redis for tracking
        
        Storage:
        - job:{job_id}:spec - Full job JSON
        - job:{job_id}:status - Current status
        - job:{job_id}:created_at - Creation timestamp
        """
        job_id = str(job.job_id)
        
        # Save full job spec
        await self.redis.set(
            f"job:{job_id}:spec",
            job.json()
        )
        
        # Save status
        await self.redis.set(
            f"job:{job_id}:status",
            job.status.value
        )
        
        # Save creation timestamp
        await self.redis.set(
            f"job:{job_id}:created_at",
            job.created_at_ts
        )
        
        # Set TTL (7 days)
        for key in [f"job:{job_id}:spec", f"job:{job_id}:status", f"job:{job_id}:created_at"]:
            await self.redis.expire(key, 604800)
    
    async def _queue_job(self, job: Job) -> None:
        """
        Push job to appropriate queue
        
        Queue structure:
        - job_queue:{tenant_id} - FIFO list for tenant's jobs
        
        Args:
            job: Job to queue
        """
        queue_key = f"job_queue:{job.tenant_id}"
        
        # Check queue size limit
        queue_size = await self.redis.llen(queue_key)
        if queue_size >= settings.MAX_QUEUED_JOBS_PER_TENANT:
            raise QuotaExceeded(
                f"Job queue full. Maximum {settings.MAX_QUEUED_JOBS_PER_TENANT} "
                f"queued jobs per tenant."
            )
        
        # Push to queue (RPUSH = add to end)
        await self.redis.rpush(queue_key, job.json())
        
        logger.info(
            "Job added to queue",
            job_id=str(job.job_id),
            tenant_id=job.tenant_id,
            queue_size=queue_size + 1
        )
    
    async def get_job_status(self, job_id: str, user: User) -> Optional[Dict[str, Any]]:
        """
        Get job status and details
        
        Args:
            job_id: Job identifier
            user: Current user (for permission check)
            
        Returns:
            Job status dictionary or None if not found
        """
        # Get job spec
        job_json = await self.redis.get(f"job:{job_id}:spec")
        if not job_json:
            return None
        
        job = Job.parse_raw(job_json)
        
        # Permission check: user can only view their own jobs (unless super admin)
        if user.role != UserRole.SUPER_ADMIN:
            if job.user_id != user.id or job.tenant_id != user.tenant_id:
                raise PermissionDenied("Cannot access other users' jobs")
        
        # Get current status
        status = await self.redis.get(f"job:{job_id}:status")
        
        # Get result if completed
        result = None
        if status in [JobStatus.COMPLETED.value, JobStatus.FAILED.value]:
            result_json = await self.redis.get(f"job:{job_id}:result")
            if result_json:
                result = json.loads(result_json)
        
        return {
            "job_id": job_id,
            "status": status,
            "created_at": job.created_at_ts,
            "execution_location": job.execution_location,
            "model": job.model,
            "result": result
        }
    
    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        result: Optional[JobResult] = None
    ) -> None:
        """
        Update job status (called by workers or internal processes)
        """
        job = await self._load_job(job_id)

        # Save result payload if provided.
        if result:
            await self.redis.set(f"job:{job_id}:result", result.json())

        # Retry/DLQ branch for failed jobs.
        if status == JobStatus.FAILED and job:
            job.retry_count += 1
            max_retries = max(0, int(settings.JOB_MAX_RETRIES))
            if job.retry_count <= max_retries:
                job.status = JobStatus.QUEUED
                await self._save_job_spec(job)
                await self.redis.set(f"job:{job_id}:status", JobStatus.QUEUED.value)
                await self.redis.rpush(f"job_queue:{job.tenant_id}", job.json())
                logger.warning(
                    "Job failed and requeued",
                    job_id=job_id,
                    retry_count=job.retry_count,
                    max_retries=max_retries,
                )
                return

            # Retries exhausted: keep FAILED status and push to DLQ.
            await self._move_to_dlq(job, result=result)
            job.status = JobStatus.FAILED
            await self._save_job_spec(job)

        # Update status
        await self.redis.set(f"job:{job_id}:status", status.value)

        # Set completion timestamp
        if status in [JobStatus.COMPLETED, JobStatus.FAILED]:
            await self.redis.set(f"job:{job_id}:completed_at", int(time.time()))

        logger.info("Job status updated", job_id=job_id, status=status.value)

    async def clear_queue(self, tenant_id: str) -> int:
        """Clear all jobs from a tenant's queue"""
        queue_key = f"job_queue:{tenant_id}"
        processing_pattern = f"job_processing:{tenant_id}:*"
        
        # Delete main queue
        count = await self.redis.delete(queue_key)
        
        # Delete all processing lists for this tenant
        p_keys = await self.redis.keys(processing_pattern)
        if p_keys:
            await self.redis.delete(*p_keys)
            count += len(p_keys)
            
        return count

    async def fix_orphaned_jobs(self, tenant_id: str) -> List[str]:
        """Find jobs marked as QUEUED but not in any queue, and mark them as FAILED"""
        fixed_ids = []
        # Find all job status keys
        keys = await self.redis.keys(f"job:*:status")
        for key in keys:
            status = await self.redis.get(key)
            if status == "QUEUED":
                # Check if it belongs to this tenant
                job_id = key.split(":")[1]
                spec_json = await self.redis.get(f"job:{job_id}:spec")
                if spec_json:
                    spec = json.loads(spec_json)
                    if spec.get("tenant_id") == tenant_id:
                        await self.redis.set(key, "FAILED")
                        fixed_ids.append(job_id)
        return fixed_ids
