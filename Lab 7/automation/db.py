"""
Lab 7 — Shared DB helpers
Redis (cache/sessions) + PostgreSQL (persistent storage)
"""
import os
import json
import logging
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

import redis.asyncio as aioredis
import asyncpg

logger = logging.getLogger(__name__)

REDIS_URL    = os.getenv("REDIS_URL",    "redis://192.168.30.20:6379/0")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://lab7:lab7pass@192.168.30.30:5432/lab7db")

_redis_pool:    Optional[aioredis.Redis]       = None
_pg_pool:       Optional[asyncpg.Pool]         = None


# ── Redis ─────────────────────────────────────────────────────────────────────
async def get_redis() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = await aioredis.from_url(REDIS_URL, decode_responses=True)
        logger.info(f"Redis connected: {REDIS_URL}")
    return _redis_pool


async def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    r = await get_redis()
    await r.setex(key, ttl, json.dumps(value))


async def cache_get(key: str) -> Optional[Any]:
    r = await get_redis()
    raw = await r.get(key)
    return json.loads(raw) if raw else None


async def cache_delete(key: str) -> None:
    r = await get_redis()
    await r.delete(key)


# ── PostgreSQL ────────────────────────────────────────────────────────────────
async def get_pg() -> asyncpg.Pool:
    global _pg_pool
    if _pg_pool is None:
        _pg_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
        logger.info(f"PostgreSQL connected: {DATABASE_URL}")
    return _pg_pool


# ── Upload operations ─────────────────────────────────────────────────────────
async def db_save_upload(metadata: Dict[str, Any]) -> None:
    pool = await get_pg()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO uploads (file_id, filename, size, mime_type, status, file_path, request_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (file_id) DO UPDATE
              SET status = EXCLUDED.status
        """,
            uuid.UUID(metadata["file_id"]),
            metadata["filename"],
            metadata["size"],
            metadata.get("mime_type", "application/octet-stream"),
            metadata.get("status", "uploaded"),
            metadata.get("file_path"),
            metadata.get("request_id"),
        )
    await cache_set(f"upload:{metadata['file_id']}", metadata)


async def db_get_upload(file_id: str) -> Optional[Dict[str, Any]]:
    cached = await cache_get(f"upload:{file_id}")
    if cached:
        return cached
    pool = await get_pg()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM uploads WHERE file_id = $1", uuid.UUID(file_id)
        )
    if not row:
        return None
    result = dict(row)
    result["file_id"]          = str(result["file_id"])
    result["upload_timestamp"] = result["upload_timestamp"].isoformat()
    await cache_set(f"upload:{file_id}", result)
    return result


async def db_delete_upload(file_id: str) -> bool:
    pool = await get_pg()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM uploads WHERE file_id = $1", uuid.UUID(file_id)
        )
    await cache_delete(f"upload:{file_id}")
    return result == "DELETE 1"


# ── Processing operations ─────────────────────────────────────────────────────
async def db_save_processing(job: Dict[str, Any]) -> None:
    pool = await get_pg()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO processing_jobs
              (job_id, file_id, operation, status, output_file, processing_time, request_id, completed_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
            uuid.UUID(job["job_id"]),
            uuid.UUID(job["file_id"]),
            job["operation"],
            job["status"],
            job.get("output_file"),
            job.get("processing_time"),
            job.get("request_id"),
            datetime.now(),
        )
    await cache_set(f"processing:{job['file_id']}:latest", job, ttl=600)


# ── AI analysis operations ────────────────────────────────────────────────────
async def db_save_analysis(analysis: Dict[str, Any]) -> None:
    pool = await get_pg()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ai_analyses
              (analysis_id, file_id, analysis_type, confidence, model_version, results, request_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
            uuid.UUID(analysis["analysis_id"]),
            uuid.UUID(analysis["file_id"]),
            analysis["analysis_type"],
            analysis["confidence"],
            analysis.get("model_version"),
            json.dumps(analysis.get("results", {})),
            analysis.get("request_id"),
        )


async def db_get_analysis_history(file_id: str) -> list:
    pool = await get_pg()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM ai_analyses WHERE file_id = $1 ORDER BY created_at DESC LIMIT 10",
            uuid.UUID(file_id)
        )
    return [
        {**dict(r), "file_id": str(r["file_id"]), "analysis_id": str(r["analysis_id"]),
         "created_at": r["created_at"].isoformat()}
        for r in rows
    ]


# ── Workflow operations ───────────────────────────────────────────────────────
async def db_save_workflow(workflow: Dict[str, Any]) -> None:
    pool = await get_pg()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO workflows
              (workflow_id, file_id, request_id, upload_status, processing_status,
               ai_analysis_status, total_time)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
            workflow["workflow_id"],
            uuid.UUID(workflow["file_id"]) if workflow.get("file_id") else None,
            workflow.get("request_id"),
            workflow.get("upload_status"),
            workflow.get("processing_status"),
            workflow.get("ai_analysis_status"),
            workflow.get("total_time"),
        )
    await cache_set(f"workflow:{workflow['workflow_id']}", workflow, ttl=3600)


async def close_connections() -> None:
    global _redis_pool, _pg_pool
    if _redis_pool:
        await _redis_pool.aclose()
    if _pg_pool:
        await _pg_pool.close()
