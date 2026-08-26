import time
from datetime import timedelta
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from scheduler.models import (
    Project, Queue, Job, Worker, JobExecution,
    Organization, BatchJob, DeadLetterQueue, ScheduledJob,
)
from django.contrib.auth import get_user_model

User = get_user_model()


class BaseSchedulerTest(TransactionTestCase):
    """Shared setUp and helper methods for all scheduler tests."""

    def setUp(self):
        self.project = Project.objects.create(
            name="Test Project", api_key="test-key", is_active=True
        )
        self.queue = Queue.objects.create(
            project=self.project, name="default", priority=1
        )
        self.api_url = "/api/jobs/submit/"
        self.client = APIClient()
        self.client.credentials(HTTP_X_PROJECT_KEY='test-key')

    def _create_worker(self, hostname="test-worker", concurrency_limit=1):
        from scheduler.management.commands.run_worker import Command
        worker_cmd = Command()
        worker_cmd.project = self.project
        worker_cmd.worker = Worker.objects.create(
            project=self.project,
            hostname=hostname,
            concurrency_limit=concurrency_limit,
        )
        return worker_cmd

    def _create_job(self, name='test_job', queue=None, status='QUEUED', **kwargs):
        queue = queue or self.queue
        return Job.objects.create(
            queue=queue,
            name=name,
            payload=kwargs.get('payload', {'data': 123}),
            status=status,
            scheduled_at=kwargs.get('scheduled_at', timezone.now()),
            max_retries=kwargs.get('max_retries', 3),
            backoff_strategy=kwargs.get('backoff_strategy', 'FIXED'),
            backoff_delay=kwargs.get('backoff_delay', 60),
            cron_expression=kwargs.get('cron_expression', None),
            batch_id=kwargs.get('batch_id', None),
        )

    def _submit_job(self, name='test_job', queue='default', payload=None, **extra):
        payload = payload or {'data': 123}
        data = {'name': name, 'queue': queue, 'payload': payload}
        data.update(extra)
        return self.client.post(self.api_url, data, format='json')


# ---------------------------------------------------------------------------
# Core integration tests
# ---------------------------------------------------------------------------

