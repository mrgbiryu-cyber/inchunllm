import asyncio
from app.core.database import engine
from sqlalchemy import text

async def main():
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema = 'buja_app' AND table_name='threads' AND column_name IN ('is_deleted','deleted_at') ORDER BY column_name"))).fetchall()
        print('columns=',rows)

asyncio.run(main())
