import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import httpx
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from redis.exceptions import RedisError

from app.config import settings
from app.database import create_db_pool
from app.dependencies import DbPool
from app.routers import auth, booking, events, places, setup, tickets, venues, websockets
from app.seat_realtime import SeatRealtimeHub, expire_holds_loop


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.db_pool = await create_db_pool()
    app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
    if settings.redis_url:
        app.state.redis = redis.Redis.from_url(
            settings.redis_url.get_secret_value(),
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True,
        )
    else:
        app.state.redis = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=(
                settings.redis_password.get_secret_value() if settings.redis_password else None
            ),
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True,
        )
    app.state.realtime_hub = SeatRealtimeHub(app.state.redis)

    background_tasks = [
        asyncio.create_task(app.state.realtime_hub.run()),
        asyncio.create_task(
            expire_holds_loop(
                app.state.db_pool,
                app.state.realtime_hub,
                settings.hold_sweep_interval_seconds,
            )
        ),
    ]

    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        await app.state.redis.aclose()
        await app.state.http_client.aclose()
        await app.state.db_pool.close()


app = FastAPI(title="Event & Meetup Hub API", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (auth, venues, places, events, setup, booking, tickets, websockets):
    app.include_router(module.router, prefix=settings.api_prefix)


@app.get("/health", tags=["health"])
async def health(request: Request, pool: DbPool) -> dict[str, str]:
    try:
        await pool.fetchval("SELECT 1")
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from exc

    try:
        await asyncio.wait_for(request.app.state.redis.ping(), timeout=2)
        redis_status = "ok"
    except (RedisError, OSError, TimeoutError):
        redis_status = "unavailable"

    return {"status": "ok", "database": "ok", "redis": redis_status}