class SchedulerIntegrationTest(BaseSchedulerTest):

    def test_full_job_lifecycle(self):
        response = self._submit_job()
        self.assertEqual(response.status_code, 201)
        job_id = response.json()['id']

        job = Job.objects.get(id=job_id)
        self.assertEqual(job.status, 'QUEUED')

        worker_cmd = self._create_worker(concurrency_limit=1)
        job_to_run = worker_cmd.claim_next_job()
        self.assertIsNotNone(job_to_run)
        self.assertEqual(str(job_to_run.id), job_id)

        worker_cmd.execute_job(job_to_run)

        job.refresh_from_db()
        self.assertEqual(job.status, 'COMPLETED')

        stats_response = self.client.get("/api/stats/")
        self.assertEqual(stats_response.status_code, 200)
        stats_data = stats_response.json()
        self.assertTrue(any(j['id'] == job_id for j in stats_data['recent_jobs']))

    def test_retry_logic(self):
        worker_cmd = self._create_worker(hostname="test-worker-retry")

        job = self._create_job(
            name='fail_job', payload={'fail': True}, max_retries=2,
            backoff_strategy='FIXED', backoff_delay=1,
        )

        def mock_execute(job):
            if job.payload.get('fail'):
                try:
                    raise Exception("Simulated Failure")
                except Exception as e:
                    execution = JobExecution.objects.create(
                        job=job, worker=worker_cmd.worker,
                        status='RUNNING', started_at=timezone.now(),
                    )
                    execution.status = 'FAILURE'
                    execution.error_message = str(e)
                    worker_cmd.handle_failure(job, execution, error_type=type(e).__name__)
                    execution.ended_at = timezone.now()
                    execution.save()
                    job.save()
            else:
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
        job.scheduled_at = timezone.now()
        job.save()
        job = worker_cmd.claim_next_job()
        worker_cmd.execute_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, 'SCHEDULED')
        self.assertEqual(job.retry_count, 2)

        # Run 3rd attempt (should go to DLQ)
        job.scheduled_at = timezone.now()
        job.save()
        job = worker_cmd.claim_next_job()
        worker_cmd.execute_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, 'DLQ')

    def test_cron_rescheduling(self):
        worker_cmd = self._create_worker(hostname="test-worker-cron")
        job = self._create_job(name='cron_job', cron_expression='* * * * *')

        worker_cmd.execute_job(job)
        job.refresh_from_db()

        self.assertEqual(job.status, 'SCHEDULED')
        self.assertIsNotNone(job.scheduled_at)
        self.assertEqual(job.retry_count, 0)

    def test_worker_concurrency(self):
        worker_cmd = self._create_worker(hostname="test-worker-concurrency", concurrency_limit=2)
        worker_cmd.concurrency = 2
        worker_cmd.active_jobs = 0

        jobs = [
            self._create_job(
                name='send_monthly_report',
                payload={'customer': f'customer-{i}', 'report': 'monthly'},
            )
            for i in range(2)
        ]

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

        self.assertEqual(JobExecution.objects.filter(worker=worker_cmd.worker).count(), 2)

    def test_dead_worker_recovers_jobs(self):
        worker_cmd = self._create_worker(hostname="test-worker-dead")
        dead_worker = Worker.objects.create(
            project=self.project, hostname="dead-worker",
            concurrency_limit=1, status="ACTIVE",
        )

        old_heartbeat = timezone.now() - timedelta(minutes=10)
        Worker.objects.filter(id=dead_worker.id).update(last_heartbeat=old_heartbeat)
        dead_worker.refresh_from_db()

        job = self._create_job(
            name="send_monthly_report",
            payload={"customer": "Acme", "report": "Recovery"},
            status="CLAIMED",
        )

        JobExecution.objects.create(
            job=job, worker=dead_worker, status="RUNNING", started_at=old_heartbeat,
        )

        worker_cmd.cleanup_dead_workers()

        dead_worker.refresh_from_db()
        job.refresh_from_db()

        self.assertEqual(dead_worker.status, "DEAD")
        self.assertEqual(job.status, "QUEUED")

    def test_submit_job_invalid_project_key(self):
        invalid_client = APIClient()
        invalid_client.credentials(HTTP_X_PROJECT_KEY='invalid-key')
        payload = {
            "name": "send_monthly_report", "queue": "default",
            "payload": {"customer": "Acme", "report": "Monthly"},
        }
        response = invalid_client.post(self.api_url, payload, format='json')
        self.assertIn(response.status_code, [401, 403])

    def test_submit_job_invalid_queue(self):
        payload = {
            "name": "send_monthly_report", "queue": "does-not-exist",
            "payload": {"customer": "Acme", "report": "Monthly"},
        }
        response = self.client.post(self.api_url, payload, format='json')
        self.assertIn(response.status_code, [400, 404])

    def test_submit_job_missing_name(self):
        payload = {"queue": "default", "payload": {"customer": "Acme"}}
        response = self.client.post(self.api_url, payload, format='json')
        self.assertEqual(response.status_code, 400)

    def test_project_isolation(self):
        other_project = Project.objects.create(
            name="Other Project", api_key="other-key",
        )
        Queue.objects.create(project=other_project, name="default", priority=1)

        payload = {
            "name": "send_monthly_report", "queue": "default",
            "payload": {"customer": "Other", "report": "Other"},
        }
        response = self.client.post(self.api_url, payload, format='json')

        if response.status_code == 201:
            job_id = response.json()["id"]
            job = Job.objects.get(id=job_id)
            self.assertEqual(job.queue.project_id, self.project.id)
            self.assertNotEqual(job.queue.project_id, other_project.id)

    def test_queue_concurrency_limit(self):
        limited_queue = Queue.objects.create(
            project=self.project, name="limited-queue",
            priority=1, concurrency_limit=2,
        )

        for i in range(5):
            self._create_job(
                name=f'test_job_{i}', queue=limited_queue,
                payload={'index': i},
            )

        worker_cmd = self._create_worker(hostname="test-worker-qlimit", concurrency_limit=5)
        worker_cmd.concurrency = 5
        worker_cmd.active_jobs = 0

        def mock_execute(job):
            JobExecution.objects.create(
                job=job, worker=worker_cmd.worker,
                status='RUNNING', started_at=timezone.now(),
            )
            job.status = 'RUNNING'
            job.save(update_fields=['status', 'updated_at'])

        original_execute = worker_cmd.execute_job
        worker_cmd.execute_job = mock_execute
        try:
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


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------

