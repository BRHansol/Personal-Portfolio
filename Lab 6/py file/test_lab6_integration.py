"""
Lab 6 — Integration Tests (upgraded from Phase 1)
Covers: cross-service tracing, file lifecycle, batch ops, error recovery, performance
"""

import pytest
import httpx
import asyncio
import json
import os
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Service URLs (Lab 6 ports) ────────────────────────────────────────────────
GATEWAY_URL    = os.getenv("GATEWAY_URL",            "http://localhost:9000")
UPLOAD_URL     = os.getenv("UPLOAD_SERVICE_URL",     "http://localhost:8000")
PROCESSING_URL = os.getenv("PROCESSING_SERVICE_URL", "http://localhost:8001")
AI_URL         = os.getenv("AI_SERVICE_URL",         "http://localhost:8002")

TEST_DATA_DIR = Path("test_data")
TEST_DATA_DIR.mkdir(exist_ok=True)

TEST_SCENARIOS = [
    {
        "name":      "Image upload and vision analysis",
        "filename":  "test_image.jpg",
        "content":   "Mock JPEG for Lab 6 integration test",
        "mime":      "image/jpeg",
        "operation": "thumbnail",
        "ai_type":   "vision",
    },
    {
        "name":      "Text document NLP analysis",
        "filename":  "test_doc.txt",
        "content":   "Sample document text for NLP processing.",
        "mime":      "text/plain",
        "operation": "convert",
        "ai_type":   "nlp",
    },
    {
        "name":      "General binary file",
        "filename":  "test_data.bin",
        "content":   "Binary content simulation",
        "mime":      "application/octet-stream",
        "operation": "thumbnail",
        "ai_type":   "general",
    },
]


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture
async def client():
    async with httpx.AsyncClient(timeout=120.0) as c:
        yield c

@pytest.fixture
def scenario_files():
    files = []
    for s in TEST_SCENARIOS:
        p = TEST_DATA_DIR / s["filename"]
        p.write_text(s["content"])
        files.append(p)
    return files

@pytest.fixture
def image_file():
    p = TEST_DATA_DIR / "test_image.jpg"
    p.write_text("Mock JPEG for Lab 6")
    return p


# ── Helper ────────────────────────────────────────────────────────────────────
async def upload(client, filepath, filename=None, mime="image/jpeg") -> str:
    name = filename or Path(filepath).name
    with open(filepath, "rb") as f:
        r = await client.post(f"{UPLOAD_URL}/upload",
                              files={"file": (name, f, mime)})
    assert r.status_code == 200, f"Upload failed: {r.text}"
    return r.json()["file_id"]


# ════════════════════════════════════════════════════════════════════════════
# 1. Complete workflow variations
# ════════════════════════════════════════════════════════════════════════════
class TestWorkflowVariations:

    @pytest.mark.asyncio
    async def test_all_scenarios(self, client, scenario_files):
        """Run all 3 file-type scenarios through full gateway pipeline"""
        for s in TEST_SCENARIOS:
            fp = TEST_DATA_DIR / s["filename"]
            with open(fp, "rb") as f:
                r = await client.post(
                    f"{GATEWAY_URL}/process-file",
                    files={"file": (s["filename"], f, s["mime"])},
                    data={"processing_options": json.dumps({
                        "enable_processing":    True,
                        "processing_operation": s["operation"],
                        "enable_ai_analysis":   True,
                        "ai_analysis_type":     s["ai_type"],
                    })},
                )
            assert r.status_code == 200, f"Scenario '{s['name']}' failed: {r.text}"
            d = r.json()
            assert d["upload_status"]      == "completed", s["name"]
            assert d["processing_status"]  == "completed", s["name"]
            assert d["ai_analysis_status"] == "completed", s["name"]

    @pytest.mark.asyncio
    async def test_upload_only_no_processing(self, client, image_file):
        with open(image_file, "rb") as f:
            r = await client.post(
                f"{GATEWAY_URL}/process-file",
                files={"file": ("test_image.jpg", f, "image/jpeg")},
                data={"processing_options": json.dumps({
                    "enable_processing": False, "enable_ai_analysis": False,
                })},
            )
        assert r.status_code == 200
        d = r.json()
        assert d["processing_status"]  == "skipped"
        assert d["ai_analysis_status"] == "skipped"

    @pytest.mark.asyncio
    async def test_processing_only_no_ai(self, client, image_file):
        with open(image_file, "rb") as f:
            r = await client.post(
                f"{GATEWAY_URL}/process-file",
                files={"file": ("test_image.jpg", f, "image/jpeg")},
                data={"processing_options": json.dumps({
                    "enable_processing": True, "processing_operation": "thumbnail",
                    "enable_ai_analysis": False,
                })},
            )
        assert r.status_code == 200
        d = r.json()
        assert d["processing_status"]  == "completed"
        assert d["ai_analysis_status"] == "skipped"

    @pytest.mark.asyncio
    async def test_ai_only_no_processing(self, client, image_file):
        with open(image_file, "rb") as f:
            r = await client.post(
                f"{GATEWAY_URL}/process-file",
                files={"file": ("test_image.jpg", f, "image/jpeg")},
                data={"processing_options": json.dumps({
                    "enable_processing": False,
                    "enable_ai_analysis": True, "ai_analysis_type": "vision",
                })},
            )
        assert r.status_code == 200
        d = r.json()
        assert d["processing_status"]  == "skipped"
        assert d["ai_analysis_status"] == "completed"


