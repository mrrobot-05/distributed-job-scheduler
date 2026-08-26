from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .serializers import (
    JobSubmitSerializer, JobListSerializer, JobDetailSerializer,
    WorkerRegistrationSerializer, QueueSerializer, QueueUpdateSerializer,
    WorkerSerializer, BatchJobSubmitSerializer, BatchJobSerializer,
    JobLogSerializer, DeadLetterQueueSerializer, ScheduledJobSerializer,
    WorkflowDependencySerializer
)
from .authentication import ProjectKeyAuthentication, IsProjectAuthenticated
from .pagination import JobPagination

from functools import wraps
from django.http import JsonResponse
from datetime import datetime
from django.utils import timezone
from django.shortcuts import get_object_or_404, render
from django.contrib.auth.decorators import login_required
from .models import (
    Project, Queue, Job, Worker,
    BatchJob, JobLog, DeadLetterQueue, ScheduledJob,
    WorkflowDependency
)
from django.db import IntegrityError
from django.db.models import Count


def api_key_required(f):
    @wraps(f)
    def decorated_function(request, *args, **kwargs):
        api_key = request.headers.get('X-Project-Key')
        if not api_key:
            return JsonResponse({'error': 'Missing X-Project-Key header'}, status=401)

        try:
            project = Project.objects.get(api_key=api_key, is_active=True)
            request.project = project
        except Project.DoesNotExist:
            return JsonResponse({'error': 'Invalid API Key'}, status=401)

        return f(request, *args, **kwargs)
    return decorated_function


