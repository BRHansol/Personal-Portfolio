"""
Lab 6 — Compliance Tests (upgraded from Phase 1)
Covers: health, upload, processing, AI, gateway + Lab 6 security/observability checks
"""

import pytest
import httpx
import asyncio
import json
from pathlib import Path
import time
import os
from dotenv import load_dotenv

load_dotenv()

# ── Service URLs (Lab 6 ports) ────────────────────────────────────────────────
GATEWAY_URL    = os.getenv("GATEWAY_URL",            "http://localhost:9000")
UPLOAD_URL     = os.getenv("UPLOAD_SERVICE_URL",     "http://localhost:8000")
PROCESSING_URL = os.getenv("PROCESSING_SERVICE_URL", "http://localhost:8001")
AI_URL         = os.getenv("AI_SERVICE_URL",         "http://localhost:8002")

TEST_DATA_DIR = Path("test_data")
TEST_DATA_DIR.mkdir(exist_ok=True)


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture
def test_file():
    f = TEST_DATA_DIR / "test_image.jpg"
    f.write_text("Mock JPEG content for Lab 6 testing")
    return f

@pytest.fixture
def test_doc():
    f = TEST_DATA_DIR / "test_doc.txt"
    f.write_text("Sample text document for NLP analysis")
    return f

@pytest.fixture
async def client():
    async with httpx.AsyncClient(timeout=60.0) as c:
        yield c


# ════════════════════════════════════════════════════════════════════════════
# 1. Health checks
# ════════════════════════════════════════════════════════════════════════════
class TestHealth:

    @pytest.mark.asyncio
    async def test_upload_health(self, client):
        r = await client.get(f"{UPLOAD_URL}/health")
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "healthy"
        assert d["service"] == "upload-service"
        assert "version" in d          # Lab 6: version field added
        assert "timestamp" in d

    @pytest.mark.asyncio
    async def test_processing_health(self, client):
        r = await client.get(f"{PROCESSING_URL}/health")
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "healthy"
        assert d["service"] == "processing-service"
        assert "version" in d

    @pytest.mark.asyncio
    async def test_ai_health(self, client):
        r = await client.get(f"{AI_URL}/health")
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "healthy"
        assert d["service"] == "ai-service"
        assert "version" in d

    @pytest.mark.asyncio
    async def test_gateway_health(self, client):
        r = await client.get(f"{GATEWAY_URL}/health")
        assert r.status_code == 200
        d = r.json()
        assert d["status"] in ["healthy", "degraded"]
        assert "services" in d
        for svc in ["upload", "processing", "ai"]:
            assert svc in d["services"]


