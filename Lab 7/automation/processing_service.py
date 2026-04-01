"""
Lab 7 — Processing Service (port 8001)
Saves job results to PostgreSQL + caches in Redis
"""
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime
import asyncio, json, uuid, logging, os, time
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
app = FastAPI(title="Processing Service — Lab 7", version="3.0.0")

PROCESSING_DIR     = Path("mock_storage"); PROCESSING_DIR.mkdir(exist_ok=True)
SUPPORTED_OPS      = {"thumbnail", "resize", "convert"}


@app.middleware("http")
async def rid_middleware(request: Request, call_next):
    rid  = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    request.state.request_id = rid
    start = time.perf_counter()
    resp  = await call_next(request)
    ms    = (time.perf_counter() - start) * 1000
    resp.headers["X-Request-ID"]    = rid
    resp.headers["X-Response-Time"] = f"{ms:.1f}ms"
    logger.info(f"{request.method} {request.url.path} → {resp.status_code} ({ms:.1f}ms)",
                extra={"request_id": rid})
    return resp


class ProcessingRequest(BaseModel):
    operation: str = "thumbnail"; parameters: dict = {}

class ProcessingResponse(BaseModel):
    file_id: str; operation: str; status: str
    output_file: str; processing_time: float; timestamp: str; storage: str = "postgresql"

class BatchRequest(BaseModel):
    file_ids: list[str]; operation: str = "thumbnail"


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "processing-service", "version": "3.0.0",
            "timestamp": datetime.now().isoformat()}


@app.post("/process/batch")
async def batch_process(body: BatchRequest, request: Request):
    rid = getattr(request.state, "request_id", "-")
    results = []
    for fid in body.file_ids:
        try:
            r = await _process(fid, ProcessingRequest(operation=body.operation), rid)
            results.append({"file_id": fid, "status": "success", "result": r})
        except Exception as e:
            results.append({"file_id": fid, "status": "failed", "error": str(e)})
    return {"batch_id": str(uuid.uuid4()), "total_files": len(body.file_ids),
            "successful": sum(1 for r in results if r["status"] == "success"),
            "failed":     sum(1 for r in results if r["status"] == "failed"),
            "results": results}


@app.post("/process/{file_id}", response_model=ProcessingResponse)
async def process_file(file_id: str, body: ProcessingRequest, request: Request):
    rid = getattr(request.state, "request_id", "-")
    return await _process(file_id, body, rid)


async def _process(file_id: str, req: ProcessingRequest, rid: str) -> ProcessingResponse:
    if req.operation not in SUPPORTED_OPS:
        raise HTTPException(400, f"Unsupported operation: {req.operation}. Use: {SUPPORTED_OPS}")

    start = datetime.now()
    delay = 2.0 + (hash(file_id) % 30) / 10.0
    logger.info(f"Processing start: {file_id} op={req.operation}", extra={"request_id": rid})
    await asyncio.sleep(delay)

    out_name = f"{file_id}_processed_{req.operation}.jpg"
    out_path = PROCESSING_DIR / out_name
    out_path.write_text(json.dumps({
        "file_id": file_id, "operation": req.operation,
        "parameters": req.parameters, "processed_at": datetime.now().isoformat(),
        "request_id": rid
    }, indent=2))

    elapsed = (datetime.now() - start).total_seconds()
    job = {"job_id": str(uuid.uuid4()), "file_id": file_id,
           "operation": req.operation, "status": "completed",
           "output_file": str(out_path), "processing_time": elapsed,
           "request_id": rid}

    storage = "filesystem"
    try:
        from db import db_save_processing
        await db_save_processing(job); storage = "postgresql+redis"
    except Exception as e:
        logger.warning(f"DB save processing: {e}", extra={"request_id": rid})

    logger.info(f"Processing done: {file_id} in {elapsed:.2f}s storage={storage}",
                extra={"request_id": rid})
    return ProcessingResponse(**{**job, "timestamp": datetime.now().isoformat(), "storage": storage})


@app.get("/process/{file_id}/status")
async def get_status(file_id: str):
    try:
        from db import cache_get
        cached = await cache_get(f"processing:{file_id}:latest")
        if cached: return {"file_id": file_id, "status": "processed", "source": "redis", **cached}
    except Exception: pass
    files = list(PROCESSING_DIR.glob(f"{file_id}_processed_*"))
    if not files:
        return {"file_id": file_id, "status": "not_processed"}
    latest = max(files, key=lambda x: x.stat().st_mtime)
    return {"file_id": file_id, "status": "processed", "output_file": str(latest),
            "processed_at": datetime.fromtimestamp(latest.stat().st_mtime).isoformat()}


@app.get("/process/operations")
async def get_operations():
    return {"operations": list(SUPPORTED_OPS)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PROCESSING_PORT", 8001)))