# ════════════════════════════════════════════════════════════════════════════
# 2. Cross-service request ID tracing (Lab 6)
# ════════════════════════════════════════════════════════════════════════════
class TestRequestTracing:

    @pytest.mark.asyncio
    async def test_request_id_propagated_through_gateway(self, client, image_file):
        """Lab 6: same X-Request-ID must flow from client → gateway response"""
        with open(image_file, "rb") as f:
            r = await client.post(
                f"{GATEWAY_URL}/process-file",
                files={"file": ("test_image.jpg", f, "image/jpeg")},
                headers={"X-Request-ID": "trace-end-to-end-001"},
            )
        assert r.status_code == 200
        assert r.headers.get("x-request-id") == "trace-end-to-end-001"
        assert r.json()["request_id"] == "trace-end-to-end-001"

    @pytest.mark.asyncio
    async def test_auto_generated_request_id(self, client, image_file):
        """Lab 6: if no X-Request-ID sent, server generates one"""
        with open(image_file, "rb") as f:
            r = await client.post(
                f"{GATEWAY_URL}/upload-only",
                files={"file": ("test_image.jpg", f, "image/jpeg")},
            )
        assert r.status_code == 200
        assert "x-request-id" in r.headers
        assert len(r.headers["x-request-id"]) > 0

    @pytest.mark.asyncio
    async def test_response_time_reasonable(self, client):
        """Lab 6: X-Response-Time should be < 5000ms for health check"""
        r = await client.get(f"{GATEWAY_URL}/health")
        ms = float(r.headers["x-response-time"].replace("ms", ""))
        assert ms < 5000


# ════════════════════════════════════════════════════════════════════════════
# 3. File lifecycle
# ════════════════════════════════════════════════════════════════════════════
class TestFileLifecycle:

    @pytest.mark.asyncio
    async def test_upload_process_analyze_delete(self, client, image_file):
        # 1. Upload
        fid = await upload(client, image_file)

        # 2. Process
        r = await client.post(f"{PROCESSING_URL}/process/{fid}",
                              json={"operation": "thumbnail", "parameters": {}})
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

        # 3. Analyze
        r = await client.post(f"{AI_URL}/analyze/{fid}",
                              json={"analysis_type": "vision", "confidence_threshold": 0.7})
        assert r.status_code == 200
        assert r.json()["confidence"] > 0

        # 4. Verify exists
        r = await client.get(f"{UPLOAD_URL}/upload/{fid}")
        assert r.status_code == 200

        # 5. Delete
        r = await client.delete(f"{UPLOAD_URL}/upload/{fid}")
        assert r.status_code == 200

        # 6. Confirm gone
        r = await client.get(f"{UPLOAD_URL}/upload/{fid}")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_processing_status_after_process(self, client, image_file):
        fid = await upload(client, image_file)
        await client.post(f"{PROCESSING_URL}/process/{fid}",
                          json={"operation": "thumbnail", "parameters": {}})
        r = await client.get(f"{PROCESSING_URL}/process/{fid}/status")
        assert r.status_code == 200
        assert r.json()["status"] == "processed"

    @pytest.mark.asyncio
    async def test_ai_analysis_history(self, client, image_file):
        fid = await upload(client, image_file)
        await client.post(f"{AI_URL}/analyze/{fid}",
                          json={"analysis_type": "general", "confidence_threshold": 0.7})
        r = await client.get(f"{AI_URL}/analyze/{fid}/history")
        assert r.status_code == 200
        assert "history" in r.json()


