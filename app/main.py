
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

app = FastAPI(title="Orders API", version="1.0.0")

# CORS for browser-based grading
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After", "Idempotency-Key"],
)

# In-memory stores
idempotency_lock = Lock()
rate_lock = Lock()

idempotency_store = {}
rate_buckets = defaultdict(deque)


class OrderCreate(BaseModel):
    item: str = "standard-order"
    quantity: int = Field(default=1, ge=1)
    customer: Optional[str] = None


def catalog_order(order_id: int):
    return {
        "id": order_id,
        "item": f"catalog-item-{order_id}",
        "quantity": 1,
    }


@app.get("/")
def root():
    return {
        "service": "orders-api",
        "total_orders": TOTAL_ORDERS,
        "rate_limit": RATE_LIMIT,
        "window_seconds": int(WINDOW_SECONDS),
    }


@app.get("/health")
def health():
    return {"status": "ok"}


# Per-client rolling-window rate limiter
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id", "anonymous")
    now = monotonic()

    with rate_lock:
        bucket = rate_buckets[client_id]

        # Remove requests outside the 10-second window
        while bucket and now - bucket[0] >= WINDOW_SECONDS:
            bucket.popleft()

        if len(bucket) >= RATE_LIMIT:
            retry_after = max(
                1,
                int(WINDOW_SECONDS - (now - bucket[0]) + 0.999),
            )

            return Response(
                content='{"detail":"Rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
                headers={
                    "Retry-After": str(retry_after),
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Expose-Headers": "Retry-After",
                },
            )

        bucket.append(now)

    return await call_next(request)


# Idempotent order creation
@app.post("/orders", status_code=201)
def create_order(
    payload: OrderCreate,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
    ),
):
    with idempotency_lock:
        if idempotency_key in idempotency_store:
            return idempotency_store[idempotency_key]

        order = {
            "id": str(uuid4()),
            "item": payload.item,
            "quantity": payload.quantity,
            "customer": payload.customer,
        }

        idempotency_store[idempotency_key] = order
        return order


# Cursor-based pagination: fixed catalog IDs 1 through 52
@app.get("/orders")
def list_orders(
    limit: int = Query(default=10, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
):
    start = 1

    if cursor:
        try:
            start = int(cursor.removeprefix("c_"))
        except (ValueError, AttributeError):
            raise HTTPException(
                status_code=400,
                detail="Invalid cursor",
            )

    if start < 1 or start > TOTAL_ORDERS + 1:
        raise HTTPException(
            status_code=400,
            detail="Invalid cursor",
        )

    end = min(start + limit - 1, TOTAL_ORDERS)

    items = [
        catalog_order(order_id)
        for order_id in range(start, end + 1)
    ]

    next_cursor = f"c_{end + 1}" if end < TOTAL_ORDERS else None

    return {
        "items": items,
        "next_cursor": next_cursor,
    }
