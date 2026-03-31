from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime, timedelta
import uuid
import logging
import os
import random
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

app = FastAPI(title="Phase 1 AI Service — Lab 6", version="2.0.0")

VALID_ANALYSIS_TYPES = {"general", "vision", "nlp", "classification"}

MOCK_AI_RESPONSES = {
    "image": {
        "objects": ["person", "dog", "tree", "car", "building"],
        "scene": "outdoor",
        "dominant_colors": ["#FF6B35", "#004E89", "#1A659E"],
        "quality_score": 0.85,
    },
    "document": {
        "language": "en", "word_count": 25, "reading_time_minutes": 2,
        "key_topics": ["sample", "document", "text"], "sentiment": "neutral",
    },
    "general": {
        "file_type": "unknown", "size_category": "medium",
        "entropy": 0.75, "patterns_detected": ["binary_data", "structured_content"],
    },
}


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


class AIRequest(BaseModel):
    analysis_type:        str   = "general"
    confidence_threshold: float = 0.7

class AIResponse(BaseModel):
    file_id:       str
    analysis_type: str
    results:       dict
    confidence:    float
    model_version: str
    request_id:    str
    timestamp:     str

class BatchAnalyzeRequest(BaseModel):
    file_ids:      list[str]
    analysis_type: str = "general"


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "ai-service", "version": "2.0.0",
            "timestamp": datetime.now().isoformat()}


@app.post("/analyze/batch")
async def batch_analyze(body: BatchAnalyzeRequest, request: Request):
    rid = getattr(request.state, "request_id", "-")
    results = []
    for fid in body.file_ids:
        try:
            r = await _do_analyze(fid, AIRequest(analysis_type=body.analysis_type), rid)
            results.append({"file_id": fid, "status": "success", "result": r})
        except Exception as e:
            results.append({"file_id": fid, "status": "failed", "error": str(e)})
    return {
        "batch_id": str(uuid.uuid4()),
        "total_files": len(body.file_ids),
        "successful": sum(1 for r in results if r["status"] == "success"),
        "failed":     sum(1 for r in results if r["status"] == "failed"),
        "results": results,
    }


@app.post("/analyze/{file_id}", response_model=AIResponse)
async def analyze_file(file_id: str, body: AIRequest, request: Request):
    rid = getattr(request.state, "request_id", "-")
    return await _do_analyze(file_id, body, rid)


async def _do_analyze(file_id: str, req: AIRequest, rid: str) -> AIResponse:
    import asyncio
    if req.analysis_type not in VALID_ANALYSIS_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid analysis_type. Use: {VALID_ANALYSIS_TYPES}")

    logger.info(f"AI analysis start: {file_id} type={req.analysis_type}", extra={"request_id": rid})
    await asyncio.sleep(random.uniform(1, 3))

    cat = "image" if "img" in file_id.lower() else "document" if "doc" in file_id.lower() else "general"
    results = MOCK_AI_RESPONSES.get(cat, MOCK_AI_RESPONSES["general"]).copy()
    confidence = random.uniform(0.65, 0.95)

    results.update({
        "confidence":      confidence,
        "processing_time": random.uniform(0.5, 2.0),
        "analysis_id":     str(uuid.uuid4()),
        "model":           f"mock-ai-{cat}-v2.0",
        "request_id":      rid,
    })

    if req.analysis_type == "classification":
        results["classification"] = {
            "category":    random.choice(["personal", "business", "educational"]),
            "subcategory": random.choice(["document", "media", "archive"]),
            "tags":        random.sample(["important", "draft", "final", "shared"], 2),
        }
    elif req.analysis_type == "vision":
        results["vision_analysis"] = {
            "object_detection": [
                {"object": o, "confidence": random.uniform(0.7, 0.95)}
                for o in random.sample(MOCK_AI_RESPONSES["image"]["objects"], 3)
            ],
            "scene_classification": random.choice(["indoor", "outdoor", "nature", "urban"]),
            "nsfw_score": round(random.uniform(0.0, 0.05), 3),
        }
    elif req.analysis_type == "nlp":
        results["nlp_analysis"] = {
            "entities": [
                {"text": "Sample Corp", "label": "ORG"},
                {"text": "John Doe",    "label": "PERSON"},
                {"text": "Bangkok",     "label": "LOC"},
            ],
            "summary":  "Document contains sample business content.",
            "keywords": ["business", "sample", "document"],
        }

    logger.info(f"AI analysis done: {file_id} confidence={confidence:.2f}", extra={"request_id": rid})
    return AIResponse(
        file_id=file_id, analysis_type=req.analysis_type, results=results,
        confidence=confidence, model_version="mock-ai-v2.0",
        request_id=rid, timestamp=datetime.now().isoformat()
    )


@app.get("/analyze/{file_id}/history")
async def get_history(file_id: str):
    return {
        "file_id": file_id,
        "history": [
            {
                "analysis_id":   str(uuid.uuid4()),
                "analysis_type": random.choice(["general", "vision", "nlp", "classification"]),
                "timestamp":     (datetime.now() - timedelta(hours=i)).isoformat(),
                "confidence":    round(random.uniform(0.6, 0.9), 2),
            }
            for i in range(random.randint(1, 4))
        ],
    }


@app.get("/models")
async def get_models():
    return {"models": [
        {"name": "mock-vision-v2.0",     "type": "vision",         "accuracy": 0.87},
        {"name": "mock-nlp-v2.0",        "type": "nlp",            "accuracy": 0.80},
        {"name": "mock-classifier-v2.0", "type": "classification", "accuracy": 0.84},
    ]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("AI_PORT", 8002)))
