import json
import time
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from scheduler.models import Project, Queue, Job, Worker, JobExecution
from django.core.management import call_command


class SchedulerIntegrationTest(TransactionTestCase):
    def setUp(self):
        self.project = Project.objects.create(name="Test Project", api_key="test-key")
        self.queue = Queue.objects.create(project=self.project, name="default", priority=1)
        self.api_url = "/api/jobs/submit/"
        self.client = APIClient()
        self.client.credentials(HTTP_X_PROJECT_KEY='test-key')

    def test_full_job_lifecycle(self):
        # 1. Submit a job via API
        payload = {
            'name': 'test_job',
            'queue': 'default',
            'payload': {'data': 123}
        }
        response = self.client.post(self.api_url, payload, format='json')
        self.assertEqual(response.status_code, 201)
        job_id = response.json()['id']

        # 2. Verify job is QUEUED
        job = Job.objects.get(id=job_id)
        self.assertEqual(job.status, 'QUEUED')

        # 3. Start worker in a separate thread (but for tests we can just call claim and execute once)
        from scheduler.management.commands.run_worker import Command
        worker_cmd = Command()
        worker_cmd.project = self.project
        worker_cmd.worker = Worker.objects.create(project=self.project, hostname="test-worker", concurrency_limit=1)
        
        job_to_run = worker_cmd.claim_next_job()
        self.assertIsNotNone(job_to_run)
        self.assertEqual(str(job_to_run.id), job_id)
        
        worker_cmd.execute_job(job_to_run)
        
        # 4. Verify job is COMPLETED
        job.refresh_from_db()
        self.assertEqual(job.status, 'COMPLETED')

        # 5. Check stats API
        stats_url = "/api/stats/"
        stats_response = self.client.get(stats_url)
        self.assertEqual(stats_response.status_code, 200)
        stats_data = stats_response.json()
        self.assertTrue(any(j['id'] == job_id for j in stats_data['recent_jobs']))

    def test_retry_logic(self):
        from scheduler.management.commands.run_worker import Command
        worker_cmd = Command()
        worker_cmd.project = self.project
        worker_cmd.worker = Worker.objects.create(project=self.project, hostname="test-worker-retry", concurrency_limit=1)
        
        job = Job.objects.create(
            queue=self.queue,
            name='fail_job',
            payload={'fail': True},
            status='QUEUED',
            scheduled_at=timezone.now(),
            max_retries=2,
            backoff_strategy='FIXED',
            backoff_delay=1
        )
        
        # Mock execute_job to fail if 'fail' is in payload
        def mock_execute(job):
            if job.payload.get('fail'):
                try:
                    raise Exception("Simulated Failure")
                except Exception as e:
                    execution = JobExecution.objects.create(
                        job=job,
                        worker=worker_cmd.worker,
                        status='RUNNING',
                        started_at=timezone.now()
                    )
                    execution.status = 'FAILURE'
                    execution.error_message = str(e)
                    worker_cmd.handle_failure(job, execution)
                    execution.ended_at = timezone.now()
                    execution.save()
                    job.save()
            else:
                # Call original execute_job - but we need to avoid recursion
                worker_cmd.execute_job_original(job)
        
        worker_cmd.execute_job_original = worker_cmd.execute_job
        worker_cmd.execute_job = mock_execute
        
        # Run 1st attempt
        job = worker_cmd.claim_next_job()
        worker_cmd.execute_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, 'SCHEDULED')
        self.assertEqual(job.retry_count, 1)
        
        # Run 2nd attempt
        job.scheduled_at = timezone.now() # fast forward
        job.save()
        job = worker_cmd.claim_next_job()
        worker_cmd.execute_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, 'SCHEDULED')
        self.assertEqual(job.retry_count, 2)
        
        # Run 3rd attempt (should go to DLQ)
        job.scheduled_at = timezone.now() # fast forward
        job.save()
        job = worker_cmd.claim_next_job()
        worker_cmd.execute_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, 'DLQ')

    def test_cron_rescheduling(self):
        from scheduler.management.commands.run_worker import Command
        worker_cmd = Command()
        worker_cmd.project = self.project
        worker_cmd.worker = Worker.objects.create(project=self.project, hostname="test-worker-cron", concurrency_limit=1)
        
        job = Job.objects.create(
            queue=self.queue,
            name='cron_job',
            cron_expression='* * * * *', # every minute
            status='QUEUED',
            scheduled_at=timezone.now()
        )
        
        worker_cmd.execute_job(job)
        job.refresh_from_db()
        
        self.assertEqual(job.status, 'SCHEDULED')
        self.assertIsNotNone(job.scheduled_at)
        self.assertEqual(job.retry_count, 0)

    def test_worker_concurrency(self):
        from scheduler.management.commands.run_worker import Command
        from scheduler.models import JobExecution

        worker_cmd = Command()
        worker_cmd.project = self.project
        worker_cmd.worker = Worker.objects.create(
            project=self.project,
            hostname="test-worker-concurrency",
            concurrency_limit=2,
        )

        jobs = [
            Job.objects.create(
                queue=self.queue,
                name='send_monthly_report',
                payload={'customer': f'customer-{i}', 'report': 'monthly'},
                status='QUEUED',
                scheduled_at=timezone.now(),
            )
            for i in range(2)
        ]

        worker_cmd.concurrency = 2
        worker_cmd.active_jobs = 0

        job1 = worker_cmd.claim_next_job()
        job2 = worker_cmd.claim_next_job()

        self.assertIsNotNone(job1)
        self.assertIsNotNone(job2)
        self.assertNotEqual(job1.id, job2.id)

        worker_cmd.execute_job_wrapper(job1)
        worker_cmd.execute_job_wrapper(job2)

        for job in jobs:
            job.refresh_from_db()
            self.assertEqual(job.status, 'COMPLETED')

        executions = JobExecution.objects.filter(worker=worker_cmd.worker)
        self.assertEqual(executions.count(), 2)

    def test_dead_worker_recovers_jobs(self):
        from scheduler.management.commands.run_worker import Command
        from scheduler.models import JobExecution

        worker_cmd = Command()
        worker_cmd.project = self.project

        dead_worker = Worker.objects.create(
            project=self.project,
            hostname="dead-worker",
            concurrency_limit=1,
            status="ACTIVE",
        )

        old_heartbeat = timezone.now() - timezone.timedelta(minutes=10)

        Worker.objects.filter(id=dead_worker.id).update(
            last_heartbeat=old_heartbeat
        )

        dead_worker.refresh_from_db()

        job = Job.objects.create(
            queue=self.queue,
            name="send_monthly_report",
            payload={
                "customer": "Acme Technologies",
                "report": "Recovery Test",
            },
            status="CLAIMED",
            scheduled_at=timezone.now(),
        )

        JobExecution.objects.create(
            job=job,
            worker=dead_worker,
            status="RUNNING",
            started_at=old_heartbeat,
        )

        worker_cmd.cleanup_dead_workers()

        dead_worker.refresh_from_db()
        job.refresh_from_db()

        self.assertEqual(dead_worker.status, "DEAD")
        self.assertEqual(job.status, "QUEUED")

    def test_submit_job_invalid_project_key(self):
        payload = {
            "name": "send_monthly_report",
            "queue": "default",
            "payload": {
                "customer": "Acme",
                "report": "Monthly",
            },
        }

        # Create a new client with invalid key
        invalid_client = APIClient()
        invalid_client.credentials(HTTP_X_PROJECT_KEY='invalid-key')
        response = invalid_client.post(self.api_url, payload, format='json')

        self.assertIn(response.status_code, [401, 403])

    def test_submit_job_invalid_queue(self):
        payload = {
            "name": "send_monthly_report",
            "queue": "does-not-exist",
            "payload": {
                "customer": "Acme",
                "report": "Monthly",
            },
        }

        response = self.client.post(self.api_url, payload, format='json')

        self.assertIn(response.status_code, [400, 404])

    def test_submit_job_missing_name(self):
        payload = {
            "queue": "default",
            "payload": {
                "customer": "Acme",
            },
        }

        response = self.client.post(self.api_url, payload, format='json')

        self.assertEqual(response.status_code, 400)

    def test_project_isolation(self):
        # Create another project and queue
        other_project = Project.objects.create(
            name="Other Project",
            api_key="other-key",
        )

        Queue.objects.create(
            project=other_project,
            name="default",
            priority=1,
        )

        # Try to submit to the other project's queue
        payload = {
            "name": "send_monthly_report",
            "queue": "default",
            "payload": {
                "customer": "Other Customer",
                "report": "Other Report",
            },
        }

        response = self.client.post(self.api_url, payload, format='json')

        # The request uses test-key, so it must not create a job
        # belonging to other-key's project.
        if response.status_code == 201:
            job_id = response.json()["id"]
            job = Job.objects.get(id=job_id)

            self.assertEqual(
                job.queue.project_id,
                self.project.id,
            )
            self.assertNotEqual(
                job.queue.project_id,
                other_project.id,
            )


