# Design Decisions & Trade-offs

## 1. Database-Backed Queue vs External Message Broker

### Decision: PostgreSQL as Queue Backend

**Rationale:**
- Eliminates operational complexity of Redis/RabbitMQ
- ACID guarantees for job state transitions
- `SELECT FOR UPDATE SKIP LOCKED` provides atomic claiming
- Single source of truth for job state
- Simpler deployment (single database)

**Trade-offs:**
- Higher latency than in-memory brokers (~5-10ms per claim)
- Connection overhead per worker
- Database becomes hot spot at very high throughput (>10k jobs/sec)

**Mitigation:**
- Connection pooling (pgBouncer)
- Batch claiming (future)
- Read replicas for dashboard

### Alternative Considered: Redis + Celery
- Pros: Mature, high throughput, built-in retries
- Cons: Additional infrastructure, eventual consistency, complex debugging

## 2. ThreadPoolExecutor vs AsyncIO vs Multiprocessing

### Decision: ThreadPoolExecutor

**Rationale:**
- Jobs are I/O-bound (HTTP calls, DB queries, file operations)
- Python GIL released during I/O → true parallelism
- Simpler than async/await migration
- Compatible with synchronous Django ORM
- Lower memory than multiprocessing

**Trade-offs:**
- CPU-bound jobs block threads
- Limited by GIL for CPU work

**Mitigation:**
- Offload CPU work to subprocesses
- Use `concurrent.futures.ProcessPoolExecutor` for CPU tasks (future)

## 3. SKIP LOCKED and Advisory Locks for Concurrency

### Decision: PostgreSQL `SELECT FOR UPDATE SKIP LOCKED` for job claiming, plus advisory locks for scheduled job coordination

