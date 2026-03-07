# -*- coding: utf-8 -*-
"""
File Management Endpoints
Handles file uploads and triggers knowledge ingestion
"""
import hashlib
import json
import sys
import uuid
import os
from pathlib import Path
from typing import Dict, List, Optional
from sqlalchemy import select

# [UTF-8] Force stdout/stderr to UTF-8
if sys.stdout.encoding is None or sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, status, Form
from structlog import get_logger

from app.core.config import settings
from app.api.dependencies import get_current_user
from app.models.schemas import User
from app.services.knowledge_service import knowledge_queue
from app.services.document_parser_service import document_parser_service
from app.core.database import _normalize_project_id, AsyncSessionLocal, MessageModel
from datetime import datetime, timezone

logger = get_logger(__name__)

router = APIRouter(prefix="/files", tags=["files"])

# Ensure upload directory exists
UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

def _build_error_code_message(reason_code: str, message: str) -> str:
    return f"{reason_code}: {message}"


def _build_folder_result(
    filename: str,
    status: str,
    *,
    reason: str = "",
    detail: str = "",
) -> Dict[str, str]:
    payload: Dict[str, str] = {"filename": filename, "status": status}
    if reason:
        payload["reason"] = reason
    if detail:
        payload["detail"] = detail
    return payload


def calculate_file_hash(file_content: bytes) -> str:
    """Calculate SHA256 hash of file content for deduplication."""
    return hashlib.sha256(file_content).hexdigest()

def _project_id_filter(project_id: str):
    project_uuid = _normalize_project_id(project_id)
    if project_uuid is None:
        return MessageModel.project_id.is_(None)
    return MessageModel.project_id == project_uuid


def _normalize_metadata(metadata: object) -> dict:
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def check_duplicate_file(session, project_id: str, file_hash: str) -> Optional[MessageModel]:
    """Check if file with same hash already exists in project."""
    stmt = (
        select(MessageModel)
        .where(_project_id_filter(project_id))
        .where(MessageModel.metadata_json.is_not(None))
        .order_by(MessageModel.timestamp.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    for message in rows:
        metadata = _normalize_metadata(message.metadata_json)
        if metadata.get("type") == "file_upload" and metadata.get("file_hash") == file_hash:
            return message
    return None

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    project_id: str = Form("system-master"),
    current_user: User = Depends(get_current_user)
):
    """
    Upload a file and trigger knowledge ingestion with Deduplication.
    """
    try:
        # 1. Read content and Calculate Hash
        content = await file.read()
        size = len(content)
        file.file.seek(0) # Reset cursor
        
        if size > settings.MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=_build_error_code_message(
                    "FILE_TOO_LARGE",
                    f"File too large. Max size: {settings.MAX_FILE_SIZE_BYTES} bytes"
                )
            )
            
        file_hash = calculate_file_hash(content)
        ext = os.path.splitext(file.filename)[1].lower()
        if not document_parser_service.is_supported_extension(ext):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_build_error_code_message(
                    "UNSUPPORTED_EXTENSION",
                    f"Unsupported file extension: {ext or 'unknown'}"
                )
            )
        
        # 2. Check Deduplication
        async with AsyncSessionLocal() as session:
            # Check if this hash exists in recent uploads for this project
            existing = await check_duplicate_file(session, project_id, file_hash)
            
            if existing:
                logger.info("Duplicate file detected, skipping upload", filename=file.filename, hash=file_hash)
                return {
                    "filename": file.filename,
                    "status": "skipped",
                    "reason": "duplicate",
                    "message_id": str(existing.message_id)
                }

        # 3. Save file
        file_id = str(uuid.uuid4())
        safe_filename = f"{file_id}{ext}"
        file_path = UPLOAD_DIR / safe_filename
        
        with open(file_path, "wb") as f:
            f.write(content)
            
        logger.info("File uploaded", filename=file.filename, size=size, user_id=current_user.id)
        
        # 4. Create Message for Ingestion
        content_preview = f"[File Upload] {file.filename} ({size} bytes)"
        full_text = ""
        
        # Extract text using document parser service
        parse_failed = False
        try:
            full_text = document_parser_service._parse_file(str(file_path), ext)
            if full_text:
                content_preview += f"\n\n--- FILE CONTENT ---\n{full_text[:2000]}... (truncated)"
        except HTTPException as e:
            parse_failed = True
            logger.warning("Failed to parse file content", error=str(e.detail))
            content_preview += f"\n(Parser fallback: {e.detail})"
        except Exception as e:
            parse_failed = True
            logger.warning("Failed to parse file content", error=str(e))
            content_preview += f"\n(Parser fallback: {str(e)})"
        
        # 5. Save to DB
        msg_id = str(uuid.uuid4())
        async with AsyncSessionLocal() as session:
            msg = MessageModel(
                message_id=msg_id,
                project_id=_normalize_project_id(project_id),
                sender_role="user", 
                content=content_preview,
                timestamp=datetime.now(timezone.utc),
                metadata_json={
                    "type": "file_upload",
                    "filename": file.filename,
                    "file_path": str(file_path),
                    "file_size": size,
                    "file_hash": file_hash, # [Deduplication Key]
                    "user_id": current_user.id
                }
            )
            session.add(msg)
            await session.commit()
            
        # 6. Trigger Knowledge Ingestion
        if parse_failed:
            logger.warning(
                "Upload succeeded with parser fallback",
                filename=file.filename,
                project_id=project_id,
            )
            return {
                "filename": file.filename,
                "file_id": file_id,
                "message_id": msg_id,
                "status": "saved_only",
                "reason": "parser_fallback",
            }

        if full_text:
            knowledge_queue.put_nowait(msg_id)
            logger.info("File queued for ingestion", message_id=msg_id, project_id=project_id, user_id=current_user.id)
            
        return {
            "filename": file.filename,
            "file_id": file_id,
            "message_id": msg_id,
            "status": "queued" if full_text else "saved_only",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Upload failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Upload failed: {str(e)}"
        )

