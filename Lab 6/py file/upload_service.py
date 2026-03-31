from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime
import uuid
import json
import aiofiles
from pathlib import Path
import logging
import os
import time

# ── Structured logging ───────────────────────────────────────────────────────
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

app = FastAPI(title="Phase 1 Upload Service — Lab 6", version="2.0.0")

UPLOAD_DIR   = Path("mock_storage")
METADATA_DIR = Path("mock_metadata")
UPLOAD_DIR.mkdir(exist_ok=True)
METADATA_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE_MB", 10)) * 1024 * 1024

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".txt", ".docx"}


# ── Request ID middleware ────────────────────────────────────────────────────
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    rid = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    request.state.request_id = rid
    start = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = rid
    response.headers["X-Response-Time"] = f"{ms:.1f}ms"
    logger.info(f"{request.method} {request.url.path} → {response.status_code} ({ms:.1f}ms)",
                extra={"request_id": rid})
    return response


class UploadResponse(BaseModel):
    file_id:          str
    filename:         str
    size:             int
    mime_type:        str
    status:           str
    upload_timestamp: str


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "upload-service", "version": "2.0.0",
            "timestamp": datetime.now().isoformat()}


@app.post("/upload", response_model=UploadResponse)
async def upload_file(request: Request, file: UploadFile = File(...)):
    rid = getattr(request.state, "request_id", "-")

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Extension validation
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        logger.warning(f"Rejected file extension: {ext}", extra={"request_id": rid})
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {ext}")

    # Sanitize filename
    safe_name = Path(file.filename).name.replace("..", "").replace("/", "")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large (max {MAX_FILE_SIZE // 1024 // 1024}MB)")

    file_id = str(uuid.uuid4())
    file_path = UPLOAD_DIR / f"{file_id}_{safe_name}"

    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    metadata = {
        "file_id":          file_id,
        "filename":         safe_name,
        "size":             len(content),
        "mime_type":        file.content_type or "application/octet-stream",
        "status":           "uploaded",
        "upload_timestamp": datetime.now().isoformat(),
        "file_path":        str(file_path),
        "request_id":       rid,
    }
    async with aiofiles.open(METADATA_DIR / f"{file_id}.json", "w") as f:
        await f.write(json.dumps(metadata, indent=2))

    logger.info(f"Uploaded: {file_id} name={safe_name} size={len(content)}",
                extra={"request_id": rid})
    return UploadResponse(**metadata)


@app.get("/upload/{file_id}")
async def get_upload_status(file_id: str, request: Request):
    rid = getattr(request.state, "request_id", "-")
    meta_path = METADATA_DIR / f"{file_id}.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    async with aiofiles.open(meta_path, "r") as f:
        return json.loads(await f.read())


@app.delete("/upload/{file_id}")
async def delete_upload(file_id: str, request: Request):
    rid = getattr(request.state, "request_id", "-")
    meta_path = METADATA_DIR / f"{file_id}.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    async with aiofiles.open(meta_path, "r") as f:
        meta = json.loads(await f.read())
    fp = Path(meta["file_path"])
    if fp.exists():
        fp.unlink()
    meta_path.unlink()
    logger.info(f"Deleted: {file_id}", extra={"request_id": rid})
    return {"message": "File deleted", "file_id": file_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("UPLOAD_PORT", 8000)))
