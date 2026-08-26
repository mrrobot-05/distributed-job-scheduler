# Distributed Job Scheduler

A production-ready distributed job scheduling platform built with Django and PostgreSQL. Supports asynchronous background job execution across multiple workers with full lifecycle tracking, retry policies, and a real-time dashboard.

## Features

- **Multi-tenancy**: Organizations → Projects → Queues hierarchy
- **Job Types**: Immediate, delayed, scheduled (cron), batch, and recurring jobs
- **Reliable Execution**: Atomic job claiming using PostgreSQL `SELECT FOR UPDATE SKIP LOCKED`
- **Retry Policies**: Fixed, linear, and exponential backoff strategies
- **Dead Letter Queue**: Automatic DLQ for permanently failed jobs with manual retry
- **Worker Management**: Heartbeat monitoring, graceful shutdown, dead worker recovery
- **Real-time Dashboard**: Live metrics, job explorer, execution logs, queue health
- **Workflow Dependencies**: Job dependency chains (DAG)
- **Rate Limiting**: Per-endpoint rate limiting rules via middleware, plus auth endpoint rate limiting
- **OpenAPI Documentation**: Auto-generated Swagger/ReDoc
- **Web UI**: Full CRUD for Projects, Queues, Jobs, Workers, Scheduled Jobs, Batch Jobs, DLQ

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Client    │────▶│  Django API │◀───▶│ PostgreSQL  │
│  (REST)     │     │  (DRF)      │     │  Database   │
└─────────────┘     └─────────────┘     └─────────────┘
                           │                    ▲
                           │                    │
                    ┌──────▼──────┐     ┌───────┴───────┐
                    │  Workers    │     │  Scheduled    │
                    │ (ThreadPool)│     │  Job Runner   │
                    └─────────────┘     └───────────────┘
```

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 14+ (or SQLite for development)

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd distributed-job-scheduler

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# (Optional) For production deployment, copy and configure environment variables:
# cp .env.example .env
# Edit .env with your settings
# Local development uses hardcoded settings in settings/local.py — no .env needed

# Run migrations
python manage.py migrate

# Create a superuser (for admin access)
python manage.py createsuperuser

# Start the development server
python manage.py runserver
```

### Registration and First Login

1. Navigate to http://localhost:8000/register/
2. Fill in username, email, organization name, and project name
3. This automatically creates: User → Organization → Project (with secure API key) → default Queue
4. Log in at http://localhost:8000/login/
5. Your API key is visible on the Project detail page

### Running Workers

```bash
# Start a worker (replace with your API key from registration)
python manage.py run_worker --project_key=your-api-key --concurrency=5

# Start worker for specific queues only
python manage.py run_worker --project_key=your-api-key --concurrency=5 --queues=high-priority,default
```

### Accessing the Dashboard

- Dashboard: http://localhost:8000/
- Job Explorer: http://localhost:8000/jobs/explorer/
- Admin: http://localhost:8000/admin/
- API Docs (Swagger): http://localhost:8000/api/docs/
- API Docs (ReDoc): http://localhost:8000/api/redoc/
- OpenAPI Schema: http://localhost:8000/api/schema/

## Web UI Pages

The following web UI pages are available for managing the system:

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/` | Real-time metrics, queue health, worker status, recent jobs |
| Job Explorer | `/jobs/explorer/` | Search, filter, paginate jobs; view execution logs |
| Projects | `/projects/` | List, create, edit projects |
| Project Detail | `/projects/<uuid>/` | View project details, queues, create queues |
| Queues | `/projects/<uuid>/queues/` | List queues for a project |
| Queue Detail | `/queues/<id>/` | View queue stats, jobs, pause/resume |
| Job Detail | `/jobs/<uuid>/` | View job details, execution history, logs, retry/cancel |
| Workers | `/workers/list/` | List workers, status, heartbeat, current jobs |
| Scheduled Jobs | `/scheduled/list/` | List, create, edit, activate/deactivate cron jobs |
| Batch Jobs | `/jobs/batch/page/` | Submit and track batch jobs |
| Dead Letter Queue | `/dlq/page/` | View failed jobs, retry from DLQ |
| API Docs (Swagger) | `/api/docs/` | Interactive Swagger UI |
| API Docs (ReDoc) | `/api/redoc/` | ReDoc documentation |
| OpenAPI Schema | `/api/schema/` | Raw OpenAPI 3.0 schema |

## API Usage

### Authentication

All API requests require the `X-Project-Key` header. API keys are cryptographically random strings generated during project creation (via `secrets.token_urlsafe(32)`).

```bash
curl -H "X-Project-Key: your-api-key" http://localhost:8000/api/jobs/
```

### Submit a Job

```bash
curl -X POST http://localhost:8000/api/jobs/submit/ \
  -H "X-Project-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "send_email",
    "queue": "default",
    "payload": {"to": "user@example.com", "subject": "Hello"},
    "max_retries": 3,
    "backoff_strategy": "EXPONENTIAL",
    "backoff_delay": 60
  }'
