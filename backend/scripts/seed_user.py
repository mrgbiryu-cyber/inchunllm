#!/usr/bin/env python3
"""Seed 또는 운영 상의 사용자 계정을 안전하게 생성/갱신한다."""

from __future__ import annotations

import argparse
import asyncio

from app.core.security import get_password_hash
from app.core.database import AsyncSessionLocal, UserModel
from app.models.schemas import UserRole
from sqlalchemy import select


async def upsert_user(
    username: str,
    password: str,
    tenant_id: str,
    role: UserRole,
) -> str:
    user_id = f"user_{username}"

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(UserModel).where(UserModel.username == username))
        existing = result.scalar_one_or_none()

        hashed_password = get_password_hash(password)

        if existing:
            existing.hashed_password = hashed_password
            existing.tenant_id = tenant_id
            existing.role = role.value
            existing.is_active = 1
            await session.commit()
            return existing.id

        user = UserModel(
            id=user_id,
            username=username,
            hashed_password=hashed_password,
            tenant_id=tenant_id,
            role=role.value,
            is_active=1,
        )
        session.add(user)
        await session.commit()
        return user_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed or update 사용자 생성")
    parser.add_argument("--username", default="test", required=False)
    parser.add_argument("--password", default="!@ssw5740", required=False)
    parser.add_argument("--tenant-id", default="tenant_hyungnim", required=False)
    parser.add_argument(
        "--role",
        choices=[r.value for r in UserRole],
        default=UserRole.STANDARD_USER.value,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    role = UserRole(args.role)
    user_id = asyncio.run(
        upsert_user(
            username=args.username,
            password=args.password,
            tenant_id=args.tenant_id,
            role=role,
        )
    )
    print(f"[OK] user_id={user_id}, username={args.username}, role={role.value}")


if __name__ == "__main__":
    main()

