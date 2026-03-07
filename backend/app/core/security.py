# -*- coding: utf-8 -*-
"""
Security utilities for AIBizPlan
"""
import json
import sys

# [UTF-8] Force stdout/stderr to UTF-8
if sys.stdout.encoding is None or sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding is None or sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import base64

from jose import JWTError, jwt
from passlib.context import CryptContext
try:
    import bcrypt as _bcrypt
except Exception:  # pragma: no cover
    _bcrypt = None
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

from app.core.config import settings


# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ============================================
# Password Hashing
# ============================================
def _validate_bcrypt_password(password: str) -> str:
    """
    bcrypt supports at most 72 bytes in the UTF-8 encoded password.
    """
    if not isinstance(password, str):
        raise ValueError("Password must be a string")

    if len(password.encode("utf-8")) > 72:
        raise ValueError(
            "Password is too long for bcrypt. "
            "Please use 72 bytes or less (ASCII 기준으로 약 72자 이하)."
        )

    return password


def _hash_with_bcrypt(password: str) -> str:
    if _bcrypt is None:
        raise RuntimeError("bcrypt module is unavailable")

    pwd = _validate_bcrypt_password(password).encode("utf-8")
    hashed = _bcrypt.hashpw(pwd, _bcrypt.gensalt(rounds=12))
    return hashed.decode("utf-8")


def _verify_with_bcrypt(password: str, hashed_password: str) -> bool:
    if _bcrypt is None:
        return False

    pwd = _validate_bcrypt_password(password).encode("utf-8")
    return _bcrypt.checkpw(pwd, hashed_password.encode("utf-8"))


def get_password_hash(password: str) -> str:
    """
    Hash a password using bcrypt
    
    Args:
        password: Plain text password
        
    Returns:
        Hashed password
    """
    password = _validate_bcrypt_password(password)

    # Primary path: direct bcrypt implementation for deterministic env compatibility
    # (passlib+bcrypt backend version mismatch can fail depending on installed wheels).
    try:
        return _hash_with_bcrypt(password)
    except Exception:
        # Fallback to passlib (legacy behavior when bcrypt backend is healthy)
        return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against its hash
    
    Args:
        plain_password: Plain text password to verify
        hashed_password: Hashed password to compare against
        
    Returns:
        True if password matches, False otherwise
    """
    plain_password = _validate_bcrypt_password(plain_password)

    if hashed_password.startswith("$2"):
        return _verify_with_bcrypt(plain_password, hashed_password)

    # Fallback path for non-bcrypt legacy hashes.
    return pwd_context.verify(plain_password, hashed_password)


# ============================================
# JWT Token Management
# ============================================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token
    
    Args:
        data: Payload data to encode (should include 'sub', 'tenant_id', 'role')
        expires_delta: Optional custom expiration time
        
    Returns:
        Encoded JWT token
    """
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    
    to_encode.update({"exp": expire})
    
    encoded_jwt = jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM
    )
    
    return encoded_jwt


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Decode and validate a JWT access token
    
    Args:
        token: JWT token to decode
        
    Returns:
        Decoded payload if valid, None otherwise
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except JWTError:
        return None


# ============================================
# Ed25519 Job Signing (CRITICAL SECURITY)
# ============================================

class SecurityError(Exception):
    """Raised when security validation fails"""
    pass


def _extract_signable_job_payload(job_data: dict) -> dict:
    payload = job_data.copy()
    if hasattr(payload, "dict"):
        payload = payload.dict()
    return payload


def sign_job_payload(job_data: dict) -> str:
    """
    Sign a job payload with Backend's Ed25519 private key
    """
    try:
        # 1. Load private key
        private_key_pem = settings.JOB_SIGNING_PRIVATE_KEY
        if not private_key_pem:
            raise SecurityError("JOB_SIGNING_PRIVATE_KEY not configured")
        private_key_pem = private_key_pem.replace('\\n', '\n')
        
        private_key = serialization.load_pem_private_key(
            private_key_pem.encode(),
            password=None
        )
        
        # 2. Create canonical JSON
        # UUID나 Enum 객체가 섞여있을 수 있으므로 default=str로 처리하되, 
        # 구조를 변형시키지 않기 위해 단순화합니다.
        payload_to_sign = _extract_signable_job_payload(job_data)
        # Pydantic 모델인 경우 dict로 변환 (이미 dict라면 그대로)
        if hasattr(payload_to_sign, "dict"):
            payload_to_sign = payload_to_sign.dict()

        canonical_json = json.dumps(payload_to_sign, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
        message = canonical_json.encode('utf-8')
        
        print(f"DEBUG: [Sign] Canonical Length: {len(message)}", flush=True)
        
        print(f"DEBUG: [Sign] Canonical Length: {len(message)}")
        
        # 3. Sign
        signature_bytes = private_key.sign(message)
        return f"base64:{base64.b64encode(signature_bytes).decode('ascii')}"
        
    except Exception as e:
        raise SecurityError(f"Job signing failed: {str(e)}")


def verify_job_signature(job_dict: dict, public_key_pem: str) -> bool:
    """
    Verify job signature with Backend's public key
    
    This function is primarily for testing. In production, only Local Workers
    verify signatures.
    
    Args:
        job_dict: Complete job including 'signature' field
        public_key_pem: Ed25519 public key in PEM format
        
    Returns:
        True if valid
        
    Raises:
        SecurityError: If signature is invalid
        
    Implementation follows JOB_AND_SECURITY.md Section 3.3
    """
    try:
        # 1. Extract signature
        job_copy = job_dict.copy()
        signature_field = job_copy.pop('signature', None)
        
        if not signature_field:
            raise SecurityError("Job missing signature field")
        
        if not signature_field.startswith('base64:'):
            raise SecurityError("Invalid signature format (must start with 'base64:')")
        
        signature_b64 = signature_field.replace('base64:', '')
        signature_bytes = base64.b64decode(signature_b64)
        
        # 2. Load public key
        public_key = serialization.load_pem_public_key(public_key_pem.encode())
        
        if not isinstance(public_key, ed25519.Ed25519PublicKey):
            raise SecurityError("Public key is not Ed25519 format")
        
        # 3. Recreate canonical message
        payload_to_verify = _extract_signable_job_payload(job_copy)
        canonical_json = json.dumps(payload_to_verify, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
        message = canonical_json.encode('utf-8')
        
        # 4. Verify signature
        try:
            public_key.verify(signature_bytes, message)
            return True
        except InvalidSignature:
            raise SecurityError(
                f"Job signature verification failed for job_id={job_copy.get('job_id')}"
            )
            
    except Exception as e:
        if isinstance(e, SecurityError):
            raise
        raise SecurityError(f"Signature verification failed: {str(e)}")


# ============================================
# Worker Token Validation
# ============================================

def validate_worker_token(token: str) -> bool:
    """
    Validate a worker token
    
    In production, this should check against a database of issued tokens.
    For now, we just verify the format.
    
    Args:
        token: Worker token (format: sk_worker_...)
        
    Returns:
        True if valid format
    """
    if not token:
        return False
    
    # Basic format validation
    if not token.startswith("sk_worker_"):
        return False
    
    # In production: Check against Redis/DB
    # await redis.exists(f"worker_token:{token}")
    
    return True
