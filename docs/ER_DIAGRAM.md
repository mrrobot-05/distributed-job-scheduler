# Entity-Relationship Diagram

## Mermaid Diagram

```mermaid
erDiagram
    ORGANIZATION ||--o{ PROJECT : "contains"
    PROJECT ||--o{ QUEUE : "owns"
    PROJECT ||--o{ WORKER : "registers"
    PROJECT ||--o{ BATCH_JOB : "submits"
    PROJECT ||--o{ RATE_LIMIT_RULE : "configures"
    QUEUE ||--o{ JOB : "contains"
    QUEUE ||--o{ SCHEDULED_JOB : "schedules"
    JOB ||--o{ JOB_EXECUTION : "logs"
    JOB_EXECUTION ||--o{ JOB_LOG : "details"
    JOB ||--o| DEAD_LETTER_QUEUE : "fails permanently"
    WORKER ||--o{ JOB_EXECUTION : "executes"
    JOB }o--o{ JOB : "depends_on (workflow)"
    BATCH_JOB ||--o{ JOB : "groups"

    ORGANIZATION {
        uuid id PK
        string name
        string slug UK
        datetime created_at
        datetime updated_at
    }

    PROJECT {
        uuid id PK
        uuid organization_id FK
        string name
        string api_key UK
        boolean is_active
        datetime created_at
        datetime updated_at
    }

    QUEUE {
        bigint id PK
        uuid project_id FK
        string name
        int priority
        int concurrency_limit
        boolean is_paused
        json retry_policy
        datetime created_at
        datetime updated_at
    }

    JOB {
        uuid id PK
        bigint queue_id FK
        string name
        json payload
        string status
        string unique_key UK
        datetime scheduled_at
        string cron_expression
        int retry_count
        int max_retries
        string backoff_strategy
        int backoff_delay
        uuid batch_id
        int timeout_seconds
        datetime created_at
        datetime updated_at
    }

    JOB_EXECUTION {
        bigint id PK
        uuid job_id FK
        uuid worker_id FK
        string status
        text error_message
        datetime started_at
        datetime ended_at
        int duration_ms
        int attempt_number
    }

    JOB_LOG {
        bigint id PK
        bigint execution_id FK
        string level
        text message
        datetime timestamp
        json meta
    }

    WORKER {
        uuid id PK
        uuid project_id FK
        string hostname
        string status
        int concurrency_limit
        datetime last_heartbeat
        int current_jobs
        json metadata
        datetime created_at
        datetime updated_at
    }

    BATCH_JOB {
        uuid id PK
        uuid project_id FK
        string name
        int total_jobs
        int completed_jobs
        int failed_jobs
        string status
        datetime created_at
        datetime updated_at
        datetime completed_at
    }

    DEAD_LETTER_QUEUE {
        bigint id PK
        uuid job_id FK (OneToOne)
        text error_message
        string failure_reason
        int retry_count
        datetime last_attempt_at
        datetime created_at
        datetime resolved_at
        string resolved_by
        text resolution_notes
    }

    SCHEDULED_JOB {
        bigint id PK
        bigint queue_id FK
        string name
        json payload
        string cron_expression
        datetime next_run_at
        int max_retries
        string backoff_strategy
        int backoff_delay
        boolean is_active
        datetime created_at
        datetime updated_at
    }

    WORKFLOW_DEPENDENCY {
        bigint id PK
        uuid job_id FK
        uuid depends_on_id FK
        datetime created_at
    }

    RATE_LIMIT_RULE {
        bigint id PK
        uuid project_id FK
        string endpoint
        int max_requests
        int window_seconds
        datetime created_at
    }
```

## Table Descriptions

### Core Tables

| Table | Purpose | Key Relationships |
|-------|---------|-------------------|
| `organization` | Top-level tenant container | Parent of Project |
| `project` | API key isolation boundary | Owns Queues, Workers, Batches |
| `queue` | Job ordering & concurrency control | Contains Jobs, ScheduledJobs |
| `job` | Unit of work | Executions, Logs, DLQ, Dependencies |
| `job_execution` | Single execution attempt | Logs, Worker assignment |
| `job_log` | Structured execution logs | Linked to Execution |

### Worker Management

| Table | Purpose | Key Relationships |
|-------|---------|-------------------|
| `worker` | Registered worker process | Executes JobExecutions |
| `dead_letter_queue` | Permanent failures | One-to-One with Job |

### Scheduling & Batching

| Table | Purpose | Key Relationships |
|-------|---------|-------------------|
| `scheduled_job` | Cron-like recurring jobs | Creates Jobs when due |
| `batch_job` | Groups related jobs | Contains Jobs via batch_id |

### Advanced Features

| Table | Purpose | Key Relationships |
|-------|---------|-------------------|
| `workflow_dependency` | Job DAG dependencies | Self-referential on Job |
| `rate_limit_rule` | API rate limiting | Per Project + Endpoint |

## Index Strategy

### Query Patterns & Indexes

| Query | Index |
|-------|-------|
| Claim next job | `Job(queue, status, scheduled_at)` |
| Job by unique_key | `Job(unique_key)` - Unique |
| Job by batch_id | `Job(batch_id)` |
| Executions by worker | `JobExecution(worker, started_at)` |
| Logs by execution | `JobLog(execution, timestamp)` |
| Dead workers | `Worker(last_heartbeat)` |
| Due scheduled jobs | `ScheduledJob(is_active, next_run_at)` |
| Workflow deps | `WorkflowDependency(depends_on)` |

## Normalization Notes

- **3NF Compliant**: No transitive dependencies
- **Denormalization**: `Job.batch_id` for batch queries without join
- **JSON Fields**: `payload`, `retry_policy`, `metadata` for flexible schemas
- **Soft Deletes**: Not implemented; use status fields (DLQ, is_active)

## Migration History

1. `0001_initial` - Core models (Project, Queue, Job, Worker, JobExecution)
2. `0002_worker_project` - Add project FK to Worker (nullable)
3. `0003_alter_worker_project` - Make Worker.project NOT NULL
4. `0004_alter_worker_project` - Revert to nullable Worker.project
5. `0005_batchjob_deadletterqueue_joblog_organization_and_more` - Extended models

## Data Retention

| Table | Retention Policy |
|-------|------------------|
| Job | Indefinite (archival by status) |
| JobExecution | Indefinite (metrics) |
| JobLog | 30 days (configurable) |
| Worker | Indefinite (history) |
| DeadLetterQueue | Until manually resolved |
| ScheduledJob | Indefinite |
| WorkflowDependency | Until jobs deleted |