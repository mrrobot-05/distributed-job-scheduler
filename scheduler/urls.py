from django.urls import path
from django.contrib.auth import views as auth_views
from . import views
from .auth_views import register
from .views import (
    WorkerRegisterView, StatsView, SubmitJobView, JobListView, JobDetailView,
    WorkerHeartbeatView, WorkerListView,
    QueueListView as APIQueueListView, QueueDetailView as APIQueueDetailView,
    QueuePauseView, QueueResumeView, QueueStatsView, JobLogsView,
    BatchJobSubmitView as APIBatchJobSubmitView, BatchJobDetailView as APIBatchJobDetailView,
    DeadLetterQueueView, DeadLetterQueueRetryView,
    ScheduledJobListView, ScheduledJobDetailView,
    WorkflowDependencyListView, WorkflowDependencyDetailView
)
from .page_views import (
    ProjectListView, ProjectCreateView, ProjectDetailView, ProjectUpdateView,
    QueueListView as PageQueueListView, QueueCreateView, QueueDetailView as PageQueueDetailView,
    QueueUpdateView, QueuePauseView as PageQueuePauseView, QueueResumeView as PageQueueResumeView,
    JobDetailPageView, JobRetryView, JobCancelView,
    WorkerListPageView, ScheduledJobListPageView, ScheduledJobCreateView,
    ScheduledJobDetailPageView, ScheduledJobUpdateView, ScheduledJobToggleView,
    BatchJobPageView, BatchJobSubmitView as PageBatchJobSubmitView, DLQPageView, DLQRetryView,
)

urlpatterns = [
    # Auth
    path('login/', auth_views.LoginView.as_view(template_name='scheduler/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('register/', register, name='register'),

    # Dashboard
    path('', views.dashboard, name='dashboard'),
    path('jobs/explorer/', views.job_explorer, name='job_explorer'),

    # Project management (web UI)
    path('projects/', ProjectListView.as_view(), name='project_list'),
    path('projects/create/', ProjectCreateView.as_view(), name='project_create'),
    path('projects/<uuid:pk>/', ProjectDetailView.as_view(), name='project_detail'),
    path('projects/<uuid:pk>/edit/', ProjectUpdateView.as_view(), name='project_update'),

    # Queue management (web UI)
    path('projects/<uuid:project_pk>/queues/', PageQueueListView.as_view(), name='queue_list_page'),
    path('projects/<uuid:project_pk>/queues/create/', QueueCreateView.as_view(), name='queue_create'),
    path('queues/<int:pk>/', PageQueueDetailView.as_view(), name='queue_detail_page'),
    path('queues/<int:pk>/edit/', QueueUpdateView.as_view(), name='queue_update'),
    path('queues/<int:pk>/pause/', PageQueuePauseView.as_view(), name='queue_pause_page'),
    path('queues/<int:pk>/resume/', PageQueueResumeView.as_view(), name='queue_resume_page'),

    # Job management (web UI)
    path('jobs/<uuid:pk>/', JobDetailPageView.as_view(), name='job_detail_page'),
    path('jobs/<uuid:pk>/retry/', JobRetryView.as_view(), name='job_retry_page'),
    path('jobs/<uuid:pk>/cancel/', JobCancelView.as_view(), name='job_cancel_page'),

    # Batch jobs (web UI)
    path('jobs/batch/page/', BatchJobPageView.as_view(), name='batch_job_page'),

    # Dead Letter Queue (web UI)
    path('dlq/page/', DLQPageView.as_view(), name='dlq_page'),
    path('dlq/<int:pk>/retry/', DLQRetryView.as_view(), name='dlq_retry_page'),

    # Scheduled jobs (web UI)
    path('scheduled/list/', ScheduledJobListPageView.as_view(), name='scheduled_list_page'),
    path('scheduled/create/', ScheduledJobCreateView.as_view(), name='scheduled_create'),
    path('scheduled/<int:pk>/', ScheduledJobDetailPageView.as_view(), name='scheduled_detail_page'),
    path('scheduled/<int:pk>/edit/', ScheduledJobUpdateView.as_view(), name='scheduled_update'),
    path('scheduled/<int:pk>/toggle/', ScheduledJobToggleView.as_view(), name='scheduled_toggle'),

    # Worker management (web UI)
    path('workers/list/', WorkerListPageView.as_view(), name='worker_list_page'),

    # --- API routes (prefixed with api/) ---
    # Stats
    path('api/stats/', StatsView.as_view(), name='api_stats'),

    # Queue management (API)
    path('api/queues/', APIQueueListView.as_view(), name='api_queue_list'),
    path('api/queues/<int:queue_id>/', APIQueueDetailView.as_view(), name='api_queue_detail'),
    path('api/queues/<int:queue_id>/pause/', QueuePauseView.as_view(), name='api_queue_pause'),
    path('api/queues/<int:queue_id>/resume/', QueueResumeView.as_view(), name='api_queue_resume'),
    path('api/queues/<int:queue_id>/stats/', QueueStatsView.as_view(), name='api_queue_stats'),

    # Job management (API)
    path('api/jobs/submit/', SubmitJobView.as_view(), name='api_submit_job'),
    path('api/jobs/', JobListView.as_view(), name='api_list_jobs'),
    path('api/jobs/<uuid:job_id>/', JobDetailView.as_view(), name='api_job_detail'),
    path('api/jobs/<uuid:job_id>/logs/', JobLogsView.as_view(), name='api_job_logs'),
    path('api/jobs/<uuid:job_id>/retry/', views.job_retry, name='api_job_retry'),

    # Batch jobs (API)
    path('api/jobs/batch/', APIBatchJobSubmitView.as_view(), name='api_batch_submit'),
    path('api/jobs/batch/<uuid:batch_id>/', APIBatchJobDetailView.as_view(), name='api_batch_detail'),

    # Dead Letter Queue (API)
    path('api/dlq/', DeadLetterQueueView.as_view(), name='api_dlq_list'),
    path('api/dlq/<int:dlq_id>/retry/', DeadLetterQueueRetryView.as_view(), name='api_dlq_retry'),

    # Scheduled jobs (API)
    path('api/scheduled/', ScheduledJobListView.as_view(), name='api_scheduled_list'),
    path('api/scheduled/<int:scheduled_id>/', ScheduledJobDetailView.as_view(), name='api_scheduled_detail'),

    # Workflow dependencies (API)
    path('api/workflows/', WorkflowDependencyListView.as_view(), name='api_workflow_list'),
    path('api/workflows/<int:dep_id>/', WorkflowDependencyDetailView.as_view(), name='api_workflow_detail'),

    # Worker management (API)
    path('api/workers/register/', WorkerRegisterView.as_view(), name='api_workers_register'),
    path('api/workers/heartbeat/', WorkerHeartbeatView.as_view(), name='api_worker_heartbeat'),
    path('api/workers/', WorkerListView.as_view(), name='api_workers_list'),
]
