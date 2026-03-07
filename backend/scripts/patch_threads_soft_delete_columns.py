#!/usr/bin/env python3
import asyncio
from app.core.database import engine
from sqlalchemy import text


async def main() -> None:
    async with engine.begin() as conn:
        rows = await conn.execute(text("SELECT table_schema FROM information_schema.tables WHERE table_name = 'threads'"))
        schemas = [r[0] for r in rows.fetchall()]
        if not schemas:
            raise RuntimeError('threads 테이블이 존재하지 않습니다.')

        from app.core.config import settings
        target_schema = settings.DATABASE_SCHEMA or schemas[0]
        if target_schema not in schemas:
            target_schema = schemas[0]
            print(f'WARN: DATABASE_SCHEMA({settings.DATABASE_SCHEMA}) 없음. {target_schema} 사용')

        existing_cols = {
            r[0] for r in (
                await conn.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_schema = :schema AND table_name = 'threads'"),
                    {"schema": target_schema},
                )
            ).fetchall()
        }

        table_name = f'"{target_schema}"."threads"'
        if 'is_deleted' not in existing_cols:
            await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT FALSE"))
            print('ADD COLUMN is_deleted')
        if 'deleted_at' not in existing_cols:
            await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN deleted_at TIMESTAMP"))
            print('ADD COLUMN deleted_at')

    print('THREAD soft-delete 컬럼 보정 완료')


if __name__ == '__main__':
    asyncio.run(main())
