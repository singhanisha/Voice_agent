import asyncio
from db.db_manager import DatabaseManager
async def main():
    db = DatabaseManager()
    await db.initialize()
    agent = await db.get_agent("7cc26eed-9c86-4081-8c2f-099d1d2161d1")
    agent["assigned_email"] = "maniarasan@jobjockey.in"
    await db.update_agent("7cc26eed-9c86-4081-8c2f-099d1d2161d1", agent)
    print("Done")
asyncio.run(main())
