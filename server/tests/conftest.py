import asyncio
import sys

if sys.platform == "win32":  # psycopg async can't use the Proactor loop
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