class SchedulerConcurrencyTest(SchedulerIntegrationTest):
    """Tests for concurrent job claiming and execution"""

    def test_concurrent_claiming_skip_locked(self):
        """Test that multiple workers can claim different jobs simultaneously without conflicts"""
        import concurrent.futures
        from django.db import connection
        from django.conf import settings
        from scheduler.management.commands.run_worker import Command

        # Skip on SQLite as it doesn't support concurrent writes
        db_engine = settings.DATABASES['default']['ENGINE']
        if 'sqlite' in db_engine:
            self.skipTest("SQLite doesn't support concurrent writes, skipping concurrent claiming test")

        # Create 10 jobs
        jobs = []
        for i in range(10):
            job = Job.objects.create(
                queue=self.queue,
                name=f'test_job_{i}',
                payload={'index': i},
                status='QUEUED',
                scheduled_at=timezone.now(),
            )
            jobs.append(job)

        # Create multiple worker commands
        workers = []
        for w in range(3):
            worker_cmd = Command()
            worker_cmd.project = self.project
            worker_cmd.worker = Worker.objects.create(
                project=self.project, 
                hostname=f"test-worker-{w}", 
                concurrency_limit=5
            )
            workers.append(worker_cmd)

        # All workers try to claim jobs concurrently
        claimed_jobs = []
        errors = []

        def worker_claim(worker_cmd):
            try:
                for _ in range(5):  # Try to claim up to 5 jobs
                    job = worker_cmd.claim_next_job()
                    if job:
                        claimed_jobs.append(str(job.id))
                    else:
                        break
            except Exception as e:
                errors.append(str(e))

        # Run concurrent claiming
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(worker_claim, w) for w in workers]
            concurrent.futures.wait(futures)

        # Verify no errors
        self.assertEqual(len(errors), 0, f"Errors during concurrent claiming: {errors}")

        # Verify no duplicate claims
        self.assertEqual(len(claimed_jobs), len(set(claimed_jobs)), "Duplicate job claims detected!")

        # Verify all claimed jobs are unique
        self.assertEqual(len(claimed_jobs), 10, "Not all jobs were claimed")

