"""
Dependencies for FastAPI endpoints
Provides reusable dependency injection functions
"""
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.security import decode_access_token, validate_worker_token
from app.models.schemas import User, UserRole, TokenData


# Security scheme
security = HTTPBearer()


def forbidden_role_detail(message: str, required: str | None = None) -> dict:
    payload = {
        "error_code": "FORBIDDEN_ROLE",
        "message": message,
    }
    if required is not None:
        payload["required"] = required
    return payload


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> User:
    """
    Dependency to get current authenticated user from JWT token
    
    Args:
        credentials: HTTP Bearer token
        
    Returns:
        Current user
        
    Raises:
        HTTPException: If token is invalid or user not found
    """
    token = credentials.credentials
    
    # Decode token
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Extract user data
    user_id: str = payload.get("sub")
    tenant_id: str = payload.get("tenant_id")
    role_str: str = payload.get("role")
    
    if user_id is None or tenant_id is None or role_str is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    try:
        role = UserRole(role_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid role in token",
        )
    
    # Fetch user from Real DB
    from app.core.database import AsyncSessionLocal, UserModel
    from sqlalchemy import select
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(UserModel).where(UserModel.id == user_id))
        user_model = result.scalar_one_or_none()
            
    if not user_model:
        # Fallback (shouldn't happen if token is valid)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    else:
        # Create User object from DB data
        user = User(
            id=user_model.id,
            username=user_model.username,
            tenant_id=user_model.tenant_id,
            role=UserRole(user_model.role),
            is_active=bool(user_model.is_active)
        )
    
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Dependency to get current active user
    
    Args:
        current_user: Current user from token
        
    Returns:
        Active user
        
    Raises:
        HTTPException: If user is inactive
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=forbidden_role_detail("비활성 사용자입니다.", "tenant_admin 또는 super_admin"),
        )
    return current_user


async def get_current_super_admin(
    current_user: User = Depends(get_current_active_user)
) -> User:
    """
    Dependency to require super admin role
    
    Args:
        current_user: Current active user
        
    Returns:
        Super admin user
        
    Raises:
        HTTPException: If user is not super admin
    """
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=forbidden_role_detail("상담사 전용 권한이 필요합니다.", "super_admin"),
        )
    return current_user


async def verify_worker_credentials(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    """
    Dependency to verify worker token
    
    Args:
        credentials: HTTP Bearer token
        
    Returns:
        Worker token
        
    Raises:
        HTTPException: If token is invalid
    """
    token = credentials.credentials
    
    if not validate_worker_token(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid worker token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return token
