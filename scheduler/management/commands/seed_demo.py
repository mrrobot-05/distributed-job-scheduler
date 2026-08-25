import json
import uuid
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db import transaction

from scheduler.models import Organization, Project, Queue, Job, ScheduledJob, Worker, BatchJob, DeadLetterQueue

User = get_user_model()


class Command(BaseCommand):
    help = 'Create demo data for the Distributed Job Scheduler'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            default='demo@demo.com',
            help='Email for demo user',
        )
        parser.add_argument(
            '--password',
            type=str,
            default='demo123',
            help='Password for demo user',
        )

    def handle(self, *args, **options):
        email = options['email']
        password = options['password']

        with transaction.atomic():
            self.stdout.write('Creating demo data...')

            # Create or get demo user
            user, created = User.objects.get_or_create(
                username='demo',
                defaults={'email': email, 'first_name': 'Demo', 'last_name': 'User'}
            )
            if created:
                user.set_password(password)
                user.save()
                self.stdout.write(f'Created user: {email}')
            else:
                user.set_password(password)
                user.save()
                self.stdout.write(f'User already exists: {email}')

            # Create organization
            org, created = Organization.objects.get_or_create(
                slug='demo-org',
                defaults={
                    'name': 'Demo Organization',
                }
            )
            if created:
                self.stdout.write('Created organization: Demo Organization')

            # Create Project 1: API Project
            api_project, created = Project.objects.get_or_create(
                organization=org,
                name='API Project',
                defaults={
                    'api_key': 'demo-api-key-1',
                    'is_active': True,
                }
            )
            if created:
                self.stdout.write(f'Created project: API Project (API Key: demo-api-key-1)')

            # Create Project 2: Batch Project
            batch_project, created = Project.objects.get_or_create(
                organization=org,
                name='Batch Project',
                defaults={
                    'api_key': 'demo-api-key-2',
                    'is_active': True,
                }
            )
            if created:
                self.stdout.write(f'Created project: Batch Project (API Key: demo-api-key-2)')

            # Create queues for API Project
            queues_data = [
                {'name': 'high-priority', 'priority': 10, 'concurrency_limit': 10, 'project': api_project},
                {'name': 'default', 'priority': 5, 'concurrency_limit': 5, 'project': api_project},
                {'name': 'low-priority', 'priority': 1, 'concurrency_limit': 2, 'project': api_project},
            ]

            for q_data in queues_data:
                queue, created = Queue.objects.get_or_create(
                    project=q_data['project'],
                    name=q_data['name'],
                    defaults={
                        'priority': q_data['priority'],
                        'concurrency_limit': q_data['concurrency_limit'],
                        'retry_policy': {'max_retries': 3, 'backoff_strategy': 'EXPONENTIAL', 'backoff_delay': 60}
                    }
                )
                if created:
                    self.stdout.write(f'  Created queue: {q_data["name"]} (priority={q_data["priority"]}, concurrency={q_data["concurrency_limit"]})')

            # Create queue for Batch Project
            batch_queue, created = Queue.objects.get_or_create(
                project=batch_project,
                name='batch-queue',
                defaults={
                    'priority': 5,
                    'concurrency_limit': 5,
                    'retry_policy': {'max_retries': 3, 'backoff_strategy': 'EXPONENTIAL', 'backoff_delay': 60}
                }
            )
            if created:
                self.stdout.write(f'  Created queue: batch-queue (for Batch Project)')

            # Get default queue for API Project
            default_queue = Queue.objects.get(project=api_project, name='default')
            high_queue = Queue.objects.get(project=api_project, name='high-priority')
            low_queue = Queue.objects.get(project=api_project, name='low-priority')

            # Create sample jobs
            jobs_data = [
                {'name': 'send_monthly_report', 'queue': default_queue, 'payload': {'customer': 'Acme Corp', 'report': 'monthly', 'month': 'January'}, 'status': 'COMPLETED'},
                {'name': 'send_monthly_report', 'queue': default_queue, 'payload': {'customer': 'Globex Inc', 'report': 'monthly', 'month': 'January'}, 'status': 'COMPLETED'},
                {'name': 'generate_report', 'queue': default_queue, 'payload': {'report_type': 'quarterly', 'customer': 'Acme Corp'}, 'status': 'QUEUED'},
                {'name': 'generate_report', 'queue': default_queue, 'payload': {'report_type': 'annual', 'customer': 'Globex Inc'}, 'status': 'SCHEDULED', 'scheduled_at': timezone.now() + timezone.timedelta(hours=2)},
                {'name': 'cleanup_database', 'queue': low_queue, 'payload': {'environment': 'production', 'tables': ['logs', 'sessions']}, 'status': 'QUEUED'},
                {'name': 'cleanup_database', 'queue': low_queue, 'payload': {'environment': 'staging', 'tables': ['temp_data']}, 'status': 'QUEUED'},
                {'name': 'send_email', 'queue': high_queue, 'payload': {'to': 'user1@example.com', 'subject': 'Welcome!', 'template': 'welcome'}, 'status': 'COMPLETED'},
                {'name': 'send_email', 'queue': high_queue, 'payload': {'to': 'user2@example.com', 'subject': 'Welcome!', 'template': 'welcome'}, 'status': 'COMPLETED'},
                {'name': 'send_email', 'queue': high_queue, 'payload': {'to': 'user3@example.com', 'subject': 'Welcome!', 'template': 'welcome'}, 'status': 'QUEUED'},
                {'name': 'process_payment', 'queue': high_queue, 'payload': {'amount': 99.99, 'currency': 'USD', 'customer_id': 'cust_123'}, 'status': 'RUNNING'},
                {'name': 'process_payment', 'queue': high_queue, 'payload': {'amount': 49.99, 'currency': 'USD', 'customer_id': 'cust_456'}, 'status': 'QUEUED'},
                {'name': 'backup_database', 'queue': low_queue, 'payload': {'database': 'main', 'destination': 's3://backups'}, 'status': 'QUEUED'},
                {'name': 'sync_users', 'queue': default_queue, 'payload': {'source': 'ldap', 'target': 'internal'}, 'status': 'FAILED'},
                {'name': 'generate_invoice', 'queue': default_queue, 'payload': {'customer_id': 'cust_789', 'amount': 299.00}, 'status': 'COMPLETED'},
                {'name': 'generate_invoice', 'queue': default_queue, 'payload': {'customer_id': 'cust_101', 'amount': 149.50}, 'status': 'COMPLETED'},
                {'name': 'send_notification', 'queue': high_queue, 'payload': {'user_id': 'user_001', 'message': 'Your report is ready'}, 'status': 'COMPLETED'},
                {'name': 'send_notification', 'queue': high_queue, 'payload': {'user_id': 'user_002', 'message': 'Your report is ready'}, 'status': 'QUEUED'},
                {'name': 'archive_logs', 'queue': low_queue, 'payload': {'retention_days': 90, 'storage': 's3'}, 'status': 'SCHEDULED', 'scheduled_at': timezone.now() + timezone.timedelta(days=1)},
                {'name': 'cleanup_temp_files', 'queue': low_queue, 'payload': {'path': '/tmp', 'older_than_days': 7}, 'status': 'QUEUED'},
                {'name': 'send_webhook', 'queue': high_queue, 'payload': {'url': 'https://api.example.com/webhook', 'event': 'payment.completed', 'data': {'id': 'pay_123'}}, 'status': 'DLQ'},
                {'name': 'fail_job', 'queue': default_queue, 'payload': {'test': 'failure'}, 'status': 'DLQ'},
            ]

            for job_data in jobs_data:
                job_params = {
                    'queue': job_data['queue'],
                    'name': job_data['name'],
                    'payload': job_data['payload'],
                    'status': job_data.get('status', 'QUEUED'),
                    'scheduled_at': job_data.get('scheduled_at', timezone.now()),
                    'max_retries': 3,
                    'backoff_strategy': 'EXPONENTIAL',
                    'backoff_delay': 60,
                }
                if 'scheduled_at' in job_data:
                    job_params['scheduled_at'] = job_data['scheduled_at']
                
                job, created = Job.objects.get_or_create(
                    queue=job_data['queue'],
                    name=job_data['name'],
                    payload=job_data['payload'],
                    defaults=job_params
                )
                if created:
                    # Update status if not default
                    if job_data.get('status') != 'QUEUED':
                        job.status = job_data['status']
                        job.save()

            self.stdout.write(f'Created {len(jobs_data)} sample jobs')

            # Create ScheduledJob (daily report at 9 AM UTC)
            scheduled, created = ScheduledJob.objects.get_or_create(
                queue=default_queue,
                name='daily-report',
                defaults={
                    'payload': {'report_type': 'daily_summary', 'format': 'pdf'},
                    'cron_expression': '0 9 * * *',
                    'next_run_at': timezone.now().replace(hour=9, minute=0, second=0, microsecond=0) + timezone.timedelta(days=1),
                    'max_retries': 3,
                    'backoff_strategy': 'EXPONENTIAL',
                    'backoff_delay': 60,
                    'is_active': True,
                }
            )
            if created:
                self.stdout.write('Created scheduled job: daily-report (cron: 0 9 * * *)')

            # Create Worker
            worker, created = Worker.objects.get_or_create(
                project=api_project,
                hostname='demo-worker-1',
                defaults={
                    'concurrency_limit': 5,
                    'status': 'ACTIVE',
                    'last_heartbeat': timezone.now(),
                }
            )
            if created:
                self.stdout.write('Created worker: demo-worker-1')

            # Create Batch Job
            batch, created = BatchJob.objects.get_or_create(
                project=batch_project,
                name='monthly-emails',
                defaults={
                    'total_jobs': 3,
                    'status': 'PARTIAL',
                }
            )
            if created:
                # Create batch jobs
                for i, customer in enumerate(['cust-1', 'cust-2', 'cust-3']):
                    Job.objects.create(
                        queue=Queue.objects.get(project=batch_project, name='batch-queue'),
                        name='send_email',
                        payload={'to': f'{customer}@example.com', 'subject': 'Monthly Newsletter', 'template': 'newsletter'},
                        status='QUEUED' if i < 2 else 'COMPLETED',
                        scheduled_at=timezone.now(),
                        batch_id=batch.id,
                        max_retries=3,
                        backoff_strategy='EXPONENTIAL',
                        backoff_delay=60,
                    )
                batch.status = 'PARTIAL'
                batch.completed_jobs = 1
                batch.failed_jobs = 0
                batch.save()
                self.stdout.write('Created batch job: monthly-emails (3 jobs)')

            # Create DLQ entries
            dlq_jobs = [
                {'name': 'send_webhook', 'queue': high_queue, 'payload': {'url': 'https://api.example.com/webhook', 'event': 'payment.completed', 'data': {'id': 'pay_123'}}, 'error': 'Connection timeout after 30s', 'reason': 'TimeoutError', 'retries': 3},
                {'name': 'fail_job', 'queue': default_queue, 'payload': {'test': 'failure'}, 'error': 'Simulated Failure', 'reason': 'Exception', 'retries': 3},
            ]

            for dlq_data in dlq_jobs:
                job, created = Job.objects.get_or_create(
                    queue=dlq_data['queue'],
                    name=dlq_data['name'],
                    payload=dlq_data['payload'],
                    defaults={
                        'status': 'DLQ',
                        'retry_count': dlq_data['retries'],
                        'max_retries': 3,
                        'backoff_strategy': 'EXPONENTIAL',
                        'backoff_delay': 60,
                        'scheduled_at': timezone.now(),
                    }
                )
                if created or job.status != 'DLQ':
                    job.status = 'DLQ'
                    job.retry_count = dlq_data['retries']
                    job.save()

                DeadLetterQueue.objects.get_or_create(
                    job=job,
                    defaults={
                        'error_message': dlq_data['error'],
                        'failure_reason': dlq_data['reason'],
                        'retry_count': dlq_data['retries'],
                        'last_attempt_at': timezone.now() - timezone.timedelta(minutes=5),
                    }
                )

            self.stdout.write('Created DLQ entries')

            self.stdout.write(self.style.SUCCESS('Demo data created successfully!'))
            self.stdout.write('')
            self.stdout.write('Demo credentials:')
            self.stdout.write(f'  Email: {email}')
            self.stdout.write(f'  Password: {password}')
            self.stdout.write('')
            self.stdout.write('API Keys:')
            self.stdout.write(f'  API Project: {api_project.api_key}')
            self.stdout.write(f'  Batch Project: {batch_project.api_key}')
            self.stdout.write('')
            self.stdout.write('Access the dashboard at: http://localhost:8000/')
            self.stdout.write('Start worker with: python manage.py run_worker --project_key=demo-api-key-1 --concurrency=5')