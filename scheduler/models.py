import uuid
from django.db import models
from django.utils import timezone
from django.conf import settings


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='organizations',
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class Project(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='projects', null=True, blank=True)
    name = models.CharField(max_length=255)
    api_key = models.CharField(max_length=255, db_index=True, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        org_name = self.organization.name if self.organization else "No Org"
        return f"{org_name} / {self.name}"


class Queue(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='queues')
    name = models.CharField(max_length=255)
    priority = models.IntegerField(default=1)  # higher runs first
    concurrency_limit = models.IntegerField(default=5)
    is_paused = models.BooleanField(default=False)
    retry_policy = models.JSONField(default=dict)  # default configuration for retries
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['project', 'name']
        ordering = ['-priority', 'name']

    def __str__(self):
        return f"{self.project.name} - {self.name}"


class Job(models.Model):
    STATUS_CHOICES = [
        ('QUEUED', 'Queued'),
        ('SCHEDULED', 'Scheduled'),
        ('CLAIMED', 'Claimed'),
        ('RUNNING', 'Running'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('DLQ', 'Dead Letter Queue'),
    ]

    BACKOFF_STRATEGY_CHOICES = [
        ('FIXED', 'Fixed'),
        ('LINEAR', 'Linear'),
        ('EXPONENTIAL', 'Exponential'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    queue = models.ForeignKey(Queue, on_delete=models.CASCADE, related_name='jobs')
    name = models.CharField(max_length=255, db_index=True)  # job type/handler identifier
    payload = models.JSONField(default=dict)  # inputs for execution
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='QUEUED', db_index=True)
    unique_key = models.CharField(max_length=255, unique=True, null=True, blank=True, db_index=True)  # for idempotency
    scheduled_at = models.DateTimeField(db_index=True)
    cron_expression = models.CharField(max_length=255, null=True, blank=True)  # for recurring jobs
    retry_count = models.IntegerField(default=0)
    max_retries = models.IntegerField(default=3)
    backoff_strategy = models.CharField(max_length=20, choices=BACKOFF_STRATEGY_CHOICES, default='FIXED')
    backoff_delay = models.IntegerField(default=60)  # default delay in seconds
    batch_id = models.UUIDField(null=True, blank=True, db_index=True)  # for batch jobs
    timeout_seconds = models.IntegerField(default=300)  # job timeout
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['queue', 'status', 'scheduled_at']),
            models.Index(fields=['batch_id']),
        ]

    def __str__(self):
        return f"{self.name} ({self.id})"


class JobExecution(models.Model):
    STATUS_CHOICES = [
        ('SUCCESS', 'Success'),
        ('FAILURE', 'Failure'),
    ]

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='executions')
    worker = models.ForeignKey('Worker', null=True, on_delete=models.SET_NULL, related_name='executions')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    error_message = models.TextField(null=True, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.IntegerField(null=True, blank=True)
    attempt_number = models.IntegerField(default=1)

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['worker', 'started_at']),
        ]

    def __str__(self):
        return f"Execution for {self.job.name} - {self.status}"


class JobLog(models.Model):
    LEVEL_CHOICES = [
        ('DEBUG', 'Debug'),
        ('INFO', 'Info'),
        ('WARNING', 'Warning'),
        ('ERROR', 'Error'),
    ]

    execution = models.ForeignKey(JobExecution, on_delete=models.CASCADE, related_name='logs')
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='INFO')
    message = models.TextField()
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['timestamp']
        indexes = [
            models.Index(fields=['execution', 'timestamp']),
        ]

    def __str__(self):
        return f"[{self.level}] {self.execution.job.name}"


class Worker(models.Model):
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('SHUTTING_DOWN', 'Shutting Down'),
        ('DEAD', 'Dead'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='workers', null=True, blank=True)
    hostname = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')
    concurrency_limit = models.IntegerField(default=10)
    last_heartbeat = models.DateTimeField(db_index=True, default=timezone.now)
    current_jobs = models.IntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-last_heartbeat']

    def __str__(self):
        return f"{self.hostname} ({self.id})"


class BatchJob(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PARTIAL', 'Partial'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='batch_jobs')
    name = models.CharField(max_length=255)
    total_jobs = models.IntegerField(default=0)
    completed_jobs = models.IntegerField(default=0)
    failed_jobs = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Batch {self.name} ({self.id})"


class WorkflowDependency(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='dependencies')
    depends_on = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='dependents')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ['job', 'depends_on']
        indexes = [
            models.Index(fields=['depends_on']),
        ]

    def __str__(self):
        return f"{self.job.name} depends on {self.depends_on.name}"


class DeadLetterQueue(models.Model):
    job = models.OneToOneField(Job, on_delete=models.CASCADE, related_name='dlq_entry')
    error_message = models.TextField()
    failure_reason = models.CharField(max_length=100)
    retry_count = models.IntegerField()
    last_attempt_at = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.CharField(max_length=255, blank=True, null=True)
    resolution_notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"DLQ: {self.job.name} ({self.job.id})"


class ScheduledJob(models.Model):
    queue = models.ForeignKey(Queue, on_delete=models.CASCADE, related_name='scheduled_jobs')
    name = models.CharField(max_length=255)
    payload = models.JSONField(default=dict)
    cron_expression = models.CharField(max_length=255)
    next_run_at = models.DateTimeField(db_index=True)
    max_retries = models.IntegerField(default=3)
    backoff_strategy = models.CharField(max_length=20, choices=Job.BACKOFF_STRATEGY_CHOICES, default='FIXED')
    backoff_delay = models.IntegerField(default=60)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['next_run_at']
        indexes = [
            models.Index(fields=['is_active', 'next_run_at']),
        ]

    def __str__(self):
        return f"Scheduled: {self.name} ({self.cron_expression})"


class RateLimitRule(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='rate_limits')
    endpoint = models.CharField(max_length=255)
    max_requests = models.IntegerField(default=100)
    window_seconds = models.IntegerField(default=60)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ['project', 'endpoint']

    def __str__(self):
        return f"RateLimit: {self.endpoint} ({self.max_requests}/{self.window_seconds}s)"
