#!/usr/bin/env python3
"""
Lab 7 — Service Startup Script
Adds: Redis + PostgreSQL readiness checks before starting services
"""
import subprocess, time, sys, signal, os, json, logging
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S')
logger = logging.getLogger("start_services")

GATEWAY_URL    = os.getenv("GATEWAY_URL",            "http://localhost:9000")
UPLOAD_URL     = os.getenv("UPLOAD_SERVICE_URL",     "http://localhost:8000")
PROCESSING_URL = os.getenv("PROCESSING_SERVICE_URL", "http://localhost:8001")
AI_URL         = os.getenv("AI_SERVICE_URL",         "http://localhost:8002")
REDIS_URL      = os.getenv("REDIS_URL",              "redis://localhost:6379/0")
DATABASE_URL   = os.getenv("DATABASE_URL",           "postgresql://lab7:lab7pass@localhost:5432/lab7db")
FRR_ENABLED    = os.getenv("FRR_ENABLED", "false").lower() == "true"


def signal_handler(sig, frame):
    logger.info("Shutdown signal received"); sys.exit(0)

signal.signal(signal.SIGINT,  signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


class ServiceManager:
    def __init__(self):
        self.services = [
            {"name": "Upload Service",     "port": int(os.getenv("UPLOAD_PORT", 8000)),
             "module": "upload_service:app",     "health_url": f"http://localhost:{os.getenv('UPLOAD_PORT',8000)}/health",     "process": None},
            {"name": "Processing Service", "port": int(os.getenv("PROCESSING_PORT", 8001)),
             "module": "processing_service:app", "health_url": f"http://localhost:{os.getenv('PROCESSING_PORT',8001)}/health", "process": None},
            {"name": "AI Service",         "port": int(os.getenv("AI_PORT", 8002)),
             "module": "ai_service:app",         "health_url": f"http://localhost:{os.getenv('AI_PORT',8002)}/health",         "process": None},
            {"name": "Gateway Service",    "port": int(os.getenv("GATEWAY_PORT", 9000)),
             "module": "gateway_service:app",    "health_url": f"http://localhost:{os.getenv('GATEWAY_PORT',9000)}/health",    "process": None},
        ]

    # ── Infrastructure readiness ───────────────────────────────────────────────
    def wait_for_redis(self, max_attempts: int = 20) -> bool:
        import redis
        url = REDIS_URL
        logger.info(f"Waiting for Redis at {url} ...")
        for i in range(1, max_attempts + 1):
            try:
                r = redis.from_url(url, socket_connect_timeout=2)
                r.ping(); logger.info(f"Redis ready (attempt {i})"); return True
            except Exception:
                time.sleep(min(1.5 ** i * 0.5, 8))
        logger.warning("Redis not ready — services will use filesystem fallback"); return False

    def wait_for_postgres(self, max_attempts: int = 20) -> bool:
        import asyncio, asyncpg
        url = DATABASE_URL
        logger.info(f"Waiting for PostgreSQL at {url} ...")
        async def _check():
            conn = await asyncpg.connect(url, timeout=3)
            await conn.fetchval("SELECT 1"); await conn.close()
        for i in range(1, max_attempts + 1):
            try:
                asyncio.run(_check()); logger.info(f"PostgreSQL ready (attempt {i})"); return True
            except Exception:
                time.sleep(min(1.5 ** i * 0.5, 8))
        logger.warning("PostgreSQL not ready — services will use filesystem fallback"); return False

    def check_ospf(self) -> bool:
        if not FRR_ENABLED:
            logger.info("FRR_ENABLED=false — skipping OSPF check"); return True
        try:
            result = subprocess.run(["vtysh", "-c", "show ip ospf neighbor json"],
                                    capture_output=True, text=True, timeout=5)
            import json as _json
            data   = _json.loads(result.stdout)
            full   = sum(1 for nbrs in data.get("neighbors", {}).values()
                         for n in nbrs if n.get("nbrState","").startswith("Full"))
            logger.info(f"OSPF: {full} neighbor(s) in FULL state"); return True
        except Exception as e:
            logger.warning(f"OSPF check: {e}"); return True

    # ── Service health ─────────────────────────────────────────────────────────
    def check_health(self, svc: dict) -> bool:
        import requests
        try:
            r = requests.get(svc["health_url"], timeout=5)
            return r.status_code == 200 and r.json().get("status") == "healthy"
        except Exception: return False

    def wait_for_service(self, svc: dict, max_attempts: int = 30) -> bool:
        logger.info(f"Waiting for {svc['name']} ...")
        delay = 1.0
        for i in range(1, max_attempts + 1):
            if self.check_health(svc):
                logger.info(f"{svc['name']} ready (attempt {i})"); return True
            if svc["process"] and svc["process"].poll() is not None:
                logger.error(f"{svc['name']} process died"); return False
            time.sleep(min(delay, 8.0)); delay *= 1.5
        logger.error(f"{svc['name']} not ready after {max_attempts} attempts"); return False

    def start_service(self, svc: dict) -> bool:
        logger.info(f"Starting {svc['name']} on port {svc['port']} ...")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", svc["module"],
                 "--host", "0.0.0.0", "--port", str(svc["port"])],
                cwd=Path(__file__).parent,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            svc["process"] = proc; return True
        except Exception as e:
            logger.error(f"Failed to start {svc['name']}: {e}"); return False

    # ── Start all ──────────────────────────────────────────────────────────────
    def start_all(self) -> bool:
        logger.info("=== Lab 7 Microservice Startup ===")
        os.chdir(Path(__file__).parent)

        # Infrastructure checks
        self.check_ospf()
        self.wait_for_redis()
        self.wait_for_postgres()

        for svc in self.services:
            if not self.start_service(svc): return False
            time.sleep(1)
            if not self.wait_for_service(svc): return False

        logger.info("All services ready"); return True

    def system_health(self) -> bool:
        import requests
        try:
            r = requests.get(f"{GATEWAY_URL}/health", timeout=10)
            if r.status_code != 200: return False
            d = r.json()
            logger.info(f"System status: {d['status']}")
            for name, info in d.get("services", {}).items():
                icon = "OK  " if info == "healthy" else "WARN"
                logger.info(f"  [{icon}] {name}: {info}")
            return d.get("status") == "healthy"
        except Exception as e:
            logger.error(f"System health: {e}"); return False

    def run_quick_test(self) -> bool:
        import requests
        logger.info("Running quick integration test ...")
        test_dir = Path("test_data"); test_dir.mkdir(exist_ok=True)
        test_file = test_dir / "sample.jpg"
        test_file.write_text("Lab 7 test file — Redis + PostgreSQL integration")
        try:
            with open(test_file, "rb") as f:
                resp = requests.post(
                    f"{GATEWAY_URL}/process-file",
                    files={"file": ("sample.jpg", f, "image/jpeg")},
                    data={"processing_options": json.dumps({
                        "enable_processing": True,  "processing_operation": "thumbnail",
                        "enable_ai_analysis": True, "ai_analysis_type": "vision",
                    })},
                    timeout=60, headers={"X-Request-ID": "quicktest-lab7"})
            if resp.status_code == 200:
                r = resp.json()
                logger.info(f"Quick test PASSED — wf={r['workflow_id']} "
                            f"time={r['total_time']:.2f}s storage={r.get('storage','?')}")
                return True
            logger.error(f"Quick test FAILED — {resp.status_code}: {resp.text}"); return False
        except Exception as e:
            logger.error(f"Quick test FAILED — {e}"); return False

    def print_status(self):
        for svc in self.services:
            ok = self.check_health(svc)
            logger.info(f"  {'UP  ' if ok else 'DOWN'} | {svc['name']:<22} :{svc['port']}")

    def cleanup(self):
        logger.info("Stopping all services ...")
        for svc in self.services:
            if svc["process"]:
                try: svc["process"].terminate(); svc["process"].wait(timeout=5)
                except Exception:
                    try: svc["process"].kill()
                    except Exception: pass
                logger.info(f"Stopped {svc['name']}")

    def run(self):
        if not self.start_all():
            logger.error("Startup failed"); self.cleanup(); sys.exit(1)

        self.system_health()
        self.run_quick_test()

        logger.info("=== Lab 7 Microservices Ready ===")
        logger.info(f"  Gateway:    {GATEWAY_URL}")
        logger.info(f"  Upload:     {UPLOAD_URL}")
        logger.info(f"  Processing: {PROCESSING_URL}")
        logger.info(f"  AI:         {AI_URL}")
        logger.info("Press Ctrl+C to stop")

        try:
            tick = 0
            while True:
                time.sleep(5); tick += 1
                if tick % 12 == 0: self.print_status()
        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()


def main():
    m = ServiceManager()
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        m.run_quick_test()
    else:
        m.run()


if __name__ == "__main__":
    main()
