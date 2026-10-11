import json
import logging
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

EMAIL = "23f2001563@ds.study.iitm.ac.in"
START = time.monotonic()
MAX_LOGS = 1000
# Probes and scrapers are counted in metrics but kept out of the log buffer,
# so the log stays about real traffic.
UNLOGGED_PATHS = {"/metrics", "/healthz", "/logs/tail"}

request_count = 0
log_buffer: deque = deque(maxlen=MAX_LOGS)

# Structured logs: one JSON object per line on stdout (visible in Render's Logs tab).
logger = logging.getLogger("app")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_handler)
logger.propagate = False

app = FastAPI()


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@app.middleware("http")
async def observe(request: Request, call_next):
    global request_count
    request_count += 1                      # counts every request to every path

    rid = (request.headers.get("x-request-id") or "").strip() or str(uuid.uuid4())
    request.state.request_id = rid
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        response = JSONResponse(status_code=500, content={"detail": "internal error"})
    response.headers["X-Request-ID"] = rid

    if request.url.path not in UNLOGGED_PATHS:
        status = response.status_code
        level = "ERROR" if status >= 500 else "WARNING" if status >= 400 else "INFO"
        entry = {
            "ts": _ts(),
            "level": level,
            "msg": "request",
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "request_id": rid,
        }
        log_buffer.append(entry)
        logger.log(getattr(logging, level), json.dumps(entry))
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.get("/work")
def work(n: int = Query(1, ge=0, le=1_000_000)):
    checksum = 0
    for i in range(n):                      # n real units of work
        checksum = (checksum * 31 + i) % 1_000_003
    return {"email": EMAIL, "done": n}


@app.get("/metrics")
def metrics():
    body = (
        "# HELP http_requests_total Total HTTP requests handled.\n"
        "# TYPE http_requests_total counter\n"
        f"http_requests_total {request_count}\n"
    )
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "uptime_s": round(time.monotonic() - START, 3)}


@app.get("/logs/tail")
def logs_tail(limit: int = 20):
    limit = max(0, min(limit, MAX_LOGS))
    return list(log_buffer)[-limit:] if limit else []


@app.get("/")
def root():
    return {"status": "ok"}
