from __future__ import annotations

import asyncio

from app.db.base import engine
from app.db.models import Base


async def main() -> None:
    print("Creating tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