@api_key_required
def job_retry(request, job_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    job = get_object_or_404(Job, id=job_id, queue__project=request.project)

    if job.status not in ['FAILED', 'DLQ']:
        return JsonResponse({'error': 'Only failed or DLQ jobs can be retried'}, status=400)

    job.status = 'QUEUED'
    job.retry_count = 0
    job.scheduled_at = timezone.now()
    job.save()

    DeadLetterQueue.objects.filter(job=job).delete()

    return JsonResponse({'id': job.id, 'status': 'queued'})


@login_required
def dashboard(request):
    return render(request, 'scheduler/dashboard.html')


@login_required
def job_explorer(request):
    return render(request, 'scheduler/job_explorer.html')


class StatsView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def get(self, request):
        project = request.auth
        active_workers = Worker.objects.filter(project=project, status='ACTIVE').count()
        jobs_queued = Job.objects.filter(queue__project=project, status='QUEUED').count()
        jobs_scheduled = Job.objects.filter(queue__project=project, status='SCHEDULED').count()
        jobs_claimed = Job.objects.filter(queue__project=project, status='CLAIMED').count()
        jobs_running = Job.objects.filter(queue__project=project, status='RUNNING').count()
        jobs_completed = Job.objects.filter(queue__project=project, status='COMPLETED').count()
        jobs_failed = Job.objects.filter(queue__project=project, status='FAILED').count()
        jobs_dlq = Job.objects.filter(queue__project=project, status='DLQ').count()

        queues = Queue.objects.filter(project=project)

        workers = Worker.objects.filter(
            project=project,
            status__in=['ACTIVE', 'SHUTTING_DOWN']
        ).order_by('-last_heartbeat')[:10]

        recent_jobs = Job.objects.filter(
            queue__project=project
        ).select_related('queue').order_by('-created_at')[:20]

        return Response({
            'active_workers': active_workers,
            'jobs_queued': jobs_queued,
            'jobs_scheduled': jobs_scheduled,
            'jobs_claimed': jobs_claimed,
            'jobs_running': jobs_running,
            'jobs_completed': jobs_completed,
            'jobs_failed': jobs_failed,
            'jobs_dlq': jobs_dlq,
            'queues': [{
                'id': q.id,
                'name': q.name,
                'priority': q.priority,
                'concurrency_limit': q.concurrency_limit,
                'is_paused': q.is_paused,
            } for q in queues],
            'workers': [{
                'id': str(w.id),
                'hostname': w.hostname,
                'status': w.status,
                'concurrency_limit': w.concurrency_limit,
                'current_jobs': w.current_jobs,
                'last_heartbeat': w.last_heartbeat.isoformat()
            } for w in workers],
            'recent_jobs': [{
                'id': str(j.id),
                'name': j.name,
                'status': j.status,
                'queue': j.queue.name,
                'scheduled_at': j.scheduled_at.isoformat()
            } for j in recent_jobs]
        })


class SubmitJobView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def post(self, request):
        serializer = JobSubmitSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {
                    "error": "Validation failed",
                    "details": serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        project = request.auth

        try:
            queue = Queue.objects.get(
                name=data["queue"],
                project=project,
            )
        except Queue.DoesNotExist:
            return Response(
                {
                    "error": "Queue not found",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            job = Job.objects.create(
                queue=queue,
                name=data["name"],
                payload=data["payload"],
                unique_key=data.get("unique_key"),
                scheduled_at=data["scheduled_at"],
                cron_expression=data.get("cron_expression"),
                max_retries=data["max_retries"],
                backoff_strategy=data["backoff_strategy"],
                backoff_delay=data["backoff_delay"],
                batch_id=data.get("batch_id"),
                timeout_seconds=data.get("timeout_seconds", 300),
            )
        except IntegrityError:
            return Response(
                {
                    "error": "A job with this unique_key already exists",
                },
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            {
                "id": str(job.id),
                "name": job.name,
                "status": job.status,
                "queue": job.queue.name,
                "scheduled_at": job.scheduled_at,
            },
            status=status.HTTP_201_CREATED,
        )


class JobListView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def get(self, request):
        project = request.auth

        jobs = (
            Job.objects
            .filter(queue__project=project)
            .select_related("queue")
            .order_by("-created_at")
        )

        job_status = request.query_params.get("status")
        if job_status:
            jobs = jobs.filter(status=job_status)

        queue_name = request.query_params.get("queue")
        if queue_name:
            jobs = jobs.filter(queue__name=queue_name)

        paginator = JobPagination()
        page = paginator.paginate_queryset(jobs, request)

        serializer = JobListSerializer(page, many=True)

        return paginator.get_paginated_response(
            serializer.data
        )


class JobDetailView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def get(self, request, job_id):
        project = request.auth

        try:
            job = (
                Job.objects
                .select_related("queue")
                .prefetch_related("executions")
                .get(
                    id=job_id,
                    queue__project=project,
                )
            )
        except Job.DoesNotExist:
            return Response(
                {
                    "error": "Job not found"
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = JobDetailSerializer(job)

        return Response(serializer.data)

    def post(self, request, job_id):
        project = request.auth
        job = get_object_or_404(Job, id=job_id, queue__project=project)

        action = request.data.get('action')
        if action == 'retry':
            if job.status not in ['FAILED', 'DLQ']:
                return Response(
                    {"error": "Only failed or DLQ jobs can be retried"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            job.status = 'QUEUED'
            job.retry_count = 0
            job.scheduled_at = timezone.now()
            job.save()
            DeadLetterQueue.objects.filter(job=job).delete()
            return Response({"id": str(job.id), "status": "queued"})

        elif action == 'cancel':
            if job.status not in ['QUEUED', 'SCHEDULED', 'CLAIMED']:
                return Response(
                    {"error": "Only queued, scheduled, or claimed jobs can be cancelled"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            job.status = 'FAILED'
            job.save()
            return Response({"id": str(job.id), "status": "cancelled"})

        return Response({"error": "Invalid action"}, status=status.HTTP_400_BAD_REQUEST)


class JobLogsView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def get(self, request, job_id):
        project = request.auth
        job = get_object_or_404(Job, id=job_id, queue__project=project)

        execution_id = request.query_params.get('execution_id')
        executions = job.executions.all().order_by('-started_at')
        if execution_id:
            executions = executions.filter(id=execution_id)

        logs = JobLog.objects.filter(execution__in=executions).order_by('timestamp')

        paginator = JobPagination()
        page = paginator.paginate_queryset(logs, request)

        serializer = JobLogSerializer(page, many=True)

        return paginator.get_paginated_response(serializer.data)


class QueueListView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def get(self, request):
        project = request.auth
        queues = Queue.objects.filter(project=project).order_by('-priority', 'name')
        serializer = QueueSerializer(queues, many=True)
        return Response(serializer.data)

    def post(self, request):
        project = request.auth
        serializer = QueueSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        queue = serializer.save(project=project)
        return Response(QueueSerializer(queue).data, status=status.HTTP_201_CREATED)


class QueueDetailView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def get(self, request, queue_id):
        project = request.auth
        queue = get_object_or_404(Queue, id=queue_id, project=project)
        serializer = QueueSerializer(queue)
        return Response(serializer.data)

    def patch(self, request, queue_id):
        project = request.auth
        queue = get_object_or_404(Queue, id=queue_id, project=project)
        serializer = QueueUpdateSerializer(queue, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        serializer.save()
        return Response(QueueSerializer(queue).data)

    def delete(self, request, queue_id):
        project = request.auth
        queue = get_object_or_404(Queue, id=queue_id, project=project)
        queue.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class QueuePauseView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def post(self, request, queue_id):
        project = request.auth
        queue = get_object_or_404(Queue, id=queue_id, project=project)
        queue.is_paused = True
        queue.save(update_fields=['is_paused', 'updated_at'])
        return Response(QueueSerializer(queue).data)


class QueueResumeView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def post(self, request, queue_id):
        project = request.auth
        queue = get_object_or_404(Queue, id=queue_id, project=project)
        queue.is_paused = False
        queue.save(update_fields=['is_paused', 'updated_at'])
        return Response(QueueSerializer(queue).data)


class QueueStatsView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def get(self, request, queue_id):
        project = request.auth
        queue = get_object_or_404(Queue, id=queue_id, project=project)

        stats = Job.objects.filter(queue=queue).values('status').annotate(count=Count('id'))
        status_counts = {s['status']: s['count'] for s in stats}

        return Response({
            'queue': queue.name,
            'total_jobs': sum(status_counts.values()),
            'by_status': status_counts,
            'concurrency_limit': queue.concurrency_limit,
            'is_paused': queue.is_paused,
        })


class WorkerRegisterView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def post(self, request):
        serializer = WorkerRegistrationSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {
                    "error": "Validation failed",
                    "details": serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        project = request.auth

        worker = Worker.objects.create(
            project=project,
            hostname=serializer.validated_data["hostname"],
            concurrency_limit=serializer.validated_data.get(
                "concurrency_limit",
                10,
            ),
        )

        return Response(
            {
                "id": str(worker.id),
                "hostname": worker.hostname,
                "status": worker.status,
                "concurrency_limit": worker.concurrency_limit,
                "last_heartbeat": worker.last_heartbeat,
            },
            status=status.HTTP_201_CREATED,
        )


class WorkerHeartbeatView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def post(self, request):
        worker_id = request.data.get("worker_id")

        if not worker_id:
            return Response(
                {
                    "error": "worker_id is required"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            worker = Worker.objects.get(
                id=worker_id,
                project=request.auth,
            )
        except Worker.DoesNotExist:
            return Response(
                {
                    "error": "Worker not found"
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        worker.last_heartbeat = timezone.now()

        if worker.status == "DEAD":
            worker.status = "ACTIVE"

        worker.save(update_fields=["last_heartbeat", "status"])

        return Response({
            "id": str(worker.id),
            "hostname": worker.hostname,
            "status": worker.status,
            "last_heartbeat": worker.last_heartbeat,
        })


class WorkerListView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def get(self, request):
        project = request.auth
        workers = Worker.objects.filter(project=project).order_by('-last_heartbeat')
        serializer = WorkerSerializer(workers, many=True)
        return Response(serializer.data)


class BatchJobSubmitView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def post(self, request):
        serializer = BatchJobSubmitSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data
        project = request.auth

        queue = get_object_or_404(Queue, name=data["queue"], project=project)

        batch = BatchJob.objects.create(
            project=project,
            name=data["name"],
            total_jobs=len(data["jobs"]),
            status='PENDING'
        )

        batch_id = batch.id
        jobs_to_create = []
        for job_data in data["jobs"]:
            scheduled_at = job_data.get("scheduled_at", timezone.now())
            jobs_to_create.append(Job(
                queue=queue,
                name=job_data["name"],
                payload=job_data.get("payload", {}),
                status='QUEUED',
                unique_key=job_data.get("unique_key"),
                scheduled_at=scheduled_at,
                cron_expression=job_data.get("cron_expression"),
                max_retries=job_data.get("max_retries", 3),
                backoff_strategy=job_data.get("backoff_strategy", "FIXED"),
                backoff_delay=job_data.get("backoff_delay", 60),
                batch_id=batch_id,
                timeout_seconds=job_data.get("timeout_seconds", 300)
            ))

        Job.objects.bulk_create(jobs_to_create)
        batch.status = 'PARTIAL'
        batch.save()

        return Response(
            {
                "batch_id": str(batch.id),
                "name": batch.name,
                "total_jobs": batch.total_jobs,
                "status": "created"
            },
            status=status.HTTP_201_CREATED,
        )


class BatchJobDetailView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def get(self, request, batch_id):
        project = request.auth
        batch = get_object_or_404(BatchJob, id=batch_id, project=project)

        jobs = Job.objects.filter(batch_id=batch_id).order_by('-created_at')

        serializer = BatchJobSerializer(batch)
        data = serializer.data
        data['jobs'] = JobListSerializer(jobs, many=True).data

        return Response(data)


class DeadLetterQueueView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def get(self, request):
        project = request.auth
        dlq = DeadLetterQueue.objects.filter(
            job__queue__project=project
        ).select_related('job', 'job__queue').order_by('-created_at')

        paginator = JobPagination()
        page = paginator.paginate_queryset(dlq, request)

        serializer = DeadLetterQueueSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class DeadLetterQueueRetryView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def post(self, request, dlq_id):
        project = request.auth
        dlq_entry = get_object_or_404(DeadLetterQueue, id=dlq_id, job__queue__project=project)
        job = dlq_entry.job

        job.status = 'QUEUED'
        job.retry_count = 0
        job.scheduled_at = timezone.now()
        job.save()

        dlq_entry.resolved_at = timezone.now()
        dlq_entry.resolved_by = 'api'
        dlq_entry.resolution_notes = 'Retried via API'
        dlq_entry.save()

        return Response({
            "id": str(job.id),
            "status": "queued"
        })


class ScheduledJobListView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def get(self, request):
        project = request.auth
        scheduled = ScheduledJob.objects.filter(
            queue__project=project
        ).select_related('queue').order_by('next_run_at')
        serializer = ScheduledJobSerializer(scheduled, many=True)
        return Response(serializer.data)

    def post(self, request):
        project = request.auth
        serializer = ScheduledJobSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        queue = get_object_or_404(Queue, id=serializer.validated_data["queue"].id, project=project)

        from croniter import croniter
        iter = croniter(serializer.validated_data["cron_expression"], timezone.now())
        next_run = iter.get_next(datetime)

        scheduled = ScheduledJob.objects.create(
            queue=queue,
            name=serializer.validated_data["name"],
            payload=serializer.validated_data.get("payload", {}),
            cron_expression=serializer.validated_data["cron_expression"],
            next_run_at=next_run,
            max_retries=serializer.validated_data.get("max_retries", 3),
            backoff_strategy=serializer.validated_data.get("backoff_strategy", "FIXED"),
            backoff_delay=serializer.validated_data.get("backoff_delay", 60),
            is_active=serializer.validated_data.get("is_active", True),
        )
        return Response(ScheduledJobSerializer(scheduled).data, status=status.HTTP_201_CREATED)


class ScheduledJobDetailView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def get(self, request, scheduled_id):
        project = request.auth
        scheduled = get_object_or_404(ScheduledJob, id=scheduled_id, queue__project=project)
        serializer = ScheduledJobSerializer(scheduled)
        return Response(serializer.data)

    def patch(self, request, scheduled_id):
        project = request.auth
        scheduled = get_object_or_404(ScheduledJob, id=scheduled_id, queue__project=project)
        serializer = ScheduledJobSerializer(scheduled, data=request.data, partial=True, context={'request': request})
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        serializer.save()
        return Response(ScheduledJobSerializer(scheduled).data)

    def delete(self, request, scheduled_id):
        project = request.auth
        scheduled = get_object_or_404(ScheduledJob, id=scheduled_id, queue__project=project)
        scheduled.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkflowDependencyListView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def get(self, request):
        project = request.auth
        deps = WorkflowDependency.objects.filter(
            job__queue__project=project
        ).select_related('job', 'depends_on', 'job__queue', 'depends_on__queue')
        serializer = WorkflowDependencySerializer(deps, many=True)
        return Response(serializer.data)

    def post(self, request):
        project = request.auth
        serializer = WorkflowDependencySerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(
                {"error": "Validation failed", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        job = get_object_or_404(Job, id=serializer.validated_data["job"].id, queue__project=project)
        depends_on = get_object_or_404(Job, id=serializer.validated_data["depends_on"].id, queue__project=project)

        if job == depends_on:
            return Response(
                {"error": "Job cannot depend on itself"},
                status=status.HTTP_400_BAD_REQUEST
            )

        dep, created = WorkflowDependency.objects.get_or_create(
            job=job,
            depends_on=depends_on
        )
        return Response(WorkflowDependencySerializer(dep).data, status=status.HTTP_201_CREATED)


class WorkflowDependencyDetailView(APIView):
    authentication_classes = [ProjectKeyAuthentication]
    permission_classes = [IsProjectAuthenticated]

    def delete(self, request, dep_id):
        project = request.auth
        dep = get_object_or_404(WorkflowDependency, id=dep_id, job__queue__project=project)
        dep.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
