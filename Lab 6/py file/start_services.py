#!/usr/bin/env python3
"""
Lab 6 — Service Startup Script (upgraded)
Adds: health retry with backoff, OSPF/FRR readiness check, structured logging
"""

import subprocess
import time
import sys
import signal
import os
import json
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Structured logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S'
)
logger = logging.getLogger("start_services")

# ── URLs from env ─────────────────────────────────────────────────────────────
GATEWAY_URL    = os.getenv("GATEWAY_URL",            "http://localhost:9000")
UPLOAD_URL     = os.getenv("UPLOAD_SERVICE_URL",     "http://localhost:8000")
PROCESSING_URL = os.getenv("PROCESSING_SERVICE_URL", "http://localhost:8001")
AI_URL         = os.getenv("AI_SERVICE_URL",         "http://localhost:8002")

FRR_ENABLED = os.getenv("FRR_ENABLED", "false").lower() == "true"


def signal_handler(sig, frame):
    logger.info("Shutdown signal received")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


class ServiceManager:
    def __init__(self):
        self.services = [
            {
                "name":       "Upload Service",
                "port":       int(os.getenv("UPLOAD_PORT", 8000)),
                "module":     "services.upload.app.main:app",
                "health_url": f"http://localhost:{os.getenv('UPLOAD_PORT', 8000)}/health",
                "process":    None,
            },
            {
                "name":       "Processing Service",
                "port":       int(os.getenv("PROCESSING_PORT", 8001)),
                "module":     "services.processing.app.main:app",
                "health_url": f"http://localhost:{os.getenv('PROCESSING_PORT', 8001)}/health",
                "process":    None,
            },
            {
                "name":       "AI Service",
                "port":       int(os.getenv("AI_PORT", 8002)),
                "module":     "services.ai.app.main:app",
                "health_url": f"http://localhost:{os.getenv('AI_PORT', 8002)}/health",
                "process":    None,
            },
            {
                "name":       "Gateway Service",
                "port":       int(os.getenv("GATEWAY_PORT", 9000)),
                "module":     "services.gateway.app.main:app",
                "health_url": f"http://localhost:{os.getenv('GATEWAY_PORT', 9000)}/health",
                "process":    None,
            },
        ]

    # ── Health check with exponential backoff ────────────────────────────────
    def check_health(self, service: dict) -> bool:
        try:
            import requests
            r = requests.get(service["health_url"], timeout=5)
            return r.status_code == 200 and r.json().get("status") == "healthy"
        except Exception:
            return False

    def wait_for_service(self, service: dict, max_attempts: int = 30) -> bool:
        logger.info(f"Waiting for {service['name']} ...")
        delay = 1.0
        for attempt in range(1, max_attempts + 1):
            if self.check_health(service):
                logger.info(f"{service['name']} is ready (attempt {attempt})")
                return True
            if service["process"] and service["process"].poll() is not None:
                logger.error(f"{service['name']} process died")
                return False
            time.sleep(min(delay, 8.0))   # cap at 8s
            delay *= 1.5                   # exponential backoff
        logger.error(f"{service['name']} did not become ready after {max_attempts} attempts")
        return False

    # ── OSPF / FRRouting readiness ───────────────────────────────────────────
    def check_ospf_ready(self) -> bool:
        if not FRR_ENABLED:
            logger.info("FRR_ENABLED=false — skipping OSPF check")
            return True
        logger.info("Checking OSPF neighbor state ...")
        try:
            result = subprocess.run(
                ["vtysh", "-c", "show ip ospf neighbor json"],
                capture_output=True, text=True, timeout=5
            )
            data = json.loads(result.stdout)
            neighbors = data.get("neighbors", {})
            full_count = sum(
                1 for nbr_list in neighbors.values()
                for nbr in nbr_list
                if nbr.get("nbrState", "").startswith("Full")
            )
            if full_count > 0:
                logger.info(f"OSPF ready — {full_count} neighbor(s) in FULL state")
                return True
            logger.warning("OSPF neighbors not yet FULL — continuing anyway")
            return True  # non-blocking; network may come up after services
        except FileNotFoundError:
            logger.warning("vtysh not found — skipping OSPF check")
            return True
        except Exception as e:
            logger.warning(f"OSPF check failed: {e} — continuing")
            return True

    # ── Start a single service ───────────────────────────────────────────────
    def start_service(self, service: dict) -> bool:
        logger.info(f"Starting {service['name']} on port {service['port']} ...")
        cmd = [sys.executable, "-m", "uvicorn", service["module"],
               "--host", "0.0.0.0", "--port", str(service["port"])]
        try:
            proc = subprocess.Popen(cmd, cwd=Path.cwd(),
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            service["process"] = proc
            return True
        except Exception as e:
            logger.error(f"Failed to start {service['name']}: {e}")
            return False

    # ── Start all services ───────────────────────────────────────────────────
    def start_all(self) -> bool:
        logger.info("=== Lab 6 Microservice Startup ===")
        os.chdir(Path(__file__).parent)

        self.check_ospf_ready()

        for svc in self.services:
            if not self.start_service(svc):
                return False
            time.sleep(1)
            if not self.wait_for_service(svc):
                return False

        logger.info("All services are running")
        return True

    # ── System-wide health check ─────────────────────────────────────────────
    def system_health(self) -> bool:
        import requests
        try:
            r = requests.get(f"{GATEWAY_URL}/health", timeout=10)
            if r.status_code != 200:
                return False
            data = r.json()
            logger.info(f"System status: {data['status']}")
            for name, info in data.get("services", {}).items():
                status = info.get("status", "unknown")
                icon = "OK" if status == "healthy" else "WARN"
                logger.info(f"  [{icon}] {name}: {status}")
            return data.get("status") == "healthy"
        except Exception as e:
            logger.error(f"System health check failed: {e}")
            return False

    # ── Quick integration test ───────────────────────────────────────────────
    def run_quick_test(self) -> bool:
        import requests
        logger.info("Running quick integration test ...")
        test_dir = Path("test_data")
        test_dir.mkdir(exist_ok=True)
        test_file = test_dir / "sample.jpg"
        test_file.write_text("Lab 6 test file — resilient microservices")

        try:
            with open(test_file, "rb") as f:
                resp = requests.post(
                    f"{GATEWAY_URL}/process-file",
                    files={"file": ("sample.jpg", f, "image/jpeg")},
                    data={"processing_options": json.dumps({
                        "enable_processing":    True,
                        "processing_operation": "thumbnail",
                        "enable_ai_analysis":   True,
                        "ai_analysis_type":     "vision",
                    })},
                    timeout=60,
                    headers={"X-Request-ID": "quicktest-lab6"},
                )
            if resp.status_code == 200:
                result = resp.json()
                logger.info(f"Quick test PASSED — workflow={result['workflow_id']} "
                            f"time={result['total_time']:.2f}s rid={result['request_id']}")
                return True
            else:
                logger.error(f"Quick test FAILED — {resp.status_code}: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Quick test FAILED — {e}")
            return False

    # ── Periodic status ──────────────────────────────────────────────────────
    def print_status(self):
        for svc in self.services:
            ok = self.check_health(svc)
            logger.info(f"  {'UP  ' if ok else 'DOWN'} | {svc['name']:<22} :{svc['port']}")

    # ── Cleanup ──────────────────────────────────────────────────────────────
    def cleanup(self):
        logger.info("Stopping all services ...")
        for svc in self.services:
            if svc["process"]:
                try:
                    svc["process"].terminate()
                    svc["process"].wait(timeout=5)
                    logger.info(f"Stopped {svc['name']}")
                except Exception:
                    try:
                        svc["process"].kill()
                    except Exception:
                        pass

    # ── Main interactive loop ─────────────────────────────────────────────────
    def run(self):
        if not self.start_all():
            logger.error("Startup failed")
            self.cleanup()
            sys.exit(1)

        self.system_health()
        self.run_quick_test()

        logger.info("=== Lab 6 Microservices Ready ===")
        logger.info(f"  Gateway:    {GATEWAY_URL}")
        logger.info(f"  Upload:     {UPLOAD_URL}")
        logger.info(f"  Processing: {PROCESSING_URL}")
        logger.info(f"  AI:         {AI_URL}")
        logger.info("Press Ctrl+C to stop")

        try:
            tick = 0
            while True:
                time.sleep(5)
                tick += 1
                if tick % 12 == 0:   # print status every ~60s
                    self.print_status()
        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()


def main():
    manager = ServiceManager()
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        manager.run_quick_test()
    else:
        manager.run()


if __name__ == "__main__":
    main()
