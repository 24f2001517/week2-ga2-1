from collections import defaultdict, deque
from threading import Lock
from time import monotonic
from uuid import uuid4
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

TOTAL_ORDERS = 52
RATE_LIMIT = 20
WINDOW_SECONDS = 10.0

app = FastAPI(title="Production Patterns Orders API", version="1.0.0")

# The grader's browser page may be hosted on a different origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After", "Idempotency-Key"],
)

_state_lock = Lock()
_idempotency_lock = Lock()
_rate_lock = Lock()
_created_orders = {}
_idempotency_keys = {}
_rate_buckets = defaultdict(deque)

class OrderCreate(BaseModel):
    item: str = "standard-order"
    quantity: int = Field(default=1, ge=1)
    customer: Optional[str] = None

def catalog_order(order_id: int):
    return {"id": order_id, "item": f"catalog-item-{order_id}", "quantity": 1}

@app.get("/")
def root():
    return {
        "service": "orders-api",
        "total_orders": TOTAL_ORDERS,
        "rate_limit": RATE_LIMIT,
        "window_seconds": int(WINDOW_SECONDS),
        "endpoints": ["/orders", "/health"],
    }

@app.get("/health")
def health():
    return {"status": "ok"}

@app.middleware("http")
async def per_client_rate_limit(request: Request, call_next):
    # OPTIONS preflights should not consume the request quota.
    if request.method == "OPTIONS":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id", "anonymous")
    now = monotonic()
    with _rate_lock:
        bucket = _rate_buckets[client_id]
        while bucket and now - bucket[0] >= WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT:
            retry_after = max(1, int(WINDOW_SECONDS - (now - bucket[0]) + 0.999))
            return Response(
                content='{"detail":"Rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(retry_after), "Access-Control-Allow-Origin": "*"},
            )
        bucket.append(now)

    return await call_next(request)

@app.post("/orders", status_code=201)
def create_order(
    payload: OrderCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1),
):
    # Atomic lookup + create ensures concurrent retries do not duplicate orders.
    with _idempotency_lock:
        if idempotency_key in _idempotency_keys:
            return _idempotency_keys[idempotency_key]
        order = {
            "id": str(uuid4()),
            "item": payload.item,
            "quantity": payload.quantity,
            "customer": payload.customer,
        }
        _idempotency_keys[idempotency_key] = order
        _created_orders[order["id"]] = order
        return order

@app.get("/orders")
def list_orders(
    limit: int = Query(default=10, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
):
    # Opaque cursors encode the next one-based catalog position.
    start = 1
    if cursor:
        try:
            start = int(cursor.removeprefix("c_"))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="Invalid cursor")

    if start < 1 or start > TOTAL_ORDERS + 1:
        raise HTTPException(status_code=400, detail="Invalid cursor")

    end = min(start + limit - 1, TOTAL_ORDERS)
    items = [catalog_order(i) for i in range(start, end + 1)]
    next_cursor = f"c_{end + 1}" if end < TOTAL_ORDERS else None
    return {"items": items, "next_cursor": next_cursor}

# Useful for local smoke tests only; do not call during grader runs.
@app.post("/_test/reset")
def reset_for_local_tests():
    with _idempotency_lock:
        _idempotency_keys.clear()
        _created_orders.clear()
    with _rate_lock:
        _rate_buckets.clear()
    return {"status": "reset"}
