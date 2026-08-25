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

## 3. SKIP LOCKED for Atomic Claiming

### Decision: PostgreSQL `SELECT FOR UPDATE SKIP LOCKED`

**Mechanism:**
```sql
SELECT * FROM jobs 
WHERE status IN ('QUEUED', 'SCHEDULED') 
  AND scheduled_at <= NOW()
ORDER BY queue_priority DESC, scheduled_at ASC
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

**Why It Works:**
- Locks only the claimed row
- Other workers skip locked rows instantly
- No blocking, no deadlocks
- Guarantees exactly-once execution

**Alternative: Advisory Locks**
- Pros: Application-level, more flexible
- Cons: Manual cleanup on crash, more complex

**Alternative: Redis SETNX**
- Pros: Fast, simple
- Cons: Requires Redis, separate system

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

## 11. Authentication: API Key vs JWT vs OAuth

### Decision: Simple API Key (X-Project-Key Header)

**Rationale:**
- Machine-to-machine primary use case
- No token refresh complexity
- Easy to rotate/revoke
- Per-project isolation

**Trade-offs:**
- No user-level identity
- No built-in expiration
- No standard token format

**Future:** Add JWT for user-facing dashboards

## 12. Frontend: Server-Rendered + Vanilla JS vs SPA

### Decision: Django Templates + Vanilla JS Polling

**Rationale:**
- Zero build step (no Webpack/Vite)
- Works without Node.js in production
- SEO-friendly (though not needed for dashboard)
- Simple deployment (single Docker image)

**Trade-offs:**
- No reactive UI
- Polling overhead (5s interval)
- Limited interactivity

**Alternative: React/Vue SPA**
- Pros: Rich UX, reactive
- Cons: Build complexity, separate deploy, CORS

**Future:** WebSocket upgrade for live updates

## 13. Testing: LiveServerTestCase vs Unit Tests

### Decision: Integration Tests with Live Server

**Rationale:**
- Tests real HTTP stack
- Validates authentication, serialization, DB
- Catches integration bugs
- Runs against actual PostgreSQL (in CI)

**Trade-offs:**
- Slower than unit tests
- Requires database setup
- Flakier due to timing

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

## 15. Configuration: Environment Variables

### Decision: `dj-database-url` + `os.getenv()`

**Rationale:**
- 12-factor app compliant
- Works with Heroku/Railway/Render
- Clear separation of config from code
- Sensible defaults for development

## Summary Matrix

| Decision | Chosen | Alternative | Complexity | Scalability |
|----------|--------|-------------|------------|-------------|
| Queue Backend | PostgreSQL | Redis/RabbitMQ | Low | Medium |
| Concurrency | ThreadPool | AsyncIO/Process | Low | Medium |
| Claiming | SKIP LOCKED | Advisory/Redis | Low | High |
| Scheduling | DB Polling | Celery Beat | Low | Medium |
| Retry Policy | Hybrid | Per-Job/Per-Queue | Medium | High |
| DLQ | Separate Table | Status Only | Low | High |
| Batch Jobs | Individual + batch_id | Single Record | Medium | High |
| Workflows | DAG | Linear | High | Medium |
| API Style | APIView | ViewSet | Medium | N/A |
| Auth | API Key | JWT/OAuth | Low | High |
| Frontend | Templates + JS | SPA | Low | N/A |
| Testing | Integration | Unit | Medium | N/A |
| Architecture | Monolith | Microservices | Low | Medium |