@router.post("/upload-folder")
async def upload_folder(
    files: List[UploadFile] = File(...),
    project_id: str = Form("system-master"),
    current_user: User = Depends(get_current_user)
):
    """
    Upload multiple files (Folder) with Deduplication.
    """
    results = []
    
    for file in files:
        try:
            # Reuse logic from upload_file but simplified for batch
            content = await file.read()
            size = len(content)
            
            if size > settings.MAX_FILE_SIZE_BYTES:
                results.append(
                    _build_folder_result(
                        file.filename,
                        "failed",
                        reason="too_large",
                        detail=_build_error_code_message(
                            "FILE_TOO_LARGE",
                            f"File too large. Max size: {settings.MAX_FILE_SIZE_BYTES} bytes",
                        ),
                    )
                )
                continue
                
            file_hash = calculate_file_hash(content)
            ext = os.path.splitext(file.filename)[1].lower()
            if not document_parser_service.is_supported_extension(ext):
                results.append(
                    _build_folder_result(
                        file.filename,
                        "failed",
                        reason="unsupported_type",
                        detail=_build_error_code_message(
                            "UNSUPPORTED_EXTENSION",
                            f"Unsupported file extension: {ext or 'unknown'}",
                        ),
                    )
                )
                continue
            
            # Check Dedupe
            async with AsyncSessionLocal() as session:
                existing = await check_duplicate_file(session, project_id, file_hash)
                
                if existing:
                    results.append(
                        _build_folder_result(
                            file.filename,
                            "skipped",
                            reason="duplicate",
                        )
                    )
                    continue

            # Save
            file_id = str(uuid.uuid4())
            safe_filename = f"{file_id}{ext}"
            file_path = UPLOAD_DIR / safe_filename
            
            with open(file_path, "wb") as f:
                f.write(content)
                
            # DB & Queue
            msg_id = str(uuid.uuid4())
            content_preview = f"[Folder Upload] {file.filename}"
            full_text = ""
            parse_failed = False
            
            try:
                full_text = document_parser_service._parse_file(str(file_path), ext)
                if full_text:
                    content_preview += f"\n\n--- FILE CONTENT ---\n{full_text[:2000]}... (truncated)"
            except Exception as e:
                 logger.warning("Failed to parse file content", error=str(e))
                 parse_failed = True
            
            async with AsyncSessionLocal() as session:
                msg = MessageModel(
                    message_id=msg_id,
                    project_id=_normalize_project_id(project_id),
                    sender_role="user", 
                    content=content_preview,
                    timestamp=datetime.now(timezone.utc),
                    metadata_json={
                        "type": "file_upload",
                        "filename": file.filename,
                        "file_path": str(file_path),
                        "file_size": size,
                        "file_hash": file_hash,
                        "user_id": current_user.id
                    }
                )
                session.add(msg)
                await session.commit()
                
            if full_text:
                knowledge_queue.put_nowait(msg_id)
                
            if parse_failed:
                results.append(
                    _build_folder_result(
                        file.filename,
                        "saved_only",
                        reason="parser_fallback",
                    )
                )
            else:
                results.append(_build_folder_result(file.filename, "queued"))
            
        except Exception as e:
            logger.error(f"Error processing file {file.filename}", error=str(e))
            results.append(
                _build_folder_result(
                    file.filename,
                    "failed",
                    reason="unknown",
                    detail=str(e),
                )
            )
            
    return {"results": results, "total": len(files), "processed": len([r for r in results if r['status'] == 'queued'])}


@router.post("/upload-batch")
async def upload_batch(
    files: List[UploadFile] = File(...),
    project_id: str = Form("system-master"),
    current_user: User = Depends(get_current_user)
):
    """
    Alias endpoint for batch file upload.
    Supports the same behavior as /upload-folder.
    """
    return await upload_folder(files=files, project_id=project_id, current_user=current_user)
