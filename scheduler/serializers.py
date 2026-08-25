from django.utils import timezone
from rest_framework import serializers
from .models import Job, Worker, Queue, Project, BatchJob, JobLog, DeadLetterQueue, ScheduledJob, WorkflowDependency, JobExecution


class JobSubmitSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)

    queue = serializers.CharField(
        max_length=255,
        default="default"
    )

    payload = serializers.JSONField(
        default=dict
    )

    unique_key = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        allow_null=True
    )

    scheduled_at = serializers.DateTimeField(
        required=False,
        default=timezone.now
    )

    cron_expression = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        allow_null=True
    )

    max_retries = serializers.IntegerField(
        required=False,
        default=3,
        min_value=0
    )

    backoff_strategy = serializers.ChoiceField(
        choices=["FIXED", "LINEAR", "EXPONENTIAL"],
        default="FIXED"
    )

    backoff_delay = serializers.IntegerField(
        required=False,
        default=60,
        min_value=0
    )

    batch_id = serializers.UUIDField(
        required=False,
        allow_null=True
    )

    timeout_seconds = serializers.IntegerField(
        required=False,
        default=300,
        min_value=1
    )

    def validate_name(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                "Job name cannot be empty."
            )

        return value

    def validate_unique_key(self, value):
        if value == "":
            return None

        return value

    def validate_cron_expression(self, value):
        if value == "":
            return None

        return value


class BatchJobSubmitSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    queue = serializers.CharField(max_length=255, default="default")
    jobs = serializers.ListField(
        child=serializers.DictField(),
        min_length=1
    )

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Batch name cannot be empty.")
        return value


class JobListSerializer(serializers.ModelSerializer):
    queue = serializers.CharField(source="queue.name", read_only=True)

    class Meta:
        model = Job
        fields = [
            "id",
            "name",
            "status",
            "scheduled_at",
            "created_at",
            "queue",
            "batch_id",
        ]


class JobDetailSerializer(serializers.ModelSerializer):
    queue = serializers.CharField(source="queue.name", read_only=True)
    executions = serializers.SerializerMethodField()

    class Meta:
        model = Job
        fields = [
            "id",
            "name",
            "status",
            "payload",
            "unique_key",
            "scheduled_at",
            "cron_expression",
            "retry_count",
            "max_retries",
            "backoff_strategy",
            "backoff_delay",
            "batch_id",
            "timeout_seconds",
            "created_at",
            "updated_at",
            "queue",
            "executions",
        ]

    def get_executions(self, obj):
        executions = obj.executions.all()[:10]
        return JobExecutionSerializer(executions, many=True).data


class JobExecutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobExecution
        fields = [
            "id",
            "status",
            "error_message",
            "started_at",
            "ended_at",
            "duration_ms",
            "attempt_number",
        ]


class JobLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobLog
        fields = [
            "id",
            "level",
            "message",
            "timestamp",
            "meta",
        ]


class QueueSerializer(serializers.ModelSerializer):
    class Meta:
        model = Queue
        fields = [
            "id",
            "name",
            "priority",
            "concurrency_limit",
            "is_paused",
            "retry_policy",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class QueueUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Queue
        fields = [
            "priority",
            "concurrency_limit",
            "is_paused",
            "retry_policy",
        ]


class WorkerRegistrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Worker
        fields = [
            "hostname",
            "concurrency_limit",
        ]


class WorkerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Worker
        fields = [
            "id",
            "hostname",
            "status",
            "concurrency_limit",
            "current_jobs",
            "last_heartbeat",
            "metadata",
            "created_at",
        ]


class BatchJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = BatchJob
        fields = [
            "id",
            "name",
            "total_jobs",
            "completed_jobs",
            "failed_jobs",
            "status",
            "created_at",
            "updated_at",
            "completed_at",
        ]


class DeadLetterQueueSerializer(serializers.ModelSerializer):
    job = JobDetailSerializer(read_only=True)

    class Meta:
        model = DeadLetterQueue
        fields = [
            "id",
            "job",
            "error_message",
            "failure_reason",
            "retry_count",
            "last_attempt_at",
            "created_at",
            "resolved_at",
            "resolved_by",
            "resolution_notes",
        ]


class ScheduledJobSerializer(serializers.ModelSerializer):
    queue = serializers.PrimaryKeyRelatedField(queryset=Queue.objects.all())

    class Meta:
        model = ScheduledJob
        fields = [
            "id",
            "name",
            "queue",
            "payload",
            "cron_expression",
            "next_run_at",
            "max_retries",
            "backoff_strategy",
            "backoff_delay",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class WorkflowDependencySerializer(serializers.ModelSerializer):
    job_name = serializers.CharField(source="job.name", read_only=True)
    depends_on_name = serializers.CharField(source="depends_on.name", read_only=True)
    job = serializers.PrimaryKeyRelatedField(queryset=Job.objects.all())
    depends_on = serializers.PrimaryKeyRelatedField(queryset=Job.objects.all())

    class Meta:
        model = WorkflowDependency
        fields = [
            "id",
            "job",
            "job_name",
            "depends_on",
            "depends_on_name",
            "created_at",
        ]