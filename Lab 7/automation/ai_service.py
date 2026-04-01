"""
Lab 7 — AI Service (port 8002)
Saves analysis results to PostgreSQL + caches in Redis
"""
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime, timedelta
import asyncio, uuid, logging, os, random, time
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
app = FastAPI(title="AI Service — Lab 7", version="3.0.0")

VALID_TYPES = {"general", "vision", "nlp", "classification"}
MOCK_RESPONSES = {
    "image":    {"objects": ["person","dog","tree","car"], "scene": "outdoor", "quality_score": 0.85},
    "document": {"language": "en", "word_count": 25, "sentiment": "neutral", "key_topics": ["sample"]},
    "general":  {"file_type": "unknown", "entropy": 0.75, "patterns_detected": ["structured_content"]},
}


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


class AIRequest(BaseModel):
    analysis_type: str = "general"; confidence_threshold: float = 0.7

class AIResponse(BaseModel):
    file_id: str; analysis_type: str; results: dict
    confidence: float; model_version: str; request_id: str
    timestamp: str; storage: str = "postgresql"

class BatchRequest(BaseModel):
    file_ids: list[str]; analysis_type: str = "general"


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "ai-service", "version": "3.0.0",
            "timestamp": datetime.now().isoformat()}


@app.post("/analyze/batch")
async def batch_analyze(body: BatchRequest, request: Request):
    rid = getattr(request.state, "request_id", "-")
    results = []
    for fid in body.file_ids:
        try:
            r = await _analyze(fid, AIRequest(analysis_type=body.analysis_type), rid)
            results.append({"file_id": fid, "status": "success", "result": r})
        except Exception as e:
            results.append({"file_id": fid, "status": "failed", "error": str(e)})
    return {"batch_id": str(uuid.uuid4()), "total_files": len(body.file_ids),
            "successful": sum(1 for r in results if r["status"] == "success"),
            "failed":     sum(1 for r in results if r["status"] == "failed"),
            "results": results}


@app.post("/analyze/{file_id}", response_model=AIResponse)
async def analyze_file(file_id: str, body: AIRequest, request: Request):
    rid = getattr(request.state, "request_id", "-")
    return await _analyze(file_id, body, rid)


async def _analyze(file_id: str, req: AIRequest, rid: str) -> AIResponse:
    if req.analysis_type not in VALID_TYPES:
        raise HTTPException(400, f"Invalid analysis_type. Use: {VALID_TYPES}")

    logger.info(f"AI analysis start: {file_id} type={req.analysis_type}", extra={"request_id": rid})
    await asyncio.sleep(random.uniform(1, 3))

    cat        = "image" if "img" in file_id.lower() else "document" if "doc" in file_id.lower() else "general"
    results    = MOCK_RESPONSES.get(cat, MOCK_RESPONSES["general"]).copy()
    confidence = round(random.uniform(0.65, 0.95), 3)
    aid        = str(uuid.uuid4())

    results.update({"confidence": confidence, "processing_time": round(random.uniform(0.5, 2.0), 3),
                    "analysis_id": aid, "model": f"mock-ai-{cat}-v3.0", "request_id": rid})

    if req.analysis_type == "vision":
        results["vision_analysis"] = {
            "object_detection": [{"object": o, "confidence": round(random.uniform(0.7, 0.95), 3)}
                                  for o in random.sample(["person","dog","tree","car"], 3)],
            "scene_classification": random.choice(["indoor","outdoor","nature","urban"]),
        }
    elif req.analysis_type == "nlp":
        results["nlp_analysis"] = {
            "entities": [{"text": "Lab Corp", "label": "ORG"}, {"text": "Bangkok", "label": "LOC"}],
            "keywords": ["network","microservice","enterprise"],
        }
    elif req.analysis_type == "classification":
        results["classification"] = {
            "category":    random.choice(["business","educational","personal"]),
            "tags":        random.sample(["important","draft","final"], 2),
        }

    storage = "filesystem"
    try:
        from db import db_save_analysis
        await db_save_analysis({"analysis_id": aid, "file_id": file_id,
                                "analysis_type": req.analysis_type, "confidence": confidence,
                                "model_version": "mock-ai-v3.0", "results": results,
                                "request_id": rid})
        storage = "postgresql"
    except Exception as e:
        logger.warning(f"DB save analysis: {e}", extra={"request_id": rid})

    logger.info(f"AI done: {file_id} confidence={confidence} storage={storage}",
                extra={"request_id": rid})
    return AIResponse(file_id=file_id, analysis_type=req.analysis_type, results=results,
                      confidence=confidence, model_version="mock-ai-v3.0",
                      request_id=rid, timestamp=datetime.now().isoformat(), storage=storage)


@app.get("/analyze/{file_id}/history")
async def get_history(file_id: str):
    try:
        from db import db_get_analysis_history
        history = await db_get_analysis_history(file_id)
        if history:
            return {"file_id": file_id, "total_analyses": len(history),
                    "history": history, "source": "postgresql"}
    except Exception: pass
    return {"file_id": file_id, "total_analyses": 0, "history": [], "source": "none"}


@app.get("/models")
async def get_models():
    return {"models": [
        {"name": "mock-vision-v3.0",     "type": "vision",         "accuracy": 0.87},
        {"name": "mock-nlp-v3.0",        "type": "nlp",            "accuracy": 0.80},
        {"name": "mock-classifier-v3.0", "type": "classification", "accuracy": 0.84},
    ]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("AI_PORT", 8002)))