def test_queue_concurrency_limit(self):
        """Test that queue concurrency limit is respected"""
        from scheduler.management.commands.run_worker import Command
        from scheduler.models import JobExecution, Queue

        # Create a queue with concurrency limit of 2
        limited_queue = Queue.objects.create(
            project=self.project,
            name="limited-queue",
            priority=1,
            concurrency_limit=2
        )

        # Create 5 jobs in the limited queue
        for i in range(5):
            Job.objects.create(
                queue=limited_queue,
                name=f'test_job_{i}',
                payload={'index': i},
                status='QUEUED',
                scheduled_at=timezone.now(),
            )

        # Worker with concurrency 5, but queue limit is 2
        worker_cmd = Command()
        worker_cmd.project = self.project
        worker_cmd.worker = Worker.objects.create(
            project=self.project,
            hostname="test-worker-concurrency",
            concurrency_limit=5
        )
        worker_cmd.concurrency = 5
        worker_cmd.active_jobs = 0

        # Mock execute_job to keep jobs in RUNNING state (don't complete them)
        def mock_execute(job):
            execution = JobExecution.objects.create(
                job=job,
                worker=worker_cmd.worker,
                status='RUNNING',
                started_at=timezone.now()
            )
            job.status = 'RUNNING'
            job.save(update_fields=['status', 'updated_at'])
            # Don't complete the job - keep it RUNNING to test concurrency limit
        
        original_execute = worker_cmd.execute_job
        worker_cmd.execute_job = mock_execute
        
        try:
            # Should only be able to claim 2 jobs (queue limit)
            claimed = []
            for _ in range(5):
                job = worker_cmd.claim_next_job()
                if job:
                    claimed.append(job)
                else:
                    break

            self.assertEqual(len(claimed), 2, "Should only claim up to queue concurrency limit")
        finally:
            worker_cmd.execute_job = original_execute


