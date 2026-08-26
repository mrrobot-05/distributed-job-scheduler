# Architecture Documentation

## System Overview

The Distributed Job Scheduler is a Django-based platform for reliable, scalable background job processing. It uses PostgreSQL as the coordination layer, eliminating the need for external message brokers like Redis or RabbitMQ.

## Core Components

### 1. API Layer (Django REST Framework)
- **Authentication**: API key-based (`X-Project-Key` header) via `ProjectKeyAuthentication`
- **Session Authentication**: Django session auth for web UI (`SessionAuthentication` in DRF defaults)
- **Endpoints**: RESTful CRUD for all entities
- **Serialization**: DRF serializers with cross-project scoping validation
- **Documentation**: Auto-generated OpenAPI 3.0 via drf-spectacular

### 2. Database Layer (PostgreSQL)
- **Primary coordination mechanism**: Row-level locking via `SELECT FOR UPDATE SKIP LOCKED`
- **MVCC**: Multi-Version Concurrency Control for read scalability
- **Advisory Locks**: PostgreSQL advisory locks for scheduled job creation coordination (via `DistributedLock` in `run_worker.py`)

### 3. Worker Daemon
- **Process Model**: Single process with ThreadPoolExecutor
- **Concurrency**: Configurable per-worker and per-queue
- **Heartbeat**: Periodic updates to Worker table
- **Graceful Shutdown**: SIGINT/SIGTERM handling
- **Distributed Lock**: Uses `pg_try_advisory_lock()` for scheduled job creation (no-op on SQLite)

### 4. Scheduler (Cron-like)
- **Mechanism**: ScheduledJob model with cron expressions
- **Execution**: Worker polls due scheduled jobs
- **Locking**: PostgreSQL advisory locks prevent duplicate job creation across workers

### 5. Multi-Tenancy
- **Hierarchy**: Organization → Project → Queue → Job
- **User linkage**: `Organization.user` FK links organizations to Django users
- **Web UI isolation**: All page views filter by `organization__user=request.user`
- **API isolation**: All API queries filtered by `project=request.auth`

### 6. Rate Limiting
- **Middleware**: `RateLimitMiddleware` in `scheduler/middleware.py`
- **Auth endpoints**: POST to `/login/` and `/register/` rate-limited to 20 requests/minute/IP
- **API endpoints**: Configurable per-endpoint via `RateLimitRule` model
- **Implementation**: Django cache-based with `X-RateLimit-*` response headers

## Data Flow

### Registration Flow
```
POST /register/ → Create User → Create Organization (linked to User)
  → Create Project (with secrets.token_urlsafe(32) API key)
  → Create default Queue → Redirect to login
```

### Job Submission
```
Client → POST /api/jobs/submit/ → Validate → Create Job (QUEUED) → Return Job ID
```

### Job Claiming (Atomic)
```
Worker Loop → SELECT FOR UPDATE SKIP LOCKED 
  WHERE status IN (QUEUED, SCHEDULED) 
  AND scheduled_at <= NOW()
  ORDER BY -queue.priority, scheduled_at
→ UPDATE status = CLAIMED
→ Return Job
```

### Job Execution
```
ThreadPoolExecutor.submit(execute_job)
  → Create JobExecution (RUNNING)
  → Update Job status = RUNNING
  → Execute handler with timeout
  → On success: COMPLETED (or SCHEDULED for cron)
  → On failure: Retry logic or DLQ
  → Save execution metrics
```

### Retry Logic
```
Failure → Check retry_count < max_retries
  → Calculate backoff (FIXED/LINEAR/EXPONENTIAL)
  → Update status = SCHEDULED, scheduled_at = NOW + delay
  → Increment retry_count
Exhausted retries → status = DLQ → Create DeadLetterQueue entry
```

### Dead Worker Recovery
```
Cleanup (every loop) → Find workers with last_heartbeat > 5 min
  → Mark worker = DEAD
  → Find jobs with status IN (CLAIMED, RUNNING) AND execution.worker = dead_worker
  → Reset job status = QUEUED
```

## Concurrency Model

### Queue-Level Concurrency
Each queue has a `concurrency_limit`. Workers check running job count per queue before claiming:

```python
running_counts = Job.objects.filter(queue__in=queues, status='RUNNING')
    .values('queue').annotate(count=Count('id'))
available_queues = [q for q in queues if running_counts[q.id] < q.concurrency_limit]
```

### Worker-Level Concurrency
Each worker has a `concurrency_limit` controlling ThreadPoolExecutor size.

### Combined Effect
- A job can run if BOTH queue AND worker have capacity
- Prevents queue starvation and worker overload

## Database Schema Highlights

### Key Indexes
- `Job(queue, status, scheduled_at)` - Claiming query
- `Job(batch_id)` - Batch job lookups
- `JobExecution(worker, started_at)` - Worker history
- `Worker(last_heartbeat)` - Dead worker detection
- `ScheduledJob(is_active, next_run_at)` - Due job polling

### Cascading Behavior
- Organization → Project: CASCADE
- Project → Queue: CASCADE
- Queue → Job: CASCADE
- Job → JobExecution: CASCADE
- Job → JobLog: CASCADE (via JobExecution)
- Worker → JobExecution: SET_NULL (preserve history)

## Security

### API Authentication
- API keys are cryptographically random strings (`secrets.token_urlsafe(32)`, 43 characters)
- Passed via `X-Project-Key` HTTP header
- Per-project isolation enforced at query level
- Inactive projects are rejected

### Web UI Authentication
- Django session-based authentication (`@login_required` for all page views)
- Registration at `/register/` creates User → Organization → Project → Queue
- All page views filter by `organization__user=request.user`

### Data Isolation
- API: All queries filtered by `project=request.auth`
- Web UI: All queries filtered by `organization__user=request.user`
- Cross-project data leakage prevented at serializer and view level

### Rate Limiting
- Auth endpoints: 20 requests/minute/IP for `/login/` and `/register/` POST
- API endpoints: Configurable per-endpoint via `RateLimitRule` model
- Implemented via `RateLimitMiddleware` using Django cache

### Admin Security
- API keys masked in admin list display (`abcd...7890`)
- Full key visible in admin readonly fields for management

## Scalability Considerations

### Horizontal Scaling
- Add more workers with same project key
- Workers automatically distribute load via SKIP LOCKED
- No leader election needed

### Vertical Scaling
- Increase worker concurrency_limit
- Increase queue concurrency_limit
- PostgreSQL connection pooling (pgBouncer)

### Database Optimization
- Partition Job table by created_at (future)
- Archive old JobExecutions (future)
- Read replicas for dashboard queries (future)

## Failure Modes & Mitigations

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Worker crash | Missing heartbeat (5 min) | Cleanup job re-queues CLAIMED/RUNNING jobs |
| Job timeout | Configured timeout_seconds | Exception caught, retry/DLQ logic |
| Duplicate submission | unique_key constraint | IntegrityError → 409 Conflict |
| DB connection loss | Django reconnects | Worker pauses, retries on next loop |
| Handler exception | Try/except in execute_job | Retry logic with backoff |

## Future Enhancements

1. **WebSocket Live Updates**: Django Channels + Redis for real-time dashboard
2. **Queue Sharding**: Hash-based job routing for massive scale
3. **Event-Driven Execution**: pg_notify for instant job claiming
4. **AI Failure Analysis**: LLM-powered error summarization
5. **RBAC**: Role-based access control for teams
6. **Multi-region**: Cross-region job scheduling