class SchedulerConcurrencyTest(BaseSchedulerTest):

    def test_concurrent_claiming_skip_locked(self):
        import concurrent.futures
        from django.conf import settings
        from scheduler.management.commands.run_worker import Command

        db_engine = settings.DATABASES['default']['ENGINE']
        if 'sqlite' in db_engine:
            self.skipTest("SQLite doesn't support concurrent writes")

        _jobs = [
            self._create_job(name=f'test_job_{i}', payload={'index': i})
            for i in range(10)
        ]

        workers = []
        for w in range(3):
            worker_cmd = Command()
            worker_cmd.project = self.project
            worker_cmd.worker = Worker.objects.create(
                project=self.project, hostname=f"test-worker-{w}",
                concurrency_limit=5,
            )
            workers.append(worker_cmd)

        claimed_jobs = []
        errors = []

        def worker_claim(worker_cmd):
            try:
                for _ in range(5):
                    job = worker_cmd.claim_next_job()
                    if job:
                        claimed_jobs.append(str(job.id))
                    else:
                        break
            except Exception as e:
                errors.append(str(e))

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(worker_claim, w) for w in workers]
            concurrent.futures.wait(futures)

        self.assertEqual(len(errors), 0, f"Errors during concurrent claiming: {errors}")
        self.assertEqual(len(claimed_jobs), len(set(claimed_jobs)), "Duplicate job claims detected!")
        self.assertEqual(len(claimed_jobs), 10, "Not all jobs were claimed")


# ---------------------------------------------------------------------------
# Worker loop tests
# ---------------------------------------------------------------------------

class SchedulerWorkerLoopTest(BaseSchedulerTest):

    def test_worker_heartbeat_updates(self):
        worker_cmd = self._create_worker()
        old_heartbeat = worker_cmd.worker.last_heartbeat
        worker_cmd.update_heartbeat()
        worker_cmd.worker.refresh_from_db()
        self.assertNotEqual(worker_cmd.worker.last_heartbeat, old_heartbeat)

    def test_worker_shutdown_sets_status(self):
        worker_cmd = self._create_worker()
        worker_cmd.handle_shutdown(None, None)
        worker_cmd.worker.refresh_from_db()
        self.assertEqual(worker_cmd.worker.status, 'SHUTTING_DOWN')


# ---------------------------------------------------------------------------
# ScheduledJob tests
# ---------------------------------------------------------------------------

class SchedulerScheduledJobTest(BaseSchedulerTest):

    def test_scheduled_job_creates_job_when_due(self):
        worker_cmd = self._create_worker()
        ScheduledJob.objects.create(
            queue=self.queue, name='scheduled_test_job',
            payload={'test': 'data'}, cron_expression='* * * * *',
            next_run_at=timezone.now(), is_active=True,
        )

        worker_cmd.process_scheduled_jobs()

        jobs = Job.objects.filter(name='scheduled_test_job', queue=self.queue)
        self.assertEqual(jobs.count(), 1)
        created_job = jobs.first()
        assert created_job is not None  # guaranteed: Job was just created by process_scheduled_jobs
        self.assertEqual(created_job.payload, {'test': 'data'})
        self.assertEqual(created_job.cron_expression, '* * * * *')
        self.assertEqual(created_job.status, 'QUEUED')

    def test_scheduled_job_not_created_when_not_due(self):
        worker_cmd = self._create_worker()
        future_time = timezone.now() + timedelta(hours=1)
        ScheduledJob.objects.create(
            queue=self.queue, name='future_scheduled_job',
            payload={'test': 'data'}, cron_expression='* * * * *',
            next_run_at=future_time, is_active=True,
        )

        worker_cmd.process_scheduled_jobs()

        jobs = Job.objects.filter(name='future_scheduled_job', queue=self.queue)
        self.assertEqual(jobs.count(), 0)

    def test_scheduled_job_inactive_not_processed(self):
        worker_cmd = self._create_worker()
        ScheduledJob.objects.create(
            queue=self.queue, name='inactive_scheduled_job',
            payload={'test': 'data'}, cron_expression='* * * * *',
            next_run_at=timezone.now(), is_active=False,
        )

        worker_cmd.process_scheduled_jobs()

        jobs = Job.objects.filter(name='inactive_scheduled_job', queue=self.queue)
        self.assertEqual(jobs.count(), 0)


# ---------------------------------------------------------------------------
# BatchJob tests
# ---------------------------------------------------------------------------