# ════════════════════════════════════════════════════════════════════════════
# 2. Upload service
# ════════════════════════════════════════════════════════════════════════════
class TestUpload:

    @pytest.mark.asyncio
    async def test_upload_success(self, client, test_file):
        with open(test_file, "rb") as f:
            r = await client.post(f"{UPLOAD_URL}/upload",
                                  files={"file": ("test_image.jpg", f, "image/jpeg")})
        assert r.status_code == 200
        d = r.json()
        assert "file_id" in d
        assert d["filename"] == "test_image.jpg"
        assert d["status"] == "uploaded"
        assert d["mime_type"] == "image/jpeg"
        return d["file_id"]

    @pytest.mark.asyncio
    async def test_upload_returns_request_id_header(self, client, test_file):
        """Lab 6: every response must carry X-Request-ID"""
        with open(test_file, "rb") as f:
            r = await client.post(f"{UPLOAD_URL}/upload",
                                  files={"file": ("test_image.jpg", f, "image/jpeg")})
        assert "x-request-id" in r.headers
        assert "x-response-time" in r.headers

    @pytest.mark.asyncio
    async def test_upload_request_id_echo(self, client, test_file):
        """Lab 6: X-Request-ID sent in should be echoed back"""
        with open(test_file, "rb") as f:
            r = await client.post(
                f"{UPLOAD_URL}/upload",
                files={"file": ("test_image.jpg", f, "image/jpeg")},
                headers={"X-Request-ID": "mytest-001"},
            )
        assert r.headers.get("x-request-id") == "mytest-001"

    @pytest.mark.asyncio
    async def test_upload_blocked_extension(self, client):
        """Lab 6: .exe must be rejected with 415"""
        r = await client.post(
            f"{UPLOAD_URL}/upload",
            files={"file": ("malware.exe", b"MZ", "application/octet-stream")},
        )
        assert r.status_code == 415

    @pytest.mark.asyncio
    async def test_upload_large_file_rejected(self, client):
        r = await client.post(
            f"{UPLOAD_URL}/upload",
            files={"file": ("big.jpg", b"x" * (11 * 1024 * 1024), "image/jpeg")},
        )
        assert r.status_code == 413

    @pytest.mark.asyncio
    async def test_upload_no_file(self, client):
        r = await client.post(f"{UPLOAD_URL}/upload")
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_upload_metadata_retrieval(self, client, test_file):
        file_id = await TestUpload().test_upload_success(client, test_file)
        r = await client.get(f"{UPLOAD_URL}/upload/{file_id}")
        assert r.status_code == 200
        assert r.json()["file_id"] == file_id

    @pytest.mark.asyncio
    async def test_upload_delete(self, client, test_file):
        file_id = await TestUpload().test_upload_success(client, test_file)
        r = await client.delete(f"{UPLOAD_URL}/upload/{file_id}")
        assert r.status_code == 200
        r2 = await client.get(f"{UPLOAD_URL}/upload/{file_id}")
        assert r2.status_code == 404

    @pytest.mark.asyncio
    async def test_upload_nonexistent(self, client):
        r = await client.get(f"{UPLOAD_URL}/upload/does-not-exist-xyz")
        assert r.status_code == 404


# ════════════════════════════════════════════════════════════════════════════
# 3. Processing service
# ════════════════════════════════════════════════════════════════════════════
class TestProcessing:

    async def _upload(self, client, test_file) -> str:
        with open(test_file, "rb") as f:
            r = await client.post(f"{UPLOAD_URL}/upload",
                                  files={"file": ("test_image.jpg", f, "image/jpeg")})
        return r.json()["file_id"]

    @pytest.mark.asyncio
    async def test_process_thumbnail(self, client, test_file):
        fid = await self._upload(client, test_file)
        r = await client.post(f"{PROCESSING_URL}/process/{fid}",
                              json={"operation": "thumbnail", "parameters": {}})
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "completed"
        assert d["operation"] == "thumbnail"
        assert "processing_time" in d

    @pytest.mark.asyncio
    async def test_process_invalid_operation(self, client, test_file):
        """Lab 6: unsupported operation → 400"""
        fid = await self._upload(client, test_file)
        r = await client.post(f"{PROCESSING_URL}/process/{fid}",
                              json={"operation": "explode", "parameters": {}})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_process_returns_request_id(self, client, test_file):
        fid = await self._upload(client, test_file)
        r = await client.post(f"{PROCESSING_URL}/process/{fid}",
                              json={"operation": "thumbnail", "parameters": {}},
                              headers={"X-Request-ID": "proc-test-01"})
        assert r.headers.get("x-request-id") == "proc-test-01"

    @pytest.mark.asyncio
    async def test_process_operations_list(self, client):
        """Lab 6: returns set/list of operation names"""
        r = await client.get(f"{PROCESSING_URL}/process/operations")
        assert r.status_code == 200
        d = r.json()
        assert "operations" in d
        ops = d["operations"]
        # Lab 6 returns a list of strings (set)
        assert "thumbnail" in ops

    @pytest.mark.asyncio
    async def test_batch_processing(self, client, test_file):
        fids = []
        for _ in range(3):
            fids.append(await self._upload(client, test_file))
        r = await client.post(f"{PROCESSING_URL}/process/batch",
                              json={"file_ids": fids, "operation": "thumbnail"})
        assert r.status_code == 200
        d = r.json()
        assert d["total_files"] == 3
        assert d["successful"] > 0