**Mechanism (Job Claiming):**
```sql
SELECT * FROM jobs 
WHERE status IN ('QUEUED', 'SCHEDULED') 
  AND scheduled_at <= NOW()
ORDER BY queue_priority DESC, scheduled_at ASC
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

**Mechanism (Scheduled Job Coordination):**
```python
class DistributedLock:
    def acquire(self):
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [self.lock_id])
```

**Why Both:**
- SKIP LOCKED: Ideal for job claiming — non-blocking, no deadlocks, workers skip locked rows
- Advisory locks: Ideal for preventing duplicate scheduled job creation across workers — application-level coordination without row locking
- Advisory locks are no-op on SQLite for development compatibility

**Trade-offs:**
- Advisory locks require manual release
- SQLite doesn't support advisory locks (handled gracefully in code)

## 4. Worker Heartbeat vs Push Notifications

### Decision: Polling Heartbeat (Worker → DB)

**Rationale:**
- Simple, reliable, no additional infrastructure
- Works across network partitions
- DB is single source of truth
- Easy to query worker health

**Trade-offs:**
- 5-minute detection window for dead workers
- Database write every ~1 second per worker

**Mitigation:**
- Configurable heartbeat interval
- Advisory lock for critical sections

## 5. Cron Scheduling via ScheduledJob Model

### Decision: Database-Driven Cron (Not Celery Beat)

**Mechanism:**
- `ScheduledJob` model with `cron_expression` and `next_run_at`
- Worker polls due scheduled jobs each loop
- Advisory lock prevents duplicate job creation
- Updates `next_run_at` after creating job

**Trade-offs:**
- Precision limited to poll interval (~1 second)
- Requires at least one running worker

**Alternative: Celery Beat**
- Pros: Precise, dedicated scheduler
- Cons: Additional process, Redis required

## 6. Retry Policy: Per-Job vs Per-Queue

### Decision: Hybrid (Queue Defaults, Job Overrides)

**Implementation:**
- Queue has `retry_policy` JSON (default config)
- Job has `max_retries`, `backoff_strategy`, `backoff_delay`
- Worker uses queue policy as fallback

**Rationale:**
- Flexibility for special jobs
- Consistent defaults per queue
- Easy to tune per queue type

## 7. Dead Letter Queue (DLQ) Design

### Decision: Separate DLQ Table with One-to-One Job Link

**Structure:**
- `DeadLetterQueue` has OneToOne to `Job`
- Job status = 'DLQ' when moved
- Original JobExecution preserved
- Manual retry via API resets job to QUEUED

**Why Not: Status Only**
- Need to store failure context (error, retry count, timestamp)
- Separate table enables DLQ-specific queries/indexes
- Resolution tracking (resolved_by, notes)

## 8. Batch Jobs: Single Record vs Individual Jobs

### Decision: Individual Jobs with Shared batch_id

**Implementation:**
- `BatchJob` tracks aggregate status
- Each job has `batch_id` FK
- Jobs execute independently
- Batch status computed from children

**Trade-offs:**
- More rows than single batch record
- But: independent retry, parallel execution, granular tracking

**Alternative: Single Batch Record**
- Pros: Atomic batch, simpler
- Cons: All-or-nothing, no partial retry, no parallelism

## 9. Workflow Dependencies: DAG vs Linear Chains

### Decision: General DAG (Directed Acyclic Graph)

**Implementation:**
- `WorkflowDependency` links Job → Depends_On
- No cycles enforced at application level
- Worker could check dependencies before claiming (future)

**Trade-offs:**
- More complex than linear chains
- Cycle detection needed
- Dependency resolution not yet implemented in worker

**Future:** Topological sort before claiming ready jobs

## 10. API Design: APIView vs ViewSet

### Decision: Explicit APIView Classes

**Rationale:**
- Full control over request/response
- Easy to understand and debug
- No "magic" routing
- Interview-friendly: every endpoint visible

**Trade-offs:**
- More boilerplate than ViewSet
- Manual pagination/filtering

**Alternative: ViewSet + Router**
- Pros: DRY, standard patterns
- Cons: Hidden logic, harder to customize

## 11. Authentication: API Key + Session Auth

### Decision: Dual authentication — API Key for API, Session for Web UI

**API Authentication:**
- `X-Project-Key` header with `ProjectKeyAuthentication`
- API keys generated via `secrets.token_urlsafe(32)` (43-character cryptographically secure strings)
- Per-project isolation at query level

**Web UI Authentication:**
- Django session-based (`@login_required`)
- Registration creates User → Organization → Project → Queue
- Page views filter by `organization__user=request.user`

**Rationale:**
- Machine-to-machine: API key is simple, no token refresh
- Web UI: Session auth integrates with Django's auth system
- User identity via `Organization.user` FK

**Trade-offs:**
- API keys have no built-in expiration
- No OAuth/JWT complexity needed for current use case

**Future:** Add JWT for user-facing dashboards if needed

## 12. Frontend: Server-Rendered + Vanilla JS vs SPA

### Decision: Django Templates + Vanilla JS Polling

**Rationale:**
- Zero build step (no Webpack/Vite)
- Works without Node.js in production
- SEO-friendly (though not needed for dashboard)
- Simple deployment (single process via Gunicorn)

**Trade-offs:**
- No reactive UI
- Polling overhead (5s interval)
- Limited interactivity

**Alternative: React/Vue SPA**
- Pros: Rich UX, reactive
- Cons: Build complexity, separate deploy, CORS

**Future:** WebSocket upgrade for live updates

## 13. Testing: LiveServerTestCase vs Unit Tests

### Decision: Integration Tests with TransactionTestCase

**Rationale:**
- Tests real HTTP stack
- Validates authentication, serialization, DB
- Catches integration bugs
- Runs against actual PostgreSQL (in CI)

**Trade-offs:**
- Slower than unit tests
- Requires database setup
- Flakier due to timing
- Concurrent claiming tests skipped on SQLite

**Complement:** Unit tests for pure logic (handlers, backoff calc)

## 14. Deployment: Single Process vs Microservices

### Decision: Monolithic Django App

**Rationale:**
- Simpler operations
- Shared database = ACID transactions
- No distributed tracing needed
- Easier debugging

**Trade-offs:**
- All components scale together
- Single point of failure
- Technology lock-in

**Future:** Extract worker as separate service if needed

## 15. Configuration: Environment Variables + Settings Package

### Decision: `dj-database-url` + `os.getenv()` + settings package

**Rationale:**
- 12-factor app compliant
- Works with Heroku/Railway/Render
- Clear separation of config from code
- Sensible defaults for development
- Settings package auto-selects module via `DJANGO_SETTINGS_MODULE`

**Implementation:**
- `manage.py` defaults to `distributed_job_scheduler.settings`
- `settings/__init__.py` reads env var and imports the appropriate module
- Falls back to `settings.local` if not set

## 16. Rate Limiting: Middleware-Based

### Decision: Django middleware with cache-based counters

**Implementation:**
- `RateLimitMiddleware` in `scheduler/middleware.py`
- Auth endpoints: Hardcoded 20 requests/minute/IP for `/login/` and `/register/`
- API endpoints: Configurable via `RateLimitRule` model (project + endpoint)
- Uses Django cache (`django.core.cache.cache`) for counters
- Returns `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` headers

**Rationale:**
- No additional infrastructure (uses existing Django cache)
- Simple to understand and maintain
- Configurable per-project per-endpoint
- Auth rate limiting protects against brute force

**Trade-offs:**
- Cache-based: counters reset on cache flush
- In-memory: not distributed across multiple web servers (unless using shared cache like Redis)

**Alternative: Django ratelimit decorator**
- Pros: Per-view control
- Cons: Less centralized, harder to configure globally

## 17. API Key Generation: UUID vs Cryptographic Random

### Decision: `secrets.token_urlsafe(32)`

**Implementation:**
- 32 bytes of randomness → 43-character URL-safe base64 string
- Generated during project creation (registration or web UI)
- Stored as-is in `Project.api_key` field

**Rationale:**
- `secrets` module is designed for cryptographic randomness
- URL-safe: can be used in headers without encoding issues
- 43 characters: sufficient entropy (256 bits) against brute force
- More standard than UUID format for API keys

**Trade-offs:**
- Not as recognizable as UUID format
- No built-in structure (UUIDs have version/variant bits)

**Alternative: UUID4**
- Pros: Recognizable format, built-in structure
- Cons: Lower entropy (122 random bits), hyphens cause issues in headers

## Summary Matrix

| Decision | Chosen | Alternative | Complexity | Scalability |
|----------|--------|-------------|------------|-------------|
| Queue Backend | PostgreSQL | Redis/RabbitMQ | Low | Medium |
| Concurrency | ThreadPool | AsyncIO/Process | Low | Medium |
| Claiming | SKIP LOCKED + Advisory Locks | Advisory/Redis only | Low | High |
| Scheduling | DB Polling | Celery Beat | Low | Medium |
| Retry Policy | Hybrid | Per-Job/Per-Queue | Medium | High |
| DLQ | Separate Table | Status Only | Low | High |
| Batch Jobs | Individual + batch_id | Single Record | Medium | High |
| Workflows | DAG | Linear | High | Medium |
| API Style | APIView | ViewSet | Medium | N/A |
| Auth | API Key + Session | JWT/OAuth | Low | High |
| Frontend | Templates + JS | SPA | Low | N/A |
| Testing | Integration | Unit | Medium | N/A |
| Architecture | Monolith | Microservices | Low | Medium |
| Rate Limiting | Middleware + Cache | Per-view decorator | Low | Medium |
| API Key Gen | secrets.token_urlsafe | UUID4 | Low | High |
