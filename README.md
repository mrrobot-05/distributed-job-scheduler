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
- **Workflow Dependencies**: Job dependency chains
- **Rate Limiting**: Per-endpoint rate limiting rules
- **OpenAPI Documentation**: Auto-generated Swagger/ReDoc

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
- Redis (optional, for future WebSocket support)

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

# Set environment variables
cp .env.example .env
# Edit .env with your settings

# Run migrations
python manage.py migrate

# Create a superuser (for admin access)
python manage.py createsuperuser

# Create initial project and queue via Django shell
python manage.py shell -c "
from scheduler.models import Project, Queue
p = Project.objects.create(name='My Project', api_key='your-api-key-here')
Queue.objects.create(project=p, name='default', priority=1, concurrency_limit=5)
"

# Start the development server
python manage.py runserver
```

### Running Workers

```bash
# Start a worker (replace with your API key)
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

## API Usage

### Authentication

All API requests require the `X-Project-Key` header:

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

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Django secret key | Required in production |
| `DEBUG` | Debug mode | `True` |
| `ALLOWED_HOSTS` | Comma-separated hosts | `localhost,127.0.0.1` |
| `DATABASE_URL` | PostgreSQL connection string | SQLite fallback |
| `DATABASE_URL` | Postgres: `postgresql://user:pass@host:port/db` | |

## Project Structure

```
distributed_job_scheduler/
├── scheduler/                    # Main Django app
│   ├── management/commands/      # Custom management commands
│   │   └── run_worker.py         # Worker daemon
│   ├── migrations/               # Database migrations
│   ├── templates/scheduler/      # HTML templates
│   │   ├── dashboard.html        # Main dashboard
│   │   └── job_explorer.html     # Job search/filter UI
│   ├── models.py                 # Database models
│   ├── views.py                  # API views
│   ├── serializers.py            # DRF serializers
│   ├── urls.py                   # URL routing
│   ├── authentication.py         # API key authentication
│   ├── pagination.py             # Custom pagination
│   ├── handlers.py               # Job handlers
│   ├── admin.py                  # Admin configuration
│   └── tests.py                  # Integration tests
├── distributed_job_scheduler/    # Project settings
│   ├── settings.py               # Django settings
│   ├── urls.py                   # Root URL config
│   └── wsgi.py                   # WSGI entry point
├── docs/                         # Documentation
│   ├── ARCHITECTURE.md           # Architecture decisions
│   ├── ER_DIAGRAM.md             # Entity-relationship diagram
│   └── DESIGN_DECISIONS.md       # Design trade-offs
├── staticfiles/                  # Collected static files
├── manage.py                     # Django management script
├── requirements.txt              # Python dependencies
└── .gitignore                    # Git ignore rules
```

## Testing

```bash
# Run all tests
python manage.py test scheduler

# Run with verbose output
python manage.py test scheduler -v 2

# Run specific test
python manage.py test scheduler.SchedulerIntegrationTest.test_full_job_lifecycle
```

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

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["gunicorn", "distributed_job_scheduler.wsgi:application", "--bind", "0.0.0.0:8000"]
```

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