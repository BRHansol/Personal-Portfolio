"""
Lab 7 — Gateway Service (port 9000)
Orchestrates Upload → Processing → AI
Saves workflow to PostgreSQL, reads/writes Redis cache
"""
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel
from datetime import datetime
import httpx, asyncio, logging, json, os, uuid, time
from typing import Optional, Dict, Any
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

limiter = Limiter(key_func=get_remote_address)
app     = FastAPI(title="Gateway Service — Lab 7", version="3.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=True, allow_methods=["GET","POST"], allow_headers=["*"])

UPLOAD_URL     = os.getenv("UPLOAD_SERVICE_URL",     "http://localhost:8000")
PROCESSING_URL = os.getenv("PROCESSING_SERVICE_URL", "http://localhost:8001")
AI_URL         = os.getenv("AI_SERVICE_URL",         "http://localhost:8002")


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
    enable_processing: bool = True; processing_operation: str = "thumbnail"
    enable_ai_analysis: bool = True; ai_analysis_type: str = "general"

class WorkflowResponse(BaseModel):
    workflow_id: str; request_id: str; file_id: str
    upload_status: str; processing_status: Optional[str] = None
    ai_analysis_status: Optional[str] = None; total_time: float
    timestamp: str; storage: str = "postgresql"


@app.get("/health")
async def health(request: Request):
    rid   = getattr(request.state, "request_id", "-")
    start = datetime.now()
    svcs  = {"gateway": "healthy", "upload": "unknown", "processing": "unknown", "ai": "unknown",
             "redis": "unknown", "postgres": "unknown"}
    async with httpx.AsyncClient(timeout=5.0) as c:
        for name, url in [("upload", f"{UPLOAD_URL}/health"),
                          ("processing", f"{PROCESSING_URL}/health"),
                          ("ai", f"{AI_URL}/health")]:
            try:
                r = await c.get(url)
                if r.status_code == 200:
                    svcs[name] = "healthy"
                    sub = r.json().get("services", {})
                    if "redis"    in sub: svcs["redis"]    = sub["redis"]
                    if "postgres" in sub: svcs["postgres"] = sub["postgres"]
                else:
                    svcs[name] = f"unhealthy ({r.status_code})"
            except Exception as e:
                svcs[name] = f"unreachable: {e}"
    overall = "healthy" if all(v == "healthy" for k, v in svcs.items() if k != "gateway") else "degraded"
    logger.info(f"Health: {overall}", extra={"request_id": rid})
    return {"status": overall, "timestamp": datetime.now().isoformat(),
            "check_duration": (datetime.now() - start).total_seconds(), "services": svcs}


@app.post("/process-file", response_model=WorkflowResponse)
@limiter.limit("20/minute")
async def process_file(
    request: Request, background_tasks: BackgroundTasks,
    file: UploadFile = File(...), processing_options: Optional[str] = Form(None)
):
    rid  = getattr(request.state, "request_id", str(uuid.uuid4())[:8])
    opts = _parse_opts(processing_options)
    wfid = f"wf_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{rid}"
    start = datetime.now()

    if not file.filename or ".." in file.filename:
        raise HTTPException(400, "Invalid filename")

    logger.info(f"Workflow start: {wfid} file={file.filename}", extra={"request_id": rid})

    try:
        up_result  = await _call_upload(file, rid)
        file_id    = up_result["file_id"]

        proc_result = None
        if opts.enable_processing:
            proc_result = await _call_processing(file_id, opts.processing_operation, rid)

        ai_result = None
        if opts.enable_ai_analysis:
            ai_result = await _call_ai(file_id, opts.ai_analysis_type, rid)

        total = (datetime.now() - start).total_seconds()

        workflow = WorkflowResponse(
            workflow_id=wfid, request_id=rid, file_id=file_id,
            upload_status="completed",
            processing_status=proc_result.get("status", "completed") if proc_result else "skipped",
            ai_analysis_status="completed" if ai_result else "skipped",
            total_time=total, timestamp=datetime.now().isoformat()
        )

        try:
            from db import db_save_workflow
            await db_save_workflow(workflow.model_dump())
            workflow.storage = "postgresql"
        except Exception as e:
            logger.warning(f"DB save workflow: {e}", extra={"request_id": rid})
            workflow.storage = "memory"

        logger.info(f"Workflow done: {wfid} in {total:.2f}s", extra={"request_id": rid})
        return workflow

    except HTTPException: raise
    except Exception as e:
        logger.error(f"Workflow failed: {wfid} — {e}", extra={"request_id": rid})
        raise HTTPException(500, f"Workflow failed: {e}")


@app.post("/upload-only")
@limiter.limit("30/minute")
async def upload_only(request: Request, file: UploadFile = File(...)):
    rid = getattr(request.state, "request_id", "-")
    return await _call_upload(file, rid)


@app.post("/process-existing/{file_id}")
@limiter.limit("20/minute")
async def process_existing(request: Request, file_id: str,
                            opts: ProcessingRequest = ProcessingRequest()):
    rid = getattr(request.state, "request_id", "-")
    results = {}
    if opts.enable_processing:
        results["processing"] = await _call_processing(file_id, opts.processing_operation, rid)
    if opts.enable_ai_analysis:
        results["ai_analysis"] = await _call_ai(file_id, opts.ai_analysis_type, rid)
    return {"file_id": file_id, "results": results, "timestamp": datetime.now().isoformat()}


@app.get("/stats")
async def stats():
    cached = None
    try:
        from db import cache_get
        cached = await cache_get("gateway:stats")
    except Exception: pass
    return {"service": "gateway", "version": "3.0.0", "phase": "lab7",
            "cached_stats": cached, "timestamp": datetime.now().isoformat()}


# ── Helpers ────────────────────────────────────────────────────────────────────
def _parse_opts(raw: Optional[str]) -> ProcessingRequest:
    if not raw or not raw.strip(): return ProcessingRequest()
    try: return ProcessingRequest.model_validate_json(raw)
    except Exception: return ProcessingRequest.model_validate(json.loads(raw))


async def _call_upload(file: UploadFile, rid: str) -> Dict[str, Any]:
    try:
        await file.seek(0)
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{UPLOAD_URL}/upload",
                             files={"file": (file.filename, file.file, file.content_type)},
                             headers={"X-Request-ID": rid})
            if r.status_code != 200:
                raise HTTPException(r.status_code, f"Upload error: {r.text}")
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(503, "Upload service unavailable")


async def _call_processing(file_id: str, operation: str, rid: str) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(f"{PROCESSING_URL}/process/{file_id}",
                             json={"operation": operation, "parameters": {}},
                             headers={"X-Request-ID": rid})
            if r.status_code != 200:
                raise HTTPException(r.status_code, f"Processing error: {r.text}")
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(503, "Processing service unavailable")


async def _call_ai(file_id: str, analysis_type: str, rid: str) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{AI_URL}/analyze/{file_id}",
                             json={"analysis_type": analysis_type, "confidence_threshold": 0.7},
                             headers={"X-Request-ID": rid})
            if r.status_code != 200:
                raise HTTPException(r.status_code, f"AI error: {r.text}")
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(503, "AI service unavailable")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("GATEWAY_PORT", 9000)))