# ════════════════════════════════════════════════════════════════════════════
# 4. AI service
# ════════════════════════════════════════════════════════════════════════════
class TestAI:

    async def _upload(self, client, test_file) -> str:
        with open(test_file, "rb") as f:
            r = await client.post(f"{UPLOAD_URL}/upload",
                                  files={"file": ("test_image.jpg", f, "image/jpeg")})
        return r.json()["file_id"]

    @pytest.mark.asyncio
    async def test_analyze_general(self, client, test_file):
        fid = await self._upload(client, test_file)
        r = await client.post(f"{AI_URL}/analyze/{fid}",
                              json={"analysis_type": "general", "confidence_threshold": 0.7})
        assert r.status_code == 200
        d = r.json()
        assert d["file_id"] == fid
        assert 0.0 <= d["confidence"] <= 1.0
        assert "request_id" in d        # Lab 6: request_id in response body

    @pytest.mark.asyncio
    async def test_analyze_invalid_type(self, client, test_file):
        """Lab 6: invalid analysis_type → 400"""
        fid = await self._upload(client, test_file)
        r = await client.post(f"{AI_URL}/analyze/{fid}",
                              json={"analysis_type": "psychic", "confidence_threshold": 0.7})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_analyze_returns_request_id_header(self, client, test_file):
        fid = await self._upload(client, test_file)
        r = await client.post(f"{AI_URL}/analyze/{fid}",
                              json={"analysis_type": "general", "confidence_threshold": 0.7},
                              headers={"X-Request-ID": "ai-test-99"})
        assert r.headers.get("x-request-id") == "ai-test-99"

    @pytest.mark.asyncio
    async def test_models_list(self, client):
        r = await client.get(f"{AI_URL}/models")
        assert r.status_code == 200
        d = r.json()
        assert "models" in d
        for m in d["models"]:
            assert "name" in m
            assert "type" in m
            assert "accuracy" in m     # Lab 6: accuracy field added

    @pytest.mark.asyncio
    async def test_batch_analysis(self, client, test_file):
        fids = [await self._upload(client, test_file) for _ in range(2)]
        r = await client.post(f"{AI_URL}/analyze/batch",
                              json={"file_ids": fids, "analysis_type": "general"})
        assert r.status_code == 200
        d = r.json()
        assert d["total_files"] == 2
        assert d["successful"] > 0


# ════════════════════════════════════════════════════════════════════════════
# 5. Gateway — workflow
# ════════════════════════════════════════════════════════════════════════════
class TestGateway:

    @pytest.mark.asyncio
    async def test_full_workflow(self, client, test_file):
        with open(test_file, "rb") as f:
            r = await client.post(
                f"{GATEWAY_URL}/process-file",
                files={"file": ("test_image.jpg", f, "image/jpeg")},
                data={"processing_options": json.dumps({
                    "enable_processing": True,  "processing_operation": "thumbnail",
                    "enable_ai_analysis": True, "ai_analysis_type": "general",
                })},
            )
        assert r.status_code == 200
        d = r.json()
        assert "workflow_id" in d
        assert "request_id" in d       # Lab 6: request_id in WorkflowResponse
        assert d["upload_status"] == "completed"
        assert d["processing_status"] == "completed"
        assert d["ai_analysis_status"] == "completed"
        assert d["total_time"] < 30

    @pytest.mark.asyncio
    async def test_workflow_skipped_steps(self, client, test_file):
        with open(test_file, "rb") as f:
            r = await client.post(
                f"{GATEWAY_URL}/process-file",
                files={"file": ("test_image.jpg", f, "image/jpeg")},
                data={"processing_options": json.dumps({
                    "enable_processing": False, "enable_ai_analysis": False,
                })},
            )
        assert r.status_code == 200
        d = r.json()
        assert d["processing_status"] == "skipped"
        assert d["ai_analysis_status"] == "skipped"

    @pytest.mark.asyncio
    async def test_gateway_propagates_request_id(self, client, test_file):
        """Lab 6: X-Request-ID sent to gateway must appear in response header"""
        with open(test_file, "rb") as f:
            r = await client.post(
                f"{GATEWAY_URL}/process-file",
                files={"file": ("test_image.jpg", f, "image/jpeg")},
                headers={"X-Request-ID": "gw-trace-42"},
            )
        assert r.headers.get("x-request-id") == "gw-trace-42"

    @pytest.mark.asyncio
    async def test_gateway_invalid_filename(self, client):
        """Lab 6: path-traversal filename must be rejected"""
        r = await client.post(
            f"{GATEWAY_URL}/process-file",
            files={"file": ("../../etc/passwd", b"evil", "text/plain")},
        )
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_upload_only(self, client, test_file):
        with open(test_file, "rb") as f:
            r = await client.post(f"{GATEWAY_URL}/upload-only",
                                  files={"file": ("test_image.jpg", f, "image/jpeg")})
        assert r.status_code == 200
        assert "file_id" in r.json()

    @pytest.mark.asyncio
    async def test_process_existing(self, client, test_file):
        with open(test_file, "rb") as f:
            up = await client.post(f"{UPLOAD_URL}/upload",
                                   files={"file": ("test_image.jpg", f, "image/jpeg")})
        fid = up.json()["file_id"]
        r = await client.post(f"{GATEWAY_URL}/process-existing/{fid}",
                              json={"enable_processing": True,
                                    "processing_operation": "thumbnail",
                                    "enable_ai_analysis": False})
        assert r.status_code == 200
        assert "processing" in r.json()["results"]

    @pytest.mark.asyncio
    async def test_workflow_performance(self, client, test_file):
        start = time.time()
        with open(test_file, "rb") as f:
            r = await client.post(f"{GATEWAY_URL}/process-file",
                                  files={"file": ("test_image.jpg", f, "image/jpeg")})
        assert r.status_code == 200
        assert (time.time() - start) < 30


