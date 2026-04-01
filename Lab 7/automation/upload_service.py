"""
Lab 7 — Upload Service (port 8000)
Storage: PostgreSQL (persistent) + Redis (cache)
"""
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime
import uuid, logging, os, time
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s | req=%(request_id)s | %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S')

class _RID(logging.Filter):
    def filter(self, r):
        if not hasattr(r, 'request_id'): r.request_id = '-'
        return True

logger = logging.getLogger(__name__)
logger.addFilter(_RID())
app = FastAPI(title="Upload Service — Lab 7", version="3.0.0")

UPLOAD_DIR = Path("mock_storage")
UPLOAD_DIR.mkdir(exist_ok=True)
MAX_FILE_SIZE       = int(os.getenv("MAX_FILE_SIZE_MB", 10)) * 1024 * 1024
ALLOWED_EXTENSIONS  = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".txt", ".docx", ".bin"}


@app.middleware("http")
async def rid_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    request.state.request_id = rid
    start = time.perf_counter()
    resp  = await call_next(request)
    ms    = (time.perf_counter() - start) * 1000
    resp.headers["X-Request-ID"]    = rid
    resp.headers["X-Response-Time"] = f"{ms:.1f}ms"
    logger.info(f"{request.method} {request.url.path} → {resp.status_code} ({ms:.1f}ms)",
                extra={"request_id": rid})
    return resp


@app.on_event("startup")
async def startup():
    from db import get_redis, get_pg
    for name, fn in [("Redis", get_redis), ("PostgreSQL", get_pg)]:
        try:
            await fn()
            logger.info(f"{name} connected", extra={"request_id": "startup"})
        except Exception as e:
            logger.warning(f"{name} unavailable: {e}", extra={"request_id": "startup"})


class UploadResponse(BaseModel):
    file_id: str; filename: str; size: int
    mime_type: str; status: str; upload_timestamp: str; storage: str = "postgresql"


@app.get("/health")
async def health():
    from db import get_redis, get_pg
    svc = {"redis": "unknown", "postgres": "unknown"}
    try:
        r = await get_redis(); await r.ping(); svc["redis"] = "healthy"
    except Exception: svc["redis"] = "unavailable"
    try:
        p = await get_pg()
        async with p.acquire() as c: await c.fetchval("SELECT 1")
        svc["postgres"] = "healthy"
    except Exception: svc["postgres"] = "unavailable"
    return {"status": "healthy", "service": "upload-service", "version": "3.0.0",
            "services": svc, "timestamp": datetime.now().isoformat()}


@app.post("/upload", response_model=UploadResponse)
async def upload_file(request: Request, file: UploadFile = File(...)):
    rid = getattr(request.state, "request_id", "-")
    if not file.filename:
        raise HTTPException(400, "No filename provided")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, f"Unsupported file type: {ext}")
    safe  = Path(file.filename).name.replace("..", "").replace("/", "")
    data  = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File too large (max {MAX_FILE_SIZE//1024//1024}MB)")

    fid  = str(uuid.uuid4())
    path = UPLOAD_DIR / f"{fid}_{safe}"
    path.write_bytes(data)

    meta = {"file_id": fid, "filename": safe, "size": len(data),
            "mime_type": file.content_type or "application/octet-stream",
            "status": "uploaded", "upload_timestamp": datetime.now().isoformat(),
            "file_path": str(path), "request_id": rid}
    try:
        from db import db_save_upload
        await db_save_upload(meta); storage = "postgresql+redis"
    except Exception as e:
        logger.warning(f"DB save failed: {e}", extra={"request_id": rid})
        storage = "filesystem"

    logger.info(f"Uploaded: {fid} name={safe} size={len(data)} storage={storage}",
                extra={"request_id": rid})
    return UploadResponse(**{**meta, "storage": storage})


@app.get("/upload/{file_id}")
async def get_upload(file_id: str, request: Request):
    rid = getattr(request.state, "request_id", "-")
    try:
        from db import db_get_upload
        meta = await db_get_upload(file_id)
        if meta: return meta
    except Exception as e:
        logger.warning(f"DB get: {e}", extra={"request_id": rid})
    raise HTTPException(404, "File not found")


@app.delete("/upload/{file_id}")
async def delete_upload(file_id: str, request: Request):
    rid = getattr(request.state, "request_id", "-")
    try:
        from db import db_get_upload, db_delete_upload
        meta = await db_get_upload(file_id)
        if not meta: raise HTTPException(404, "File not found")
        fp = Path(meta.get("file_path", ""))
        if fp.exists(): fp.unlink()
        await db_delete_upload(file_id)
        logger.info(f"Deleted: {file_id}", extra={"request_id": rid})
        return {"message": "File deleted", "file_id": file_id}
    except HTTPException: raise
    except Exception as e: raise HTTPException(500, str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("UPLOAD_PORT", 8000)))