```

### Submit a Delayed Job

```bash
curl -X POST http://localhost:8000/api/jobs/submit/ \
  -H "X-Project-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "generate_report",
    "queue": "default",
    "payload": {"report_type": "monthly"},
    "scheduled_at": "2025-12-01T10:00:00Z"
  }'
```

### Submit a Recurring (Cron) Job

```bash
curl -X POST http://localhost:8000/api/jobs/submit/ \
  -H "X-Project-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "cleanup_database",
    "queue": "maintenance",
    "payload": {"environment": "production"},
    "cron_expression": "0 2 * * *"
  }'
```

### Submit a Batch Job

```bash
curl -X POST http://localhost:8000/api/jobs/batch/ \
  -H "X-Project-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "bulk_email_campaign",
    "queue": "emails",
    "jobs": [
      {"name": "send_email", "payload": {"to": "user1@example.com"}},
      {"name": "send_email", "payload": {"to": "user2@example.com"}},
      {"name": "send_email", "payload": {"to": "user3@example.com"}}
    ]
  }'
```

### List Jobs with Filters

```bash
# All jobs
curl -H "X-Project-Key: your-api-key" http://localhost:8000/api/jobs/

# Filter by status
curl -H "X-Project-Key: your-api-key" "http://localhost:8000/api/jobs/?status=FAILED"

# Filter by queue
curl -H "X-Project-Key: your-api-key" "http://localhost:8000/api/jobs/?queue=emails"

# Pagination
curl -H "X-Project-Key: your-api-key" "http://localhost:8000/api/jobs/?page=2&page_size=50"
```

### Retry a Failed Job

```bash
curl -X POST http://localhost:8000/api/jobs/<job-id>/retry/ \
  -H "X-Project-Key: your-api-key"
```

### Get Job Logs

```bash
curl -H "X-Project-Key: your-api-key" http://localhost:8000/api/jobs/<job-id>/logs/
```

### Queue Management

```bash
# List queues
curl -H "X-Project-Key: your-api-key" http://localhost:8000/api/queues/

# Pause queue
curl -X POST http://localhost:8000/api/queues/<queue-id>/pause/ \
  -H "X-Project-Key: your-api-key"

# Resume queue
curl -X POST http://localhost:8000/api/queues/<queue-id>/resume/ \
  -H "X-Project-Key: your-api-key"

# Queue stats
curl -H "X-Project-Key: your-api-key" http://localhost:8000/api/queues/<queue-id>/stats/
```

### Worker Registration

```bash
curl -X POST http://localhost:8000/api/workers/register/ \
  -H "X-Project-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"hostname": "worker-1", "concurrency_limit": 10}'
```

### Worker Heartbeat

```bash
curl -X POST http://localhost:8000/api/workers/heartbeat/ \
  -H "X-Project-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"worker_id": "worker-uuid"}'
