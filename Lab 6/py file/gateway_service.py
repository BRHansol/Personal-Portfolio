from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks, Form, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel
from datetime import datetime
import httpx
import asyncio
import logging
import json
import os
import uuid
import time
from typing import Optional, Dict, Any
from dotenv import load_dotenv

load_dotenv()

# ── Structured logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s | req=%(request_id)s | %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S'
)

class RequestIDFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, 'request_id'):
            record.request_id = '-'
        return True

logger = logging.getLogger(__name__)
logger.addFilter(RequestIDFilter())

# ── Rate limiter ─────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Phase 1 Gateway Service — Lab 6", version="2.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── Request ID middleware ────────────────────────────────────────────────────
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    request.state.request_id = request_id
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time"] = f"{duration_ms:.1f}ms"
    logger.info(
        f"{request.method} {request.url.path} → {response.status_code} ({duration_ms:.1f}ms)",
        extra={"request_id": request_id}
    )
    return response

# ── Service URLs ─────────────────────────────────────────────────────────────
UPLOAD_SERVICE_URL     = os.getenv("UPLOAD_SERVICE_URL",     "http://localhost:8001")
PROCESSING_SERVICE_URL = os.getenv("PROCESSING_SERVICE_URL", "http://localhost:8002")
AI_SERVICE_URL         = os.getenv("AI_SERVICE_URL",         "http://localhost:8003")

class ProcessingRequest(BaseModel):
    enable_processing:    bool = True
    processing_operation: str  = "thumbnail"
    enable_ai_analysis:   bool = True
    ai_analysis_type:     str  = "general"

class WorkflowResponse(BaseModel):
    workflow_id:         str
    request_id:          str
    file_id:             str
    upload_status:       str
    processing_status:   Optional[str] = None
    ai_analysis_status:  Optional[str] = None
    total_time:          float
    timestamp:           str


# ── Health ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health_check(request: Request):
    rid = getattr(request.state, "request_id", "-")
    start = datetime.now()
    services = {
        "gateway":    {"url": "self",                          "status": "healthy"},
        "upload":     {"url": f"{UPLOAD_SERVICE_URL}/health",     "status": "unknown"},
        "processing": {"url": f"{PROCESSING_SERVICE_URL}/health", "status": "unknown"},
        "ai":         {"url": f"{AI_SERVICE_URL}/health",         "status": "unknown"},
    }
    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, info in services.items():
            if name == "gateway":
                continue
            try:
                r = await client.get(info["url"])
                info["status"] = "healthy" if r.status_code == 200 else f"unhealthy ({r.status_code})"
            except Exception as e:
                info["status"] = f"unreachable: {e}"
    overall = "healthy" if all(v["status"] == "healthy" for v in services.values()) else "degraded"
    logger.info(f"Health check: {overall}", extra={"request_id": rid})
    return {
        "status": overall,
        "timestamp": datetime.now().isoformat(),
        "check_duration": (datetime.now() - start).total_seconds(),
        "services": services,
    }


# ── Process-file (rate limited: 20/minute) ───────────────────────────────────
@app.post("/process-file", response_model=WorkflowResponse)
@limiter.limit("20/minute")
async def process_file_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    processing_options: Optional[str] = Form(None),
):
    rid = getattr(request.state, "request_id", str(uuid.uuid4())[:8])
    opts = _parse_processing_options(processing_options)
    workflow_id = f"wf_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{rid}"
    start = datetime.now()

    logger.info(f"Workflow start: {workflow_id} file={file.filename}", extra={"request_id": rid})

    # Validate filename
    if not file.filename or ".." in file.filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    try:
        upload_result = await _upload_file(file, rid)
        file_id = upload_result["file_id"]

        processing_result = None
        if opts.enable_processing:
            processing_result = await _process_file(file_id, opts.processing_operation, rid)

        ai_result = None
        if opts.enable_ai_analysis:
            ai_result = await _analyze_file(file_id, opts.ai_analysis_type, rid)

        total_time = (datetime.now() - start).total_seconds()
        logger.info(f"Workflow done: {workflow_id} in {total_time:.2f}s", extra={"request_id": rid})

        return WorkflowResponse(
            workflow_id=workflow_id,
            request_id=rid,
            file_id=file_id,
            upload_status="completed",
            processing_status=processing_result.get("status", "completed") if processing_result else "skipped",
            ai_analysis_status="completed" if ai_result else "skipped",
            total_time=total_time,
            timestamp=datetime.now().isoformat(),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Workflow failed: {workflow_id} — {e}", extra={"request_id": rid})
        raise HTTPException(status_code=500, detail=f"Workflow failed: {e}")


@app.post("/upload-only")
@limiter.limit("30/minute")
async def upload_only(request: Request, file: UploadFile = File(...)):
    rid = getattr(request.state, "request_id", "-")
    return await _upload_file(file, rid)


@app.post("/process-existing/{file_id}")
@limiter.limit("20/minute")
async def process_existing(
    request: Request,
    file_id: str,
    processing_options: ProcessingRequest = ProcessingRequest(),
):
    rid = getattr(request.state, "request_id", "-")
    results = {}
    if processing_options.enable_processing:
        results["processing"] = await _process_file(file_id, processing_options.processing_operation, rid)
    if processing_options.enable_ai_analysis:
        results["ai_analysis"] = await _analyze_file(file_id, processing_options.ai_analysis_type, rid)
    return {"file_id": file_id, "results": results, "timestamp": datetime.now().isoformat()}


@app.get("/stats")
async def stats():
    return {"service": "gateway", "version": "2.0.0", "phase": "lab6", "timestamp": datetime.now().isoformat()}


# ── Helpers ───────────────────────────────────────────────────────────────────
def _parse_processing_options(raw: Optional[str]) -> ProcessingRequest:
    if not raw or not raw.strip():
        return ProcessingRequest()
    try:
        return ProcessingRequest.model_validate_json(raw)
    except Exception:
        return ProcessingRequest.model_validate(json.loads(raw))


async def _upload_file(file: UploadFile, rid: str) -> Dict[str, Any]:
    try:
        await file.seek(0)
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{UPLOAD_SERVICE_URL}/upload",
                files={"file": (file.filename, file.file, file.content_type)},
                headers={"X-Request-ID": rid},
            )
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code, detail=f"Upload error: {r.text}")
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Upload service unavailable")


async def _process_file(file_id: str, operation: str, rid: str) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{PROCESSING_SERVICE_URL}/process/{file_id}",
                json={"operation": operation, "parameters": {}},
                headers={"X-Request-ID": rid},
            )
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code, detail=f"Processing error: {r.text}")
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Processing service unavailable")


async def _analyze_file(file_id: str, analysis_type: str, rid: str) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{AI_SERVICE_URL}/analyze/{file_id}",
                json={"analysis_type": analysis_type, "confidence_threshold": 0.7},
                headers={"X-Request-ID": rid},
            )
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code, detail=f"AI error: {r.text}")
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="AI service unavailable")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("GATEWAY_PORT", 9000)))