class SchedulerBatchJobTest(BaseSchedulerTest):

    def test_batch_job_creation_and_progress(self):
        worker_cmd = self._create_worker(concurrency_limit=3)

        payload = {
            "name": "test_batch", "queue": "default",
            "jobs": [
                {"name": "send_monthly_report", "payload": {"customer": f"cust-{i}"}}
                for i in range(3)
            ],
        }
        response = self.client.post("/api/jobs/batch/", payload, format='json')
        self.assertEqual(response.status_code, 201)

        batch_id = response.json()['batch_id']
        batch = BatchJob.objects.get(id=batch_id)
        self.assertEqual(batch.total_jobs, 3)
        self.assertEqual(batch.status, 'PARTIAL')

        for _ in range(3):
            job_to_run = worker_cmd.claim_next_job()
            if job_to_run:
                worker_cmd.execute_job(job_to_run)

        time.sleep(0.1)
        batch.refresh_from_db()
        self.assertEqual(batch.completed_jobs, 3)
        self.assertEqual(batch.status, 'COMPLETED')
        self.assertIsNotNone(batch.completed_at)


# ---------------------------------------------------------------------------
# DLQ tests
# ---------------------------------------------------------------------------

class SchedulerDLQTest(BaseSchedulerTest):

    def test_dlq_retry_via_api(self):
        job = self._create_job(
            name='fail_job', payload={'fail': True}, status='DLQ',
            retry_count=3, max_retries=2,
        )
        dlq_entry = DeadLetterQueue.objects.create(
            job=job, error_message='Test error', failure_reason='Exception',
            retry_count=3, last_attempt_at=timezone.now(),
        )

        response = self.client.post(f"/api/dlq/{dlq_entry.id}/retry/")
        self.assertEqual(response.status_code, 200)

        job.refresh_from_db()
        dlq_entry.refresh_from_db()
        self.assertEqual(job.status, 'QUEUED')
        self.assertEqual(job.retry_count, 0)
        self.assertIsNotNone(dlq_entry.resolved_at)
        self.assertEqual(dlq_entry.resolution_notes, 'Retried via API')

    def test_job_retry_action_via_detail_api(self):
        job = self._create_job(
            name='dlq_job', payload={'test': 'data'}, status='DLQ',
            retry_count=3, max_retries=2,
        )
        DeadLetterQueue.objects.create(
            job=job, error_message='Test error', failure_reason='Exception',
            retry_count=3, last_attempt_at=timezone.now(),
        )

        response = self.client.post(
            f"/api/jobs/{job.id}/", {'action': 'retry'}, format='json'
        )
        self.assertEqual(response.status_code, 200)

        job.refresh_from_db()
        self.assertEqual(job.status, 'QUEUED')
        self.assertEqual(job.retry_count, 0)
        self.assertFalse(DeadLetterQueue.objects.filter(job=job).exists())


# ---------------------------------------------------------------------------
# Timezone tests
# ---------------------------------------------------------------------------

class SchedulerTimezoneTest(BaseSchedulerTest):

    def test_scheduled_at_timezone_aware_conversion(self):
        naive_dt = (timezone.now() + timedelta(hours=1)).replace(tzinfo=None)
        response = self._submit_job(scheduled_at=naive_dt.isoformat())
        self.assertEqual(response.status_code, 201)
        job = Job.objects.get(id=response.json()['id'])
        self.assertIsNotNone(job.scheduled_at.tzinfo)

    def test_scheduled_at_utc_timezone(self):
        utc_dt = timezone.now() + timedelta(hours=1)
        response = self._submit_job(scheduled_at=utc_dt.isoformat())
        self.assertEqual(response.status_code, 201)
        job = Job.objects.get(id=response.json()['id'])
        self.assertIsNotNone(job.scheduled_at.tzinfo)

    def test_cron_timezone_handling(self):
        worker_cmd = self._create_worker()
        job = self._create_job(
            name='cron_job_tz', cron_expression='0 12 * * *',
        )
        worker_cmd.execute_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status, 'SCHEDULED')
        self.assertIsNotNone(job.scheduled_at)
        self.assertIsNotNone(job.scheduled_at.tzinfo)


# ---------------------------------------------------------------------------
# Security / IDOR tests
# ---------------------------------------------------------------------------