class SchedulerWorkerLoopTest(SchedulerIntegrationTest):
    """Tests for worker daemon loop functionality"""

    def test_worker_heartbeat_updates(self):
        """Test that worker heartbeat is updated"""
        from scheduler.management.commands.run_worker import Command

        worker_cmd = Command()
        worker_cmd.project = self.project
        worker_cmd.worker = Worker.objects.create(
            project=self.project, 
            hostname="test-worker", 
            concurrency_limit=1
        )

        old_heartbeat = worker_cmd.worker.last_heartbeat
        worker_cmd.update_heartbeat()
        worker_cmd.worker.refresh_from_db()

        self.assertNotEqual(worker_cmd.worker.last_heartbeat, old_heartbeat)

    def test_worker_shutdown_sets_status(self):
        """Test that shutdown handler sets worker status to SHUTTING_DOWN"""
        from scheduler.management.commands.run_worker import Command

        worker_cmd = Command()
        worker_cmd.project = self.project
        worker_cmd.worker = Worker.objects.create(
            project=self.project, 
            hostname="test-worker", 
            concurrency_limit=1
        )

        worker_cmd.handle_shutdown(None, None)
        worker_cmd.worker.refresh_from_db()

        self.assertEqual(worker_cmd.worker.status, 'SHUTTING_DOWN')