```

## Web UI Features

### Dashboard (`/`)
Real-time metrics with charts:
- Throughput (jobs/min)
- Job status distribution
- Queue health (active/paused)
- Worker load (current jobs / capacity)
- Recent jobs table

### Job Explorer (`/jobs/explorer/`)
- Filter by status, queue, date range
- Pagination with configurable page size
- Click any job to view detailed modal with:
  - Job info (status, queue, retries, backoff)
  - Payload (formatted JSON)
  - Execution history with status, duration, error messages
  - Execution logs (DEBUG/INFO/WARNING/ERROR with timestamps)
  - Retry/Cancel buttons for failed/queued jobs

### Projects (`/projects/`)
- List all projects with queue/job counts
- Create new project (generates API key automatically)
- View project detail with queue list
- Edit project name/active status

### Queues
- List queues with priority, concurrency, status
- Pause/resume queues
- View queue statistics (jobs by status)
- Edit queue settings (priority, concurrency, retry policy)

### Jobs
- List with filters (status, queue, date range)
- Detail view with execution history and logs
- Retry failed/DLQ jobs
- Cancel pending jobs

### Workers
- List all workers with status, current jobs, capacity
- Last heartbeat timestamp

### Scheduled Jobs
- List with cron expression, next run time, active status
- Create/edit with cron expression validator
- Activate/deactivate toggle
- Automatic job creation via worker's scheduled job processor

### Batch Jobs
- Submit multiple jobs in one API call
- Track aggregate progress (completed/failed/total)
- View individual job statuses

### Dead Letter Queue (DLQ)
- List all permanently failed jobs
- View error message, failure reason, retry count
- One-click retry from DLQ

## Authentication and Security

### API Authentication
- API keys are generated using `secrets.token_urlsafe(32)` (cryptographically secure)
- Passed via `X-Project-Key` HTTP header
- Per-project data isolation enforced at query level

### Web UI Authentication
- Django session-based authentication (`@login_required`)
- Login at `/login/`, registration at `/register/`
- Registration auto-creates: User → Organization → Project → default Queue

### Rate Limiting
- **Auth endpoints**: POST to `/login/` and `/register/` are rate-limited to 20 requests per minute per IP address
- **API endpoints**: Configurable per-endpoint rate limiting via `RateLimitRule` model and `RateLimitMiddleware`

### Admin Security
- API keys are masked in Django admin interface (e.g., `abcd...7890`)
- Full API key visible only in project detail view and admin readonly fields

## Settings Configuration

The application uses a settings package at `distributed_job_scheduler/settings/`:

```
distributed_job_scheduler/settings/
├── __init__.py      # Auto-selects settings module based on DJANGO_SETTINGS_MODULE
├── base.py          # Shared settings (PostgreSQL, DRF, logging, middleware)
├── local.py         # Development settings (SQLite, DEBUG=True)
└── production.py    # Production settings (SSL, HSTS, file logging)
```

**Settings auto-selection**: `manage.py` defaults to `distributed_job_scheduler.settings`, which triggers `settings/__init__.py`. This module reads `DJANGO_SETTINGS_MODULE` and falls back to `settings.local` if not set or if set to the package itself.

### Local Development (default)
- SQLite database
- `DEBUG=True`
- No SSL/HSTS
- CORS allowed for all origins

### Production
Set `DJANGO_SETTINGS_MODULE=distributed_job_scheduler.settings.production`:
- PostgreSQL via `DATABASE_URL`
- `DEBUG=False`
- SSL redirects, HSTS, secure cookies
- SMTP email backend
- File-based logging

## Environment Variables

| Variable | Description | Local (local.py) | Production (production.py) |
|----------|-------------|-------------------|---------------------------|
| `SECRET_KEY` | Django secret key | Hardcoded insecure dev key | **Required** (from env) |
| `DEBUG` | Debug mode | Hardcoded `True` | Hardcoded `False` |
| `ALLOWED_HOSTS` | Comma-separated hosts | Hardcoded `localhost,127.0.0.1,0.0.0.0` | **Required** (from env) |
| `DATABASE_URL` | PostgreSQL connection string | SQLite (hardcoded, no env var) | **Required** (from env) |
| `DJANGO_SETTINGS_MODULE` | Settings module path | `distributed_job_scheduler.settings.local` | `distributed_job_scheduler.settings.production` |

> **Note**: `DEBUG` and `ALLOWED_HOSTS` are **not** read from environment variables in local development — they are hardcoded in `settings/local.py`. They are only configurable via environment in `settings/production.py`.

## Project Structure

```
distributed_job_scheduler/
├── scheduler/                    # Main Django app
│   ├── management/commands/      # Custom management commands
│   │   ├── run_worker.py         # Worker daemon
│   │   └── seed_demo.py          # Demo data seeder
│   ├── migrations/               # Database migrations (6 total)
│   ├── templates/scheduler/      # HTML templates
│   │   ├── base.html             # Base template with sidebar
│   │   ├── dashboard.html        # Main dashboard
│   │   ├── job_explorer.html     # Job search/filter UI
│   │   ├── projects/             # Project CRUD templates
│   │   ├── queues/               # Queue CRUD templates
│   │   ├── jobs/                 # Job detail, batch templates
│   │   ├── workers/              # Worker list template
│   │   ├── scheduled/            # Scheduled job CRUD templates
│   │   └── dlq/                  # DLQ list template
│   ├── models.py                 # Database models (11 models)
│   ├── views.py                  # API views
│   ├── page_views.py             # Web UI page views
│   ├── serializers.py            # DRF serializers
│   ├── urls.py                   # URL routing
│   ├── authentication.py         # API key authentication
│   ├── pagination.py             # Custom pagination
│   ├── handlers.py               # Job handlers
│   ├── middleware.py             # Rate limiting middleware
│   ├── admin.py                  # Admin configuration
│   └── tests.py                  # Integration tests
├── distributed_job_scheduler/    # Project settings
│   ├── settings/                 # Settings package
│   │   ├── __init__.py           # Auto-selects settings module
│   │   ├── base.py               # Common settings
│   │   ├── local.py              # Development settings
│   │   └── production.py         # Production settings
│   ├── urls.py                   # Root URL config
│   └── wsgi.py                   # WSGI entry point
├── docs/                         # Documentation
│   ├── ARCHITECTURE.md           # Architecture decisions
│   ├── ER_DIAGRAM.md             # Entity-relationship diagram
│   └── DESIGN_DECISIONS.md       # Design trade-offs
├── staticfiles/                  # Collected static files
├── templates/                    # Global templates
├── manage.py                     # Django management script
├── requirements.txt              # Python dependencies
├── requirements-ci.txt           # CI tool dependencies (pinned)
├── Dockerfile                    # Multi-stage Docker build
├── verify_all.py                 # End-to-end verification script
└── .gitignore                    # Git ignore rules
```

## Testing

```bash
# Run all tests
python manage.py test scheduler

