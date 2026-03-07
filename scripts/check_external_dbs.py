#!/usr/bin/env python3
"""
External DB connectivity check for app-server-only deployment.
Checks Redis, Postgres, and Neo4j connections using values from `.env`.
"""

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import redis.asyncio as redis
from dotenv import load_dotenv
from neo4j import AsyncGraphDatabase


ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT_DIR / ".env"
load_dotenv(ENV_FILE)


def _get_env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _is_loopback_url(url: str) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
        return host in {"localhost", "127.0.0.1", "::1"}
    except Exception:
        return False


async def check_redis() -> tuple[bool, str]:
    if os.getenv("STARTUP_WITHOUT_REDIS", "").lower() in {"1", "true", "yes", "on"}:
        return True, "SKIPPED (STARTUP_WITHOUT_REDIS=true)"

    redis_url = _get_env("REDIS_URL")
    if not redis_url:
        return False, "REDIS_URL missing"
    if _is_loopback_url(redis_url):
        return False, "REDIS_URL points to loopback (localhost/127.0.0.1)"

    client = redis.from_url(redis_url, decode_responses=True)
    try:
        pong = await asyncio.wait_for(client.ping(), timeout=5)
        if pong is True:
            return True, "redis ping ok"
        return False, f"unexpected ping response: {pong}"
    except Exception as exc:
        return False, f"redis connect failed: {exc}"
    finally:
        await client.close()


async def check_postgres() -> tuple[bool, str]:
    database_url = _get_env("DATABASE_URL")
    if not database_url:
        return False, "DATABASE_URL missing"
    if _is_loopback_url(database_url):
        return False, "DATABASE_URL points to loopback (localhost/127.0.0.1)"

    # asyncpg expects postgresql://... format.
    if database_url.startswith("postgresql+asyncpg://"):
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    try:
        conn = await asyncio.wait_for(asyncpg.connect(database_url), timeout=7)
        try:
            value = await conn.fetchval("SELECT 1")
            return (value == 1), "postgres SELECT 1 ok" if value == 1 else f"unexpected SELECT 1 result: {value}"
        finally:
            await conn.close()
    except Exception as exc:
        return False, f"postgres connect failed: {exc}"


async def check_neo4j() -> tuple[bool, str]:
    neo4j_uri = _get_env("NEO4J_URI") or _get_env("NEO4J_URL")
    neo4j_user = _get_env("NEO4J_USER")
    neo4j_password = _get_env("NEO4J_PASSWORD")

    if not neo4j_uri:
        return False, "NEO4J_URI/NEO4J_URL missing"
    if not neo4j_user or not neo4j_password:
        return False, "NEO4J_USER/NEO4J_PASSWORD missing"
    if _is_loopback_url(neo4j_uri):
        return False, "NEO4J_URI/NEO4J_URL points to loopback (localhost/127.0.0.1)"

    driver = AsyncGraphDatabase.driver(
        neo4j_uri,
        auth=(neo4j_user, neo4j_password),
        connection_timeout=5,
    )
    try:
        async with driver.session() as session:
            result = await asyncio.wait_for(session.run("RETURN 1 AS ok"), timeout=7)
            record = await result.single()
            ok = bool(record and record.get("ok") == 1)
            return ok, "neo4j RETURN 1 ok" if ok else "neo4j query returned unexpected value"
    except Exception as exc:
        return False, f"neo4j connect failed: {exc}"
    finally:
        await driver.close()


async def main() -> int:
    checks = [
        ("Redis", check_redis),
        ("Postgres", check_postgres),
        ("Neo4j", check_neo4j),
    ]

    print("External DB connectivity check")
    print(f"Using env file: {ENV_FILE}")
    print("-" * 70)

    success = True
    for name, check_fn in checks:
        ok, msg = await check_fn()
        status = "OK" if ok else "FAIL"
        print(f"[{status}] {name}: {msg}")
        if not ok:
            success = False

    print("-" * 70)
    if success:
        print("All external DB connections are healthy.")
        return 0
    print("One or more external DB checks failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