class SchedulerScheduledJobTest(SchedulerIntegrationTest):
    """Tests for ScheduledJob cron functionality"""

    def test_scheduled_job_creates_job_when_due(self):
        """Test that ScheduledJob creates a Job when next_run_at is due"""
        from scheduler.management.commands.run_worker import Command
        from scheduler.models import ScheduledJob

        scheduled = ScheduledJob.objects.create(
            queue=self.queue,
            name='scheduled_test_job',
            payload={'test': 'data'},
            cron_expression='* * * * *',  # every minute
            next_run_at=timezone.now(),  # Due now
            is_active=True,
        )

        worker_cmd = Command()
        worker_cmd.project = self.project
        worker_cmd.worker = Worker.objects.create(
            project=self.project, 
            hostname="test-worker", 
            concurrency_limit=1
        )

        # Process scheduled jobs
        worker_cmd.process_scheduled_jobs()

        # Verify Job was created
        jobs = Job.objects.filter(name='scheduled_test_job', queue=self.queue)
        self.assertEqual(jobs.count(), 1)

        created_job = jobs.first()
        self.assertEqual(created_job.payload, {'test': 'data'})
        self.assertEqual(created_job.cron_expression, '* * * * *')
        self.assertEqual(created_job.status, 'QUEUED')

        # Verify next_run_at was updated
        scheduled.refresh_from_db()
        self.assertGreater(scheduled.next_run_at, timezone.now())

    def test_scheduled_job_not_created_when_not_due(self):
        """Test that ScheduledJob doesn't create Job when not due"""
        from scheduler.management.commands.run_worker import Command
        from scheduler.models import ScheduledJob

        future_time = timezone.now() + timezone.timedelta(hours=1)
        scheduled = ScheduledJob.objects.create(
            queue=self.queue,
            name='future_scheduled_job',
            payload={'test': 'data'},
            cron_expression='* * * * *',
            next_run_at=future_time,
            is_active=True,
        )

        worker_cmd = Command()
        worker_cmd.project = self.project
        worker_cmd.worker = Worker.objects.create(
            project=self.project, 
            hostname="test-worker", 
            concurrency_limit=1
        )

        worker_cmd.process_scheduled_jobs()

        # Verify no Job was created
        jobs = Job.objects.filter(name='future_scheduled_job', queue=self.queue)
        self.assertEqual(jobs.count(), 0)

    def test_scheduled_job_inactive_not_processed(self):
        """Test that inactive ScheduledJob is not processed"""
        from scheduler.management.commands.run_worker import Command
        from scheduler.models import ScheduledJob

        scheduled = ScheduledJob.objects.create(
            queue=self.queue,
            name='inactive_scheduled_job',
            payload={'test': 'data'},
            cron_expression='* * * * *',
            next_run_at=timezone.now(),
            is_active=False,
        )

        worker_cmd = Command()
        worker_cmd.project = self.project
        worker_cmd.worker = Worker.objects.create(
            project=self.project, 
            hostname="test-worker", 
            concurrency_limit=1
        )

        worker_cmd.process_scheduled_jobs()

        # Verify no Job was created
        jobs = Job.objects.filter(name='inactive_scheduled_job', queue=self.queue)
        self.assertEqual(jobs.count(), 0)


class SchedulerBatchJobTest(SchedulerIntegrationTest):
    """Tests for BatchJob functionality"""

    def test_batch_job_creation_and_progress(self):
        """Test batch job creation and progress tracking"""
        from scheduler.management.commands.run_worker import Command
        from scheduler.models import BatchJob

        # Create batch via API
        payload = {
            "name": "test_batch",
            "queue": "default",
            "jobs": [
                {"name": "send_monthly_report", "payload": {"customer": "cust-1"}},
                {"name": "send_monthly_report", "payload": {"customer": "cust-2"}},
                {"name": "send_monthly_report", "payload": {"customer": "cust-3"}},
            ]
        }
        response = self.client.post("/api/jobs/batch/", payload, format='json')
        self.assertEqual(response.status_code, 201)

        batch_id = response.json()['batch_id']
        batch = BatchJob.objects.get(id=batch_id)
        self.assertEqual(batch.total_jobs, 3)
        self.assertEqual(batch.status, 'PARTIAL')

        # Execute all jobs via worker
        worker_cmd = Command()
        worker_cmd.project = self.project
        worker_cmd.worker = Worker.objects.create(
            project=self.project, 
            hostname="test-worker", 
            concurrency_limit=3
        )

        jobs = Job.objects.filter(batch_id=batch_id)
        for job in jobs:
            job_to_run = worker_cmd.claim_next_job()
            if job_to_run:
                worker_cmd.execute_job(job_to_run)

        # Wait a moment for batch progress to update
        import time
        time.sleep(0.1)

        # Check batch progress
        batch.refresh_from_db()
        self.assertEqual(batch.completed_jobs, 3)
        self.assertEqual(batch.status, 'COMPLETED')
        self.assertIsNotNone(batch.completed_at)