# ════════════════════════════════════════════════════════════════════════════
# 4. Batch operations
# ════════════════════════════════════════════════════════════════════════════
class TestBatchOperations:

    @pytest.mark.asyncio
    async def test_batch_process_and_analyze(self, client, image_file):
        fids = [await upload(client, image_file) for _ in range(3)]

        # Batch process
        r = await client.post(f"{PROCESSING_URL}/process/batch",
                              json={"file_ids": fids, "operation": "thumbnail"})
        assert r.status_code == 200
        pd = r.json()
        assert pd["total_files"] == 3
        assert pd["successful"] == 3

        # Batch analyze
        r = await client.post(f"{AI_URL}/analyze/batch",
                              json={"file_ids": fids, "analysis_type": "vision"})
        assert r.status_code == 200
        ad = r.json()
        assert ad["total_files"] == 3
        assert ad["successful"] == 3

    @pytest.mark.asyncio
    async def test_batch_unique_batch_ids(self, client, image_file):
        """Each batch call must return a unique batch_id"""
        fids = [await upload(client, image_file) for _ in range(2)]
        r1 = await client.post(f"{PROCESSING_URL}/process/batch",
                               json={"file_ids": fids, "operation": "thumbnail"})
        r2 = await client.post(f"{PROCESSING_URL}/process/batch",
                               json={"file_ids": fids, "operation": "resize"})
        assert r1.json()["batch_id"] != r2.json()["batch_id"]


# ════════════════════════════════════════════════════════════════════════════
# 5. Error handling
# ════════════════════════════════════════════════════════════════════════════
class TestErrorHandling:

    @pytest.mark.asyncio
    async def test_invalid_processing_operation(self, client, image_file):
        fid = await upload(client, image_file)
        r = await client.post(f"{PROCESSING_URL}/process/{fid}",
                              json={"operation": "unknown_op", "parameters": {}})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_ai_analysis_type(self, client, image_file):
        fid = await upload(client, image_file)
        r = await client.post(f"{AI_URL}/analyze/{fid}",
                              json={"analysis_type": "telepathy", "confidence_threshold": 0.7})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_nonexistent_file_returns_404(self, client):
        r = await client.get(f"{UPLOAD_URL}/upload/fake-id-9999")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self, client):
        r = await client.post(
            f"{GATEWAY_URL}/process-file",
            files={"file": ("../../../etc/passwd", b"evil", "text/plain")},
        )
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_blocked_extension_rejected(self, client):
        r = await client.post(
            f"{UPLOAD_URL}/upload",
            files={"file": ("bad.exe", b"MZ", "application/octet-stream")},
        )
        assert r.status_code == 415


# ════════════════════════════════════════════════════════════════════════════
# 6. Performance
# ════════════════════════════════════════════════════════════════════════════
class TestPerformance:

    @pytest.mark.asyncio
    async def test_full_workflow_under_30s(self, client, image_file):
        start = time.time()
        with open(image_file, "rb") as f:
            r = await client.post(
                f"{GATEWAY_URL}/process-file",
                files={"file": ("test_image.jpg", f, "image/jpeg")},
            )
        assert r.status_code == 200
        elapsed = time.time() - start
        assert elapsed < 30, f"Workflow took {elapsed:.1f}s — too slow"
        assert r.json()["total_time"] < 30

    @pytest.mark.asyncio
    async def test_concurrent_workflows(self, client, image_file):
        async def wf():
            with open(image_file, "rb") as f:
                return await client.post(
                    f"{GATEWAY_URL}/process-file",
                    files={"file": ("test_image.jpg", f, "image/jpeg")},
                )
        responses = await asyncio.gather(*[wf() for _ in range(3)])
        assert all(r.status_code == 200 for r in responses)
        # All workflow IDs must be unique
        wf_ids = [r.json()["workflow_id"] for r in responses]
        assert len(set(wf_ids)) == 3

    @pytest.mark.asyncio
    async def test_concurrent_uploads_unique_ids(self, client, image_file):
        async def up():
            with open(image_file, "rb") as f:
                r = await client.post(
                    f"{UPLOAD_URL}/upload",
                    files={"file": ("test_image.jpg", f, "image/jpeg")},
                )
            return r.json()["file_id"]
        ids = await asyncio.gather(*[up() for _ in range(5)])
        assert len(set(ids)) == 5   # no duplicates


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
