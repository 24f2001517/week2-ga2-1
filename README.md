# Orders API

FastAPI implementation of idempotent order creation, cursor pagination, and per-client rate limiting.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API docs: http://127.0.0.1:8000/docs

## Endpoints

- `GET /health`
- `POST /orders` with `Idempotency-Key: some-unique-key` and JSON body such as `{"item":"keyboard","quantity":1}`
- `GET /orders?limit=10` then pass the returned `next_cursor` as `cursor`
- Send `X-Client-Id` on requests to use independent rate-limit buckets.

## Assigned settings

- Fixed catalog IDs: 1 through 52
- Rate limit: 20 requests per client per rolling 10-second window
- CORS: wildcard origins, methods, and headers enabled for browser-based grading

## Deploy to Render

1. Push this folder to a Git repository.
2. In Render, create a **New Web Service** from the repository.
3. Render should detect `render.yaml`; use the build/start commands above.
4. After deployment, copy the service URL (for example, `https://your-service.onrender.com`) into the grader's **Deployed orders API base URL** field.

## Notes

The idempotency and rate-limit stores are in-memory, suitable for a single-process grading deployment. For multi-instance production deployment, move them to a shared database/Redis store. Rate-limit checks are applied to all non-OPTIONS requests, including health checks, so grader traffic should use distinct client IDs where required.