class SchedulerDLQTest(SchedulerIntegrationTest):
    """Tests for Dead Letter Queue functionality"""

    def test_dlq_retry_via_api(self):
        """Test retrying a job from DLQ via API"""
        from scheduler.models import DeadLetterQueue

        # Create a job in DLQ
        job = Job.objects.create(
            queue=self.queue,
            name='fail_job',
            payload={'fail': True},
            status='DLQ',
            retry_count=3,
            max_retries=2,
            scheduled_at=timezone.now(),
        )
        dlq_entry = DeadLetterQueue.objects.create(
            job=job,
            error_message='Test error',
            failure_reason='Exception',
            retry_count=3,
            last_attempt_at=timezone.now(),
        )

        # Retry via API
        response = self.client.post(f"/api/dlq/{dlq_entry.id}/retry/")
        self.assertEqual(response.status_code, 200)

        job.refresh_from_db()
        dlq_entry.refresh_from_db()

        self.assertEqual(job.status, 'QUEUED')
        self.assertEqual(job.retry_count, 0)
        self.assertIsNotNone(dlq_entry.resolved_at)
        self.assertEqual(dlq_entry.resolution_notes, 'Retried via API')

    def test_job_retry_action_via_detail_api(self):
        """Test retry action via JobDetailView POST"""
        from scheduler.models import DeadLetterQueue

        job = Job.objects.create(
            queue=self.queue,
            name='dlq_job',
            payload={'test': 'data'},
            status='DLQ',
            retry_count=3,
            max_retries=2,
            scheduled_at=timezone.now(),
        )
        DeadLetterQueue.objects.create(
            job=job,
            error_message='Test error',
            failure_reason='Exception',
            retry_count=3,
            last_attempt_at=timezone.now(),
        )

        # Retry via JobDetailView POST
        response = self.client.post(f"/api/jobs/{job.id}/", {'action': 'retry'}, format='json')
        self.assertEqual(response.status_code, 200)

        job.refresh_from_db()
        self.assertEqual(job.status, 'QUEUED')
        self.assertEqual(job.retry_count, 0)

        # Verify DLQ entry is deleted
        self.assertFalse(DeadLetterQueue.objects.filter(job=job).exists())


class SchedulerTimezoneTest(SchedulerIntegrationTest):
    """Tests for timezone handling in job scheduling"""

    def test_scheduled_at_timezone_aware_conversion(self):
        """Test that naive datetime is converted to timezone-aware"""
        # Submit with naive datetime (no timezone)
        naive_dt = (timezone.now() + timezone.timedelta(hours=1)).replace(tzinfo=None)
        payload = {
            'name': 'test_job',
            'queue': 'default',
            'payload': {'data': 123},
            'scheduled_at': naive_dt.isoformat(),
        }

        response = self.client.post(self.api_url, payload, format='json')
        self.assertEqual(response.status_code, 201)

        job = Job.objects.get(id=response.json()['id'])
        # Should be timezone-aware
        self.assertIsNotNone(job.scheduled_at.tzinfo)

    def test_scheduled_at_utc_timezone(self):
        """Test that UTC datetime is handled correctly"""
        utc_dt = timezone.now() + timezone.timedelta(hours=1)
        payload = {
            'name': 'test_job',
            'queue': 'default',
            'payload': {'data': 123},
            'scheduled_at': utc_dt.isoformat(),
        }

        response = self.client.post(self.api_url, payload, format='json')
        self.assertEqual(response.status_code, 201)

        job = Job.objects.get(id=response.json()['id'])
        self.assertIsNotNone(job.scheduled_at.tzinfo)

    def test_cron_timezone_handling(self):
        """Test that cron jobs use correct timezone"""
        from scheduler.management.commands.run_worker import Command

        job = Job.objects.create(
            queue=self.queue,
            name='cron_job_tz',
            cron_expression='0 12 * * *',  # noon UTC
            status='QUEUED',
            scheduled_at=timezone.now(),
        )

        worker_cmd = Command()
        worker_cmd.project = self.project
        worker_cmd.worker = Worker.objects.create(
            project=self.project, 
            hostname="test-worker", 
            concurrency_limit=1
        )

        worker_cmd.execute_job(job)
        job.refresh_from_db()

        # Should be rescheduled to next cron occurrence
        self.assertEqual(job.status, 'SCHEDULED')
        self.assertIsNotNone(job.scheduled_at)
        self.assertIsNotNone(job.scheduled_at.tzinfo)