# ════════════════════════════════════════════════════════════════════════════
# 6. Lab 6 Security tests
# ════════════════════════════════════════════════════════════════════════════
class TestLab6Security:

    @pytest.mark.asyncio
    async def test_rate_limit_header_present(self, client, test_file):
        """Gateway should respond normally within rate limit"""
        with open(test_file, "rb") as f:
            r = await client.post(f"{GATEWAY_URL}/upload-only",
                                  files={"file": ("test_image.jpg", f, "image/jpeg")})
        # Below limit → 200 OK
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_response_time_header(self, client):
        """Lab 6: X-Response-Time header must be present on all responses"""
        r = await client.get(f"{GATEWAY_URL}/health")
        assert "x-response-time" in r.headers
        # Value should be numeric ms, e.g. "12.3ms"
        val = r.headers["x-response-time"].replace("ms", "")
        assert float(val) >= 0

    @pytest.mark.asyncio
    async def test_blocked_file_extension_at_gateway(self, client):
        """Lab 6: .exe blocked at upload service — gateway returns 415"""
        r = await client.post(
            f"{GATEWAY_URL}/upload-only",
            files={"file": ("virus.exe", b"MZ", "application/octet-stream")},
        )
        assert r.status_code == 415

    @pytest.mark.asyncio
    async def test_cors_header_present(self, client):
        """Lab 6: CORS middleware should respond to OPTIONS"""
        r = await client.options(
            f"{GATEWAY_URL}/health",
            headers={"Origin": "http://localhost:3000",
                     "Access-Control-Request-Method": "GET"},
        )
        # Should not return 405 (method not allowed) — CORS middleware handles it
        assert r.status_code in [200, 204]


# ════════════════════════════════════════════════════════════════════════════
# 7. Concurrency
# ════════════════════════════════════════════════════════════════════════════
class TestConcurrency:

    @pytest.mark.asyncio
    async def test_concurrent_uploads(self, client, test_file):
        async def do_upload():
            with open(test_file, "rb") as f:
                r = await client.post(f"{UPLOAD_URL}/upload",
                                      files={"file": ("test_image.jpg", f, "image/jpeg")})
            return r
        responses = await asyncio.gather(*[do_upload() for _ in range(5)])
        assert all(r.status_code == 200 for r in responses)
        # All file_ids must be unique
        ids = [r.json()["file_id"] for r in responses]
        assert len(set(ids)) == 5

    @pytest.mark.asyncio
    async def test_concurrent_workflows(self, client, test_file):
        async def do_workflow():
            with open(test_file, "rb") as f:
                return await client.post(
                    f"{GATEWAY_URL}/process-file",
                    files={"file": ("test_image.jpg", f, "image/jpeg")},
                )
        responses = await asyncio.gather(*[do_workflow() for _ in range(3)])
        assert all(r.status_code == 200 for r in responses)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
