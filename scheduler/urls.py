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
    WorkerListPageView,
    QueuePauseView as PageQueuePauseView2, QueueResumeView as PageQueueResumeView2
)
from . import views
from django.contrib.auth import views as auth_views
from .auth_views import register
from django.contrib.auth import views as auth_views
from .auth_views import register
from django.urls import path

urlpatterns = [
    # Auth
    path('login/', auth_views.LoginView.as_view(template_name='scheduler/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('register/', register, name='register'),

    # Dashboard
    path('', views.dashboard, name='dashboard'),
    path('jobs/explorer/', views.job_explorer, name='job_explorer'),

    # Stats
    path('stats/', views.StatsView.as_view(), name='api_stats'),

    # Project management
    path('projects/', ProjectListView.as_view(), name='project_list'),
    path('projects/create/', ProjectCreateView.as_view(), name='project_create'),
    path('projects/<uuid:pk>/', ProjectDetailView.as_view(), name='project_detail'),
    path('projects/<uuid:pk>/edit/', ProjectUpdateView.as_view(), name='project_update'),

    # Queue management (API)
    path('queues/', APIQueueListView.as_view(), name='queue_list'),
    path('queues/create/', views.manage_queues, name='manage_queues'),
    path('queues/<uuid:queue_id>/', APIQueueDetailView.as_view(), name='queue_detail'),
    path('queues/<uuid:queue_id>/pause/', QueuePauseView.as_view(), name='queue_pause'),
    path('queues/<uuid:queue_id>/resume/', QueueResumeView.as_view(), name='queue_resume'),
    path('queues/<uuid:queue_id>/stats/', QueueStatsView.as_view(), name='queue_stats'),

    # Queue management (web UI)
    path('projects/<uuid:project_pk>/queues/', PageQueueListView.as_view(), name='queue_list'),
    path('projects/<uuid:project_pk>/queues/create/', QueueCreateView.as_view(), name='queue_create'),
    path('queues/<uuid:pk>/', PageQueueDetailView.as_view(), name='queue_detail'),
    path('queues/<uuid:pk>/edit/', QueueUpdateView.as_view(), name='queue_update'),
    path('queues/<uuid:pk>/pause/', PageQueuePauseView.as_view(), name='queue_pause'),
    path('queues/<uuid:pk>/resume/', PageQueueResumeView.as_view(), name='queue_resume'),

    # Job management (API)
    path('jobs/submit/', SubmitJobView.as_view(), name='submit_job'),
    path('jobs/', JobListView.as_view(), name='list_jobs'),
    path('jobs/<uuid:job_id>/', JobDetailView.as_view(), name='job_detail'),
    path('jobs/<uuid:job_id>/logs/', JobLogsView.as_view(), name='job_logs'),
    path('jobs/<uuid:job_id>/retry/', views.job_retry, name='job_retry'),

    # Job management (web UI)
    path('jobs/<uuid:pk>/', JobDetailPageView.as_view(), name='job_detail_page'),
    path('jobs/<uuid:pk>/retry/', JobRetryView.as_view(), name='job_retry_page'),
    path('jobs/<uuid:pk>/cancel/', JobCancelView.as_view(), name='job_cancel_page'),

    # Batch jobs
    path('jobs/batch/', APIBatchJobSubmitView.as_view(), name='batch_submit'),
    path('jobs/batch/<uuid:batch_id>/', APIBatchJobDetailView.as_view(), name='batch_detail'),
    path('jobs/batch/', PageBatchJobSubmitView.as_view(), name='batch_job_page'),

    # Dead Letter Queue
    path('dlq/', DeadLetterQueueView.as_view(), name='dlq_list'),
    path('dlq/<int:dlq_id>/retry/', DeadLetterQueueRetryView.as_view(), name='dlq_retry'),
    path('dlq/', DLQPageView.as_view(), name='dlq_page'),
    path('dlq/<int:pk>/retry/', DLQRetryView.as_view(), name='dlq_retry_page'),

    # Scheduled jobs (API)
    path('scheduled/', ScheduledJobListView.as_view(), name='scheduled_jobs_list'),
    path('scheduled/<uuid:scheduled_id>/', ScheduledJobDetailView.as_view(), name='scheduled_job_detail'),

    # Scheduled jobs (web UI)
    path('scheduled/', ScheduledJobListPageView.as_view(), name='scheduled_list_page'),
    path('scheduled/create/', ScheduledJobCreateView.as_view(), name='scheduled_create'),
    path('scheduled/<uuid:pk>/', ScheduledJobDetailPageView.as_view(), name='scheduled_detail_page'),
    path('scheduled/<uuid:pk>/edit/', ScheduledJobUpdateView.as_view(), name='scheduled_update'),
    path('scheduled/<uuid:pk>/toggle/', ScheduledJobToggleView.as_view(), name='scheduled_toggle'),

    # Workflow dependencies
    path('workflows/', WorkflowDependencyListView.as_view(), name='workflow_dependencies'),
    path('workflows/<uuid:dep_id>/', WorkflowDependencyDetailView.as_view(), name='workflow_dependency_detail'),

    # Worker management (API)
    path('workers/register/', WorkerRegisterView.as_view(), name='workers_register'),
    path('workers/heartbeat/', WorkerHeartbeatView.as_view(), name='worker_heartbeat'),
    path('workers/', WorkerListView.as_view(), name='workers_list'),

    # Worker management (web UI)
    path('workers/', WorkerListPageView.as_view(), name='worker_list_page'),
]