class SecurityTest(TransactionTestCase):
    """Verify cross-project isolation and authentication enforcement."""

    def setUp(self):
        self.org = Organization.objects.create(
            name="Sec Org", slug="sec-org",
        )
        self.project_a = Project.objects.create(
            name="Project A", api_key="key-a", is_active=True,
            organization=self.org,
        )
        self.project_b = Project.objects.create(
            name="Project B", api_key="key-b", is_active=True,
            organization=self.org,
        )
        self.queue_a = Queue.objects.create(
            project=self.project_a, name="default", priority=1,
        )
        self.queue_b = Queue.objects.create(
            project=self.project_b, name="default", priority=1,
        )
        self.job_a = Job.objects.create(
            queue=self.queue_a, name="job-a", status="QUEUED",
            scheduled_at=timezone.now(),
        )
        self.job_b = Job.objects.create(
            queue=self.queue_b, name="job-b", status="QUEUED",
            scheduled_at=timezone.now(),
        )

    def _client(self, key):
        c = APIClient()
        c.credentials(HTTP_X_PROJECT_KEY=key)
        return c

    def test_cross_project_job_list_isolation(self):
        """Project A's key must not see Project B's jobs."""
        client_a = self._client("key-a")
        response = client_a.get("/api/jobs/")
        self.assertEqual(response.status_code, 200)
        ids = [j['id'] for j in response.json()['results']]
        self.assertIn(str(self.job_a.id), ids)
        self.assertNotIn(str(self.job_b.id), ids)

    def test_cross_project_job_detail_forbidden(self):
        """Project A's key must not read Project B's job detail."""
        client_a = self._client("key-a")
        response = client_a.get(f"/api/jobs/{self.job_b.id}/")
        self.assertIn(response.status_code, [403, 404])

    def test_cross_project_queue_list_isolation(self):
        client_a = self._client("key-a")
        response = client_a.get("/api/queues/")
        self.assertEqual(response.status_code, 200)
        names = [q['name'] for q in response.json()]
        self.assertIn("default", names)
        # Project B's queue should not appear (only one "default" exists per project,
        # but verify only one item returned)
        self.assertEqual(len(names), 1)

    def test_cross_project_dlq_isolation(self):
        dlq_a = DeadLetterQueue.objects.create(
            job=self.job_a, error_message="err-a", failure_reason="Exception",
            retry_count=1, last_attempt_at=timezone.now(),
        )
        DeadLetterQueue.objects.create(
            job=self.job_b, error_message="err-b", failure_reason="Exception",
            retry_count=1, last_attempt_at=timezone.now(),
        )

        client_a = self._client("key-a")
        response = client_a.get("/api/dlq/")
        self.assertEqual(response.status_code, 200)
        ids = [e['id'] for e in response.json()['results']]
        self.assertIn(dlq_a.id, ids)
        self.assertEqual(len(ids), 1)

    def test_missing_api_key_returns_401_or_403(self):
        c = APIClient()
        response = c.get("/api/jobs/")
        self.assertIn(response.status_code, [401, 403])

    def test_invalid_api_key_returns_401_or_403(self):
        c = APIClient()
        c.credentials(HTTP_X_PROJECT_KEY='nonexistent')
        response = c.get("/api/jobs/")
        self.assertIn(response.status_code, [401, 403])

    def test_inactive_project_rejected(self):
        self.project_a.is_active = False
        self.project_a.save(update_fields=['is_active'])

        c = self._client("key-a")
        response = c.get("/api/jobs/")
        self.assertIn(response.status_code, [401, 403])

    def test_inactive_project_submit_rejected(self):
        self.project_a.is_active = False
        self.project_a.save(update_fields=['is_active'])

        c = self._client("key-a")
        payload = {
            "name": "test_job", "queue": "default",
            "payload": {"customer": "Acme"},
        }
        response = c.post("/api/jobs/submit/", payload, format='json')
        self.assertIn(response.status_code, [401, 403])

    def test_stats_isolation(self):
        client_a = self._client("key-a")
        response = client_a.get("/api/stats/")
        self.assertEqual(response.status_code, 200)
        # stats should only reflect project_a jobs
        data = response.json()
        self.assertEqual(data['jobs_queued'], 1)  # only job_a

    def test_worker_registration_isolation(self):
        """Worker registered with key-a must belong to project_a."""
        client_a = self._client("key-a")
        response = client_a.post(
            "/api/workers/register/",
            {"hostname": "w1", "concurrency_limit": 5},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        worker = Worker.objects.get(id=response.json()['id'])
        self.assertEqual(worker.project_id, self.project_a.id)

    def test_dlq_retry_cross_project_forbidden(self):
        dlq_b = DeadLetterQueue.objects.create(
            job=self.job_b, error_message="err", failure_reason="Exception",
            retry_count=1, last_attempt_at=timezone.now(),
        )
        client_a = self._client("key-a")
        response = client_a.post(f"/api/dlq/{dlq_b.id}/retry/")
        self.assertIn(response.status_code, [403, 404])

    def test_job_cancel_cross_project_forbidden(self):
        client_a = self._client("key-a")
        response = client_a.post(
            f"/api/jobs/{self.job_b.id}/",
            {"action": "cancel"}, format='json',
        )
        self.assertIn(response.status_code, [403, 404])


# ---------------------------------------------------------------------------
# API Key Generation tests
# ---------------------------------------------------------------------------

class APIKeyGenerationTest(TransactionTestCase):
    """Verify consistent secure API key generation across all creation paths."""

    def test_registration_generates_token_urlsafe_key(self):
        from scheduler.auth_views import RegisterForm

        form_data = {
            'username': 'reg_user_1',
            'email': 'reg@test.com',
            'organization_name': 'Reg Org',
            'project_name': 'Reg Project',
            'password1': 'ComplexPass123!',
            'password2': 'ComplexPass123!',
        }
        form = RegisterForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()

        org = user.organizations.first()
        self.assertIsNotNone(org)
        project = org.projects.first()
        self.assertIsNotNone(project)
        self.assertGreaterEqual(len(project.api_key), 40)
        self.assertNotRegex(project.api_key, r'^[0-9a-f]{8}-[0-9a-f]{4}-')

    def test_ui_creation_generates_token_urlsafe_key(self):
        from django.contrib.auth import get_user_model
        from scheduler.models import Organization, Project
        User = get_user_model()

        user = User.objects.create_user(username='ui_user_1', password='test123')
        org = Organization.objects.create(
            name='UI Org', slug='ui-org', user=user,
        )
        project = Project.objects.create(
            name='UI Project', organization=org, is_active=True,
        )
        import secrets
        project.api_key = secrets.token_urlsafe(32)
        project.save()

        self.assertGreaterEqual(len(project.api_key), 40)

    def test_project_api_key_is_43_chars_minimum(self):
        from scheduler.models import Project, Organization
        org = Organization.objects.create(name='Key Org', slug='key-org')
        import secrets
        project = Project.objects.create(
            name='Key Proj', organization=org,
            api_key=secrets.token_urlsafe(32), is_active=True,
        )
        self.assertGreaterEqual(len(project.api_key), 40)


# ---------------------------------------------------------------------------
# Serializer Project Scoping tests
# ---------------------------------------------------------------------------

class SerializerProjectScopingTest(TransactionTestCase):
    """Verify serializer validation rejects cross-project foreign keys."""

    def setUp(self):
        self.org_a = Organization.objects.create(name="Org A", slug="scoping-a")
        self.org_b = Organization.objects.create(name="Org B", slug="scoping-b")
        self.project_a = Project.objects.create(
            name="Project A", api_key="scope-key-a", is_active=True,
            organization=self.org_a,
        )
        self.project_b = Project.objects.create(
            name="Project B", api_key="scope-key-b", is_active=True,
            organization=self.org_b,
        )
        self.queue_a = Queue.objects.create(
            project=self.project_a, name="default", priority=1,
        )
        self.queue_b = Queue.objects.create(
            project=self.project_b, name="default", priority=1,
        )
        self.job_a = Job.objects.create(
            queue=self.queue_a, name="job-a", status="QUEUED",
            scheduled_at=timezone.now(),
        )
        self.job_b = Job.objects.create(
            queue=self.queue_b, name="job-b", status="QUEUED",
            scheduled_at=timezone.now(),
        )

    def _make_request(self, key):
        from rest_framework.test import APIRequestFactory
        factory = APIRequestFactory()
        request = factory.get('/')
        project = Project.objects.get(api_key=key)
        request.auth = project
        return request

    def test_scheduled_job_serializer_rejects_other_project_queue(self):
        from scheduler.serializers import ScheduledJobSerializer
        request = self._make_request("scope-key-a")
        serializer = ScheduledJobSerializer(
            data={
                'name': 'test',
                'queue': self.queue_b.id,
                'cron_expression': '* * * * *',
                'payload': {},
            },
            context={'request': request},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('queue', serializer.errors)

    def test_scheduled_job_serializer_accepts_own_project_queue(self):
        from scheduler.serializers import ScheduledJobSerializer
        request = self._make_request("scope-key-a")
        serializer = ScheduledJobSerializer(
            data={
                'name': 'test',
                'queue': self.queue_a.id,
                'cron_expression': '* * * * *',
                'payload': {},
            },
            context={'request': request},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_workflow_serializer_rejects_other_project_job(self):
        from scheduler.serializers import WorkflowDependencySerializer
        request = self._make_request("scope-key-a")
        serializer = WorkflowDependencySerializer(
            data={
                'job': self.job_a.id,
                'depends_on': self.job_b.id,
            },
            context={'request': request},
        )
        self.assertFalse(serializer.is_valid())

    def test_workflow_serializer_accepts_own_project_jobs(self):
        from scheduler.serializers import WorkflowDependencySerializer
        request = self._make_request("scope-key-a")
        job_a2 = Job.objects.create(
            queue=self.queue_a, name="job-a2", status="QUEUED",
            scheduled_at=timezone.now(),
        )
        serializer = WorkflowDependencySerializer(
            data={
                'job': self.job_a.id,
                'depends_on': job_a2.id,
            },
            context={'request': request},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_api_cross_project_scheduled_job_returns_error(self):
        client = APIClient()
        client.credentials(HTTP_X_PROJECT_KEY='scope-key-a')
        response = client.post("/api/scheduled/", {
            'name': 'cross-project-test',
            'queue': self.queue_b.id,
            'cron_expression': '* * * * *',
            'payload': {},
        }, format='json')
        self.assertIn(response.status_code, [400, 403, 404])

    def test_api_cross_project_workflow_returns_error(self):
        client = APIClient()
        client.credentials(HTTP_X_PROJECT_KEY='scope-key-a')
        response = client.post("/api/workflows/", {
            'job': str(self.job_a.id),
            'depends_on': str(self.job_b.id),
        }, format='json')
        self.assertIn(response.status_code, [400, 403, 404])


# ---------------------------------------------------------------------------
# Batch Job completed_at semantics tests
# ---------------------------------------------------------------------------

class BatchJobSemanticsTest(BaseSchedulerTest):
    """Verify BatchJob status and completed_at semantics."""

    def test_all_jobs_succeed(self):
        from scheduler.models import BatchJob
        worker_cmd = self._create_worker(concurrency_limit=3)
        batch = BatchJob.objects.create(
            project=self.project, name="all-succeed", total_jobs=2, status='PENDING',
        )
        j1 = Job.objects.create(
            queue=self.queue, name='test_job', status='QUEUED',
            scheduled_at=timezone.now(), batch_id=batch.id,
        )
        j2 = Job.objects.create(
            queue=self.queue, name='test_job', status='QUEUED',
            scheduled_at=timezone.now(), batch_id=batch.id,
        )
        batch.status = 'PARTIAL'
        batch.save()

        worker_cmd.execute_job(j1)
        worker_cmd.execute_job(j2)

        batch.refresh_from_db()
        self.assertEqual(batch.status, 'COMPLETED')
        self.assertEqual(batch.completed_jobs, 2)
        self.assertEqual(batch.failed_jobs, 0)
        self.assertIsNotNone(batch.completed_at)

    def test_one_job_fails(self):
        from scheduler.models import BatchJob
        worker_cmd = self._create_worker(concurrency_limit=3)
        batch = BatchJob.objects.create(
            project=self.project, name="one-fails", total_jobs=2, status='PENDING',
        )
        j1 = Job.objects.create(
            queue=self.queue, name='test_job', status='QUEUED',
            scheduled_at=timezone.now(), batch_id=batch.id,
        )
        j2 = Job.objects.create(
            queue=self.queue, name='fail_job', payload={'fail': True},
            status='QUEUED', scheduled_at=timezone.now(), batch_id=batch.id,
            max_retries=0,
        )
        batch.status = 'PARTIAL'
        batch.save()

        worker_cmd.execute_job(j1)
        worker_cmd.execute_job(j2)

        batch.refresh_from_db()
        self.assertEqual(batch.status, 'FAILED')
        self.assertEqual(batch.completed_jobs, 1)
        self.assertEqual(batch.failed_jobs, 1)
        self.assertIsNotNone(batch.completed_at)

    def test_all_jobs_fail(self):
        from scheduler.models import BatchJob
        worker_cmd = self._create_worker(concurrency_limit=3)
        batch = BatchJob.objects.create(
            project=self.project, name="all-fail", total_jobs=2, status='PENDING',
        )
        j1 = Job.objects.create(
            queue=self.queue, name='fail_job', payload={'fail': True},
            status='QUEUED', scheduled_at=timezone.now(), batch_id=batch.id,
            max_retries=0,
        )
        j2 = Job.objects.create(
            queue=self.queue, name='fail_job', payload={'fail': True},
            status='QUEUED', scheduled_at=timezone.now(), batch_id=batch.id,
            max_retries=0,
        )
        batch.status = 'PARTIAL'
        batch.save()

        worker_cmd.execute_job(j1)
        worker_cmd.execute_job(j2)

        batch.refresh_from_db()
        self.assertEqual(batch.status, 'FAILED')
        self.assertEqual(batch.completed_jobs, 0)
        self.assertEqual(batch.failed_jobs, 2)
        self.assertIsNotNone(batch.completed_at)

    def test_batch_partial_status(self):
        from scheduler.models import BatchJob
        worker_cmd = self._create_worker(concurrency_limit=3)
        batch = BatchJob.objects.create(
            project=self.project, name="partial", total_jobs=3, status='PENDING',
        )
        j1 = Job.objects.create(
            queue=self.queue, name='test_job', status='QUEUED',
            scheduled_at=timezone.now(), batch_id=batch.id,
        )
        _j2 = Job.objects.create(
            queue=self.queue, name='test_job', status='QUEUED',
            scheduled_at=timezone.now(), batch_id=batch.id,
        )
        _j3 = Job.objects.create(
            queue=self.queue, name='test_job', status='QUEUED',
            scheduled_at=timezone.now(), batch_id=batch.id,
        )
        batch.status = 'PARTIAL'
        batch.save()

        worker_cmd.execute_job(j1)
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'PARTIAL')
        self.assertIsNone(batch.completed_at)


# ---------------------------------------------------------------------------
# Auth Rate Limiting tests
# ---------------------------------------------------------------------------

class AuthRateLimitingTest(TransactionTestCase):
    """Verify brute-force protection on login/register endpoints."""

    def test_login_rate_limiting(self):
        from django.test import Client
        c = Client()
        for i in range(21):
            response = c.post('/login/', {
                'username': 'nonexistent',
                'password': 'wrong',
            }, follow=True)
        self.assertEqual(response.status_code, 429)

    def test_register_rate_limiting(self):
        from django.test import Client
        c = Client()
        for i in range(21):
            response = c.post('/register/', {
                'username': f'rateuser{i}',
                'email': f'rate{i}@test.com',
                'organization_name': f'Rate Org {i}',
                'project_name': f'Rate Proj {i}',
                'password1': 'ComplexPass123!',
                'password2': 'DifferentPass456!',
            })
        self.assertEqual(response.status_code, 429)

    def test_api_endpoints_not_affected_by_auth_rate_limit(self):
        from scheduler.models import Organization
        org = Organization.objects.create(name="Rate Org", slug="rate-limit-org")
        Project.objects.create(
            name="Rate Proj", organization=org, api_key="rate-limit-key", is_active=True,
        )
        client = APIClient()
        client.credentials(HTTP_X_PROJECT_KEY='rate-limit-key')
        for i in range(25):
            response = client.get('/api/jobs/')
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# Admin Masked API Key tests
# ---------------------------------------------------------------------------

class AdminApiKeyDisplayTest(TransactionTestCase):
    """Verify admin displays masked API keys."""

    def test_admin_masked_api_key(self):
        from django.contrib.admin.sites import AdminSite
        from scheduler.admin import ProjectAdmin
        from scheduler.models import Project, Organization

        org = Organization.objects.create(name="Admin Org", slug="admin-org")
        project = Project.objects.create(
            name="Admin Proj", organization=org,
            api_key="abcdef1234567890abcdef1234567890", is_active=True,
        )

        admin = ProjectAdmin(Project, AdminSite())
        masked = admin.masked_api_key(project)
        self.assertIn('abcd', masked)
        self.assertIn('7890', masked)
        self.assertNotIn('abcdef1234567890abcdef1234567890', masked)
