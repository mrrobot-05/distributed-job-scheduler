from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Project, Queue, Job, Worker, JobExecution,
    Organization, JobLog, BatchJob, WorkflowDependency,
    DeadLetterQueue, ScheduledJob, RateLimitRule
)

@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'user', 'created_at')
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization', 'masked_api_key', 'is_active', 'created_at')
    list_filter = ('organization', 'is_active')
    search_fields = ('name', 'api_key')
    readonly_fields = ('api_key',)

    def masked_api_key(self, obj):
        key = obj.api_key
        if key and len(key) > 8:
            return format_html('{}...{}', key[:4], key[-4:])
        return key
    masked_api_key.short_description = 'API Key'  # type: ignore[attr-defined]  # Django admin decorator sets short_description at runtime


@admin.register(Queue)
class QueueAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'priority', 'concurrency_limit', 'is_paused')
    list_filter = ('project', 'is_paused')
    search_fields = ('name',)


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ('name', 'queue', 'status', 'scheduled_at', 'retry_count', 'batch_id')
    list_filter = ('status', 'queue')
    search_fields = ('name', 'unique_key')
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'created_at'


@admin.register(Worker)
class WorkerAdmin(admin.ModelAdmin):
    list_display = ('hostname', 'project', 'status', 'concurrency_limit', 'current_jobs', 'last_heartbeat')
    list_filter = ('status', 'project')
    search_fields = ('hostname',)


@admin.register(JobExecution)
class JobExecutionAdmin(admin.ModelAdmin):
    list_display = ('job', 'worker', 'status', 'started_at', 'duration_ms', 'attempt_number')
    list_filter = ('status', 'worker')
    readonly_fields = ('started_at', 'ended_at', 'duration_ms')
    date_hierarchy = 'started_at'


@admin.register(JobLog)
class JobLogAdmin(admin.ModelAdmin):
    list_display = ('execution', 'level', 'timestamp')
    list_filter = ('level',)
    search_fields = ('message',)
    readonly_fields = ('timestamp',)
    date_hierarchy = 'timestamp'


@admin.register(BatchJob)
class BatchJobAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'status', 'total_jobs', 'completed_jobs', 'failed_jobs', 'created_at')
    list_filter = ('status', 'project')
    search_fields = ('name',)
    readonly_fields = ('created_at', 'updated_at', 'completed_at')
    date_hierarchy = 'created_at'


@admin.register(WorkflowDependency)
class WorkflowDependencyAdmin(admin.ModelAdmin):
    list_display = ('job', 'depends_on', 'created_at')
    search_fields = ('job__name', 'depends_on__name')


@admin.register(DeadLetterQueue)
class DeadLetterQueueAdmin(admin.ModelAdmin):
    list_display = ('job', 'failure_reason', 'retry_count', 'last_attempt_at', 'resolved_at')
    list_filter = ('failure_reason', 'resolved_at')
    search_fields = ('job__name', 'error_message')
    readonly_fields = ('created_at',)
    date_hierarchy = 'created_at'


@admin.register(ScheduledJob)
class ScheduledJobAdmin(admin.ModelAdmin):
    list_display = ('name', 'queue', 'cron_expression', 'next_run_at', 'is_active')
    list_filter = ('is_active', 'queue')
    search_fields = ('name',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(RateLimitRule)
class RateLimitRuleAdmin(admin.ModelAdmin):
    list_display = ('project', 'endpoint', 'max_requests', 'window_seconds')
    search_fields = ('project__name', 'endpoint')