# Run with verbose output
python manage.py test scheduler -v 2

# Run specific test class
python manage.py test scheduler.SchedulerIntegrationTest

# Run specific test
python manage.py test scheduler.SchedulerIntegrationTest.test_full_job_lifecycle
```

### Test Suite

The test suite includes 51 tests covering:
- Job lifecycle (submit, claim, execute, complete, retry, DLQ)
- Worker management (heartbeat, graceful shutdown, dead worker recovery)
- Scheduled jobs (cron scheduling, due job processing)
- Batch jobs (creation, progress tracking)
- Security (cross-project isolation, API key validation)
- API key generation (secrets.token_urlsafe(32))
- Serializer project scoping (cross-project FK rejection)
- Auth rate limiting (login/register rate limiting)
- Admin API key masking
- Timezone handling

**Note**: Concurrent job claiming tests require PostgreSQL `SELECT FOR UPDATE SKIP LOCKED`. These tests are skipped when running against SQLite.

## Production Deployment

### Using Gunicorn + WhiteNoise

```bash
# Collect static files
python manage.py collectstatic --noinput

# Run with Gunicorn
gunicorn distributed_job_scheduler.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 4 \
  --worker-class gthread \
  --threads 4 \
  --timeout 120
```

### Docker

```bash
# Build
docker build -t distributed-job-scheduler .

# Run
docker run -p 8000:8000 \
  -e SECRET_KEY=your-secret-key \
  -e DATABASE_URL=postgresql://user:pass@host:port/db \
  -e DJANGO_SETTINGS_MODULE=distributed_job_scheduler.settings.production \
  distributed-job-scheduler
```

The Dockerfile uses a multi-stage build:
1. **Builder stage**: Installs system dependencies (gcc, libpq-dev) and Python packages
2. **Final stage**: Copies packages, creates non-root user, collects static files, runs Gunicorn

### Environment-Specific Settings

Create separate settings files:
- `settings/production.py` - Production settings
- `settings/staging.py` - Staging settings

## Extending Job Handlers

Add custom job handlers in `scheduler/handlers.py`:

```python
def my_custom_job(payload):
    # Your business logic here
    return {"status": "completed", "data": "result"}

HANDLERS = {
    # ... existing handlers
    "my_custom_job": my_custom_job,
}
```

## Demo Data

Seed the database with demo data for testing:

```bash
python manage.py seed_demo
```

This creates:
- User: `demo@demo.com` / `demo123`
- Organization: "Demo Organization" (linked to demo user)
- Projects: "API Project" and "Batch Project" (with auto-generated secure API keys)
- Queues: high-priority, default, low-priority, batch-queue
- 21 sample jobs (various statuses)
- 1 ScheduledJob (daily-report cron)
- 1 Worker registration
- 1 BatchJob (3 jobs)
- 2 DLQ entries

After seeding, the demo API keys are printed to the console. Use them with the `--project_key` flag when starting workers.

## Monitoring

Key metrics to monitor:
- Queue depth (jobs pending per queue)
- Worker count and health
- Job throughput (completed/minute)
- Error rate (failed/total)
- DLQ size
- Worker heartbeat staleness

## License

MIT License - see LICENSE file for details.
