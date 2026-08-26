import time
import socket
import uuid
import logging
import signal
import threading
from croniter import croniter
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.db.models import Count, F
from scheduler.models import Job, Queue, Worker, JobExecution, Project, JobLog, BatchJob, DeadLetterQueue, ScheduledJob
from scheduler.handlers import execute_handler

logger = logging.getLogger(__name__)


class DistributedLock:
    """Distributed lock using PostgreSQL advisory locks (no-op for SQLite)"""

    def __init__(self, lock_id: int, timeout: int = 30):
        self.lock_id = lock_id
        self.timeout = timeout
        self.acquired = False

    def acquire(self) -> bool:
        from django.db import connection
        # SQLite doesn't support advisory locks - return True to allow execution
        if connection.vendor == 'sqlite':
            self.acquired = True
            return True
        
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s)",
                [self.lock_id]
            )
            result = cursor.fetchone()
            self.acquired = result[0] if result else False
            return self.acquired

    def release(self):
        if self.acquired:
            from django.db import connection
            if connection.vendor == 'sqlite':
                self.acquired = False
                return
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [self.lock_id])
            self.acquired = False

    def __enter__(self):
        if self.acquire():
            return self
        raise RuntimeError(f"Could not acquire lock {self.lock_id}")

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class Command(BaseCommand):
    help = 'Runs the distributed job scheduler worker daemon'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Initialize attributes that tests might access directly
        self.shutdown = False
        self.shutdown_event = threading.Event()
        self.active_jobs = 0
        self.active_jobs_lock = threading.Lock()
        self.job_futures = {}
        self.project = None
        self.worker = None
        self.concurrency = 5

    def add_arguments(self, parser):
        parser.add_argument('--concurrency', type=int, default=5, help='Number of concurrent threads')
        parser.add_argument('--project_key', type=str, required=True, help='API key of the project this worker belongs to')
        parser.add_argument('--queues', type=str, default='', help='Comma-separated list of queue names to process (empty = all)')

    def handle(self, *args, **options):
        self.concurrency = options['concurrency']
        project_key = options['project_key']
        queue_names = options['queues'].split(',') if options['queues'] else None

        try:
            self.project = Project.objects.get(api_key=project_key, is_active=True)
        except Project.DoesNotExist:
            self.stderr.write(self.style.ERROR('Invalid project key.'))
            return

        self.worker = Worker.objects.create(
            project=self.project,
            hostname=socket.gethostname(),
            concurrency_limit=self.concurrency,
            status='ACTIVE'
        )
        self.stdout.write(self.style.SUCCESS(f'Started worker {self.worker.id} on {self.worker.hostname}'))

        self.shutdown = False
        self.shutdown_event = threading.Event()
        signal.signal(signal.SIGINT, self.handle_shutdown)
        signal.signal(signal.SIGTERM, self.handle_shutdown)
        self.active_jobs = 0
        self.active_jobs_lock = threading.Lock()
        self.job_futures = {}

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            while not self.shutdown:
                # 1. Update Heartbeat and current_jobs count
                self.update_heartbeat()

                # 2. Cleanup Dead Workers & Orphan Jobs
                self.cleanup_dead_workers()

                # 3. Process ScheduledJobs (create Job instances from cron)
                self.process_scheduled_jobs()

                # 4. Claim and Execute Jobs
                with self.active_jobs_lock:
                    can_claim = self.active_jobs < self.concurrency

                if can_claim:
                    job = self.claim_next_job(queue_names)
                    if job:
                        with self.active_jobs_lock:
                            self.active_jobs += 1
                        future = executor.submit(self.execute_job_wrapper, job)
                        self.job_futures[future] = job
                    else:
                        self.shutdown_event.wait(1)
                else:
                    # Check for completed futures
                    self.check_completed_futures()
                    self.shutdown_event.wait(0.1)

            # Wait for all running jobs to complete
            self.wait_for_running_jobs(executor)

        self.worker.status = 'DEAD'
        self.worker.current_jobs = 0
        self.worker.save()
        self.stdout.write(self.style.WARNING(f'Worker {self.worker.id} shut down gracefully'))

    def handle_shutdown(self, signum, frame):
        self.stdout.write(self.style.WARNING('Shutdown signal received...'))
        self.shutdown = True
        self.worker.status = 'SHUTTING_DOWN'
        self.worker.save()
        self.shutdown_event.set()

    def update_heartbeat(self):
        self.worker.last_heartbeat = timezone.now()
        self.worker.current_jobs = self.active_jobs
        self.worker.save(update_fields=['last_heartbeat', 'current_jobs', 'updated_at'])

    def check_completed_futures(self):
        done_futures = [f for f in self.job_futures if f.done()]
        for future in done_futures:
            job = self.job_futures.pop(future)
            with self.active_jobs_lock:
                self.active_jobs -= 1
            try:
                future.result()
            except Exception as e:
                logger.error(f"Job {job.id} raised exception: {e}")

    def wait_for_running_jobs(self, executor):
        self.stdout.write(self.style.WARNING('Waiting for running jobs to complete...'))
        for future in as_completed(self.job_futures.keys()):
            job = self.job_futures[future]
            try:
                future.result()
            except Exception as e:
                logger.error(f"Job {job.id} raised exception during shutdown: {e}")

    def claim_next_job(self, queue_names=None):
        with transaction.atomic():
            # Find active queues
            queues_qs = Queue.objects.filter(project=self.project, is_paused=False)
            if queue_names:
                queues_qs = queues_qs.filter(name__in=queue_names)

            # Check queue-level concurrency: count running + claimed jobs per queue
            running_counts = Job.objects.filter(
                queue__in=queues_qs,
                status__in=['RUNNING', 'CLAIMED']
            ).values('queue').annotate(count=Count('id'))

            queue_running = {r['queue']: r['count'] for r in running_counts}

            # Get queues with available capacity
            available_queues = []
            for queue in queues_qs:
                running = queue_running.get(queue.id, 0)
                if running < queue.concurrency_limit:
                    available_queues.append(queue)

            if not available_queues:
                return None

            # Atomically claim the next job from available queues
            job = Job.objects.filter(
                queue__in=available_queues,
                status__in=['QUEUED', 'SCHEDULED'],
                scheduled_at__lte=timezone.now()
            ).select_for_update(
                skip_locked=True
            ).order_by(
                '-queue__priority',
                'scheduled_at',
                'created_at'
            ).first()

            if job:
                job.status = 'CLAIMED'
                job.save(update_fields=['status', 'updated_at'])
                return job
        return None

    def execute_job_wrapper(self, job):
        try:
            self.execute_job(job)
        finally:
            pass  # active_jobs decremented in check_completed_futures

    def execute_job(self, job):
        execution = JobExecution.objects.create(
            job=job,
            worker=self.worker,
            status='RUNNING',
            started_at=timezone.now(),
            attempt_number=job.retry_count + 1
        )

        # Create initial log
        JobLog.objects.create(
            execution=execution,
            level='INFO',
            message=f'Job started on worker {self.worker.hostname}',
            meta={'worker_id': str(self.worker.id)}
        )

        job.status = 'RUNNING'
        job.save(update_fields=['status', 'updated_at'])

        try:
            logger.info(f"Executing job {job.name} ({job.id})")

            # Execute with timeout
            result = self.execute_with_timeout(job, execution)

            logger.info(f"Job {job.name} ({job.id}) completed with result: {result}")

            execution.status = 'SUCCESS'
            JobLog.objects.create(
                execution=execution,
                level='INFO',
                message=f'Job completed successfully',
                meta={'result': str(result)[:500]}
            )

            self.handle_success(job, execution)

        except Exception as e:
            execution.status = 'FAILURE'
            execution.error_message = str(e)
            JobLog.objects.create(
                execution=execution,
                level='ERROR',
                message=f'Job failed: {str(e)}',
                meta={'error_type': type(e).__name__}
            )
            self.handle_failure(job, execution, error_type=type(e).__name__)

        finally:
            execution.ended_at = timezone.now()
            execution.duration_ms = int(
                (execution.ended_at - execution.started_at).total_seconds() * 1000
            )
            execution.save()

            job.save()

            # Update batch job progress if applicable (after job.save() so status is persisted)
            if job.batch_id:
                self.update_batch_progress(job.batch_id)

    def execute_with_timeout(self, job, execution):
        """Execute job handler with timeout"""
        import concurrent.futures
        from concurrent.futures import ThreadPoolExecutor as TPE

        timeout = job.timeout_seconds or 300

        with TPE(max_workers=1) as executor:
            future = executor.submit(execute_handler, job)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                raise Exception(f"Job timed out after {timeout} seconds")

    def handle_success(self, job, execution):
        if job.cron_expression:
            # Handle recurring jobs - reschedule
            job.status = 'SCHEDULED'
            iter = croniter(job.cron_expression, timezone.now())
            job.scheduled_at = iter.get_next(timezone.datetime)
            job.retry_count = 0  # Reset retry count for next run
        else:
            job.status = 'COMPLETED'

    def handle_failure(self, job, execution, error_type=None):
        # Get retry policy from queue or use job defaults
        queue_policy = job.queue.retry_policy or {}
        max_retries = queue_policy.get('max_retries', job.max_retries)
        backoff_strategy = queue_policy.get('backoff_strategy', job.backoff_strategy)
        backoff_delay = queue_policy.get('backoff_delay', job.backoff_delay)

        if job.retry_count < max_retries:
            job.retry_count += 1
            job.status = 'SCHEDULED'

            # Backoff Logic
            delay = backoff_delay
            if backoff_strategy == 'LINEAR':
                delay = backoff_delay * job.retry_count
            elif backoff_strategy == 'EXPONENTIAL':
                delay = backoff_delay * (2 ** (job.retry_count - 1))

            job.scheduled_at = timezone.now() + timezone.timedelta(seconds=delay)

            JobLog.objects.create(
                execution=execution,
                level='WARNING',
                message=f'Job failed, scheduling retry {job.retry_count}/{max_retries} in {delay}s',
                meta={'retry_count': job.retry_count, 'max_retries': max_retries, 'delay': delay}
            )
        else:
            job.status = 'DLQ'
            JobLog.objects.create(
                execution=execution,
                level='ERROR',
                message=f'Job moved to DLQ after {job.retry_count} retries',
                meta={'final_error': execution.error_message}
            )

            # Create DLQ entry
            DeadLetterQueue.objects.create(
                job=job,
                error_message=execution.error_message or 'Unknown error',
                failure_reason=error_type or 'Exception',
                retry_count=job.retry_count,
                last_attempt_at=execution.ended_at or timezone.now()
            )

    def cleanup_dead_workers(self):
        # Mark workers as DEAD if no heartbeat for 5 minutes
        timeout = timezone.now() - timezone.timedelta(minutes=5)
        dead_workers = Worker.objects.filter(last_heartbeat__lt=timeout).exclude(status='DEAD')

        for dw in dead_workers:
            dw.status = 'DEAD'
            dw.save(update_fields=['status', 'updated_at'])
            # Recover jobs claimed by this worker - only if latest execution is by this worker
            from django.db.models import Subquery, OuterRef
            latest_execution_subquery = JobExecution.objects.filter(
                job=OuterRef('pk')
            ).order_by('-started_at').values('worker_id')[:1]
            
            Job.objects.filter(
                status__in=['CLAIMED', 'RUNNING']
            ).annotate(
                latest_worker_id=Subquery(latest_execution_subquery)
            ).filter(
                latest_worker_id=dw.id
            ).update(status='QUEUED')

    def process_scheduled_jobs(self):
        """Process ScheduledJob models and create Job instances when due"""
        now = timezone.now()
        due_scheduled = ScheduledJob.objects.filter(
            queue__project=self.project,
            is_active=True,
            next_run_at__lte=now
        ).select_related('queue')

        for scheduled in due_scheduled:
            # Use distributed lock to avoid duplicate creation
            lock_id = hash(f"scheduled_{scheduled.id}") % 2147483647
            with DistributedLock(lock_id, timeout=5) as lock:
                # Double-check after acquiring lock
                scheduled.refresh_from_db()
                if scheduled.next_run_at > now:
                    continue

                # Create the job
                job = Job.objects.create(
                    queue=scheduled.queue,
                    name=scheduled.name,
                    payload=scheduled.payload,
                    status='QUEUED',
                    scheduled_at=timezone.now(),
                    cron_expression=scheduled.cron_expression,
                    max_retries=scheduled.max_retries,
                    backoff_strategy=scheduled.backoff_strategy,
                    backoff_delay=scheduled.backoff_delay,
                )

                # Update next run time
                iter = croniter(scheduled.cron_expression, now)
                scheduled.next_run_at = iter.get_next(timezone.datetime)
                scheduled.save(update_fields=['next_run_at', 'updated_at'])

                logger.info(f"Created scheduled job {job.id} from ScheduledJob {scheduled.id}")

    def update_batch_progress(self, batch_id):
        """Update BatchJob progress counters"""
        with transaction.atomic():
            batch = BatchJob.objects.select_for_update().filter(id=batch_id).first()
            if not batch:
                return

            jobs = Job.objects.filter(batch_id=batch_id)
            completed = jobs.filter(status='COMPLETED').count()
            failed = jobs.filter(status='DLQ').count()

            batch.completed_jobs = completed
            batch.failed_jobs = failed

            if completed + failed >= batch.total_jobs:
                batch.status = 'COMPLETED' if failed == 0 else 'FAILED'
                batch.completed_at = timezone.now()
            elif completed + failed > 0:
                batch.status = 'PARTIAL'

            batch.save(update_fields=['completed_jobs', 'failed_jobs', 'status', 'completed_at', 'updated_at'])