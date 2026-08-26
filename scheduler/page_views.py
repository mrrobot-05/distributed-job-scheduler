import secrets
from django.views.generic import ListView, CreateView, DetailView, UpdateView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy, reverse
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.views import View
from django.utils import timezone
from django.db.models import Count, Q

from scheduler.models import Project, Queue, Job, Worker, ScheduledJob, BatchJob, DeadLetterQueue, JobExecution, JobLog


class ProjectListView(LoginRequiredMixin, ListView):
    model = Project
    template_name = 'scheduler/projects/list.html'
    context_object_name = 'projects'
    paginate_by = 20

    def get_queryset(self):
        return Project.objects.filter(organization__user=self.request.user).select_related('organization').annotate(
            queue_count=Count('queues'),
            job_count=Count('queues__jobs')
        ).order_by('-created_at')


class ProjectCreateView(LoginRequiredMixin, CreateView):
    model = Project
    template_name = 'scheduler/projects/form.html'
    fields = ['name', 'is_active']
    success_url = reverse_lazy('project_list')

    def form_valid(self, form):
        from scheduler.models import Organization
        org, created = Organization.objects.get_or_create(
            user=self.request.user,
            defaults={'name': f"{self.request.user.username}'s Organization", 'slug': f"{self.request.user.username}-org"}
        )
        form.instance.organization = org
        form.instance.api_key = secrets.token_urlsafe(32)
        messages.success(self.request, 'Project created successfully!')
        return super().form_valid(form)


class ProjectDetailView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = 'scheduler/projects/detail.html'
    context_object_name = 'project'

    def get_queryset(self):
        return Project.objects.filter(organization__user=self.request.user).prefetch_related('queues')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.object
        context['queues'] = project.queues.all().annotate(
            job_count=Count('jobs'),
            queued_count=Count('jobs', filter=Q(jobs__status='QUEUED')),
            running_count=Count('jobs', filter=Q(jobs__status='RUNNING'))
        )
        return context


class ProjectUpdateView(LoginRequiredMixin, UpdateView):
    model = Project
    template_name = 'scheduler/projects/form.html'
    fields = ['name', 'is_active']
    success_url = reverse_lazy('project_list')

    def get_queryset(self):
        return Project.objects.filter(organization__user=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, 'Project updated successfully!')
        return super().form_valid(form)


class QueueListView(LoginRequiredMixin, ListView):
    model = Queue
    template_name = 'scheduler/queues/list.html'
    context_object_name = 'queues'
    paginate_by = 20

    def get_queryset(self):
        project_pk = self.kwargs['project_pk']
        project = get_object_or_404(Project, pk=project_pk, organization__user=self.request.user)
        self.project = project
        return Queue.objects.filter(project=project).annotate(
            job_count=Count('jobs'),
            queued_count=Count('jobs', filter=Q(jobs__status='QUEUED')),
            running_count=Count('jobs', filter=Q(jobs__status='RUNNING'))
        ).order_by('-priority', 'name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        return context


class QueueCreateView(LoginRequiredMixin, CreateView):
    model = Queue
    template_name = 'scheduler/queues/form.html'
    fields = ['name', 'priority', 'concurrency_limit', 'is_paused', 'retry_policy']

    def get_success_url(self):
        return reverse('queue_list_page', kwargs={'project_pk': self.kwargs['project_pk']})

    def form_valid(self, form):
        project = get_object_or_404(Project, pk=self.kwargs['project_pk'], organization__user=self.request.user)
        form.instance.project = project
        messages.success(self.request, 'Queue created successfully!')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = get_object_or_404(Project, pk=self.kwargs['project_pk'], organization__user=self.request.user)
        context['is_create'] = True
        return context


class QueueDetailView(LoginRequiredMixin, DetailView):
    model = Queue
    template_name = 'scheduler/queues/detail.html'
    context_object_name = 'queue'

    def get_queryset(self):
        return Queue.objects.filter(project__organization__user=self.request.user).select_related('project')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queue = self.object
        queue = Queue.objects.filter(pk=queue.pk).annotate(
            job_count=Count('jobs'),
            queued_count=Count('jobs', filter=Q(jobs__status='QUEUED')),
            running_count=Count('jobs', filter=Q(jobs__status='RUNNING')),
        ).first()
        context['queue'] = queue
        context['jobs'] = queue.jobs.select_related('queue').order_by('-created_at')[:50]
        context['job_stats'] = queue.jobs.values('status').annotate(count=Count('id'))
        return context


class QueueUpdateView(LoginRequiredMixin, UpdateView):
    model = Queue
    template_name = 'scheduler/queues/form.html'
    fields = ['name', 'priority', 'concurrency_limit', 'is_paused', 'retry_policy']

    def get_queryset(self):
        return Queue.objects.filter(project__organization__user=self.request.user)

    def get_success_url(self):
        return reverse('queue_detail_page', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        messages.success(self.request, 'Queue updated successfully!')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_create'] = False
        context['project'] = self.object.project
        return context


class QueuePauseView(LoginRequiredMixin, View):
    def post(self, request, pk):
        queue = get_object_or_404(Queue, pk=pk, project__organization__user=request.user)
        queue.is_paused = True
        queue.save()
        messages.success(request, f'Queue "{queue.name}" paused.')
        return redirect('queue_detail_page', pk=pk)


class QueueResumeView(LoginRequiredMixin, View):
    def post(self, request, pk):
        queue = get_object_or_404(Queue, pk=pk, project__organization__user=request.user)
        queue.is_paused = False
        queue.save()
        messages.success(request, f'Queue "{queue.name}" resumed.')
        return redirect('queue_detail_page', pk=pk)


class JobDetailPageView(LoginRequiredMixin, DetailView):
    model = Job
    template_name = 'scheduler/jobs/detail.html'
    context_object_name = 'job'

    def get_queryset(self):
        return Job.objects.filter(queue__project__organization__user=self.request.user).select_related('queue', 'queue__project').prefetch_related('executions__logs')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        job = self.object
        context['executions'] = job.executions.select_related('worker').order_by('-started_at')
        context['logs'] = JobLog.objects.filter(execution__job=job).select_related('execution').order_by('timestamp')
        return context


class JobRetryView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk, queue__project__organization__user=request.user)
        if job.status not in ['FAILED', 'DLQ']:
            messages.error(request, 'Only failed or DLQ jobs can be retried.')
            return redirect('job_detail_page', pk=pk)
        
        job.status = 'QUEUED'
        job.retry_count = 0
        job.scheduled_at = timezone.now()
        job.save()
        DeadLetterQueue.objects.filter(job=job).delete()
        messages.success(request, 'Job queued for retry.')
        return redirect('job_detail_page', pk=pk)


class JobCancelView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk, queue__project__organization__user=request.user)
        if job.status not in ['QUEUED', 'SCHEDULED', 'CLAIMED']:
            messages.error(request, 'Only queued, scheduled, or claimed jobs can be cancelled.')
            return redirect('job_detail_page', pk=pk)
        
        job.status = 'FAILED'
        job.save()
        messages.success(request, 'Job cancelled.')
        return redirect('job_detail_page', pk=pk)


class WorkerListPageView(LoginRequiredMixin, ListView):
    model = Worker
    template_name = 'scheduler/workers/list.html'
    context_object_name = 'workers'
    paginate_by = 20

    def get_queryset(self):
        return Worker.objects.filter(project__organization__user=self.request.user).select_related('project').annotate(
            execution_count=Count('executions')
        ).order_by('-last_heartbeat')


class ScheduledJobListPageView(LoginRequiredMixin, ListView):
    model = ScheduledJob
    template_name = 'scheduler/scheduled/list.html'
    context_object_name = 'scheduled_jobs'
    paginate_by = 20

    def get_queryset(self):
        return ScheduledJob.objects.filter(queue__project__organization__user=self.request.user).select_related('queue', 'queue__project').order_by('next_run_at')


class ScheduledJobCreateView(LoginRequiredMixin, CreateView):
    model = ScheduledJob
    template_name = 'scheduler/scheduled/form.html'
    fields = ['queue', 'name', 'payload', 'cron_expression', 'max_retries', 'backoff_strategy', 'backoff_delay', 'is_active']

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields['queue'].queryset = Queue.objects.filter(project__organization__user=self.request.user)
        return form

    def get_success_url(self):
        return reverse('scheduled_list_page')

    def form_valid(self, form):
        from croniter import croniter
        form.instance.next_run_at = croniter(form.instance.cron_expression, timezone.now()).get_next(timezone.datetime)
        messages.success(self.request, 'Scheduled job created successfully!')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_create'] = True
        return context


class ScheduledJobDetailPageView(LoginRequiredMixin, DetailView):
    model = ScheduledJob
    template_name = 'scheduler/scheduled/detail.html'
    context_object_name = 'scheduled_job'

    def get_queryset(self):
        return ScheduledJob.objects.filter(queue__project__organization__user=self.request.user).select_related('queue', 'queue__project')


class ScheduledJobUpdateView(LoginRequiredMixin, UpdateView):
    model = ScheduledJob
    template_name = 'scheduler/scheduled/form.html'
    fields = ['queue', 'name', 'payload', 'cron_expression', 'max_retries', 'backoff_strategy', 'backoff_delay', 'is_active']

    def get_queryset(self):
        return ScheduledJob.objects.filter(queue__project__organization__user=self.request.user)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields['queue'].queryset = Queue.objects.filter(project__organization__user=self.request.user)
        return form

    def get_success_url(self):
        return reverse('scheduled_detail_page', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        from croniter import croniter
        if 'cron_expression' in form.changed_data:
            form.instance.next_run_at = croniter(form.instance.cron_expression, timezone.now()).get_next(timezone.datetime)
        messages.success(self.request, 'Scheduled job updated successfully!')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_create'] = False
        return context


class ScheduledJobToggleView(LoginRequiredMixin, View):
    def post(self, request, pk):
        scheduled = get_object_or_404(ScheduledJob, pk=pk, queue__project__organization__user=request.user)
        scheduled.is_active = not scheduled.is_active
        scheduled.save()
        status = 'activated' if scheduled.is_active else 'deactivated'
        messages.success(request, f'Scheduled job {status}.')
        return redirect('scheduled_list_page')


class BatchJobPageView(LoginRequiredMixin, TemplateView):
    template_name = 'scheduler/jobs/batch.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['queues'] = Queue.objects.filter(project__organization__user=self.request.user)
        context['batch_jobs'] = BatchJob.objects.filter(project__organization__user=self.request.user).order_by('-created_at')[:20]
        return context


class BatchJobSubmitView(LoginRequiredMixin, View):
    def post(self, request):
        queue_id = request.POST.get('queue')
        name = request.POST.get('name')
        job_names = request.POST.getlist('job_names[]')
        job_payloads = request.POST.getlist('job_payloads[]')
        
        queue = get_object_or_404(Queue, pk=queue_id, project__organization__user=request.user)
        
        batch = BatchJob.objects.create(
            project=queue.project,
            name=name,
            total_jobs=len(job_names),
            status='PENDING'
        )
        
        for i, (name, payload) in enumerate(zip(job_names, job_payloads)):
            if name.strip():
                import json
                Job.objects.create(
                    queue=queue,
                    name=name.strip(),
                    payload=json.loads(payload) if payload else {},
                    status='QUEUED',
                    scheduled_at=timezone.now(),
                    batch_id=batch.id
                )
        
        batch.status = 'PARTIAL'
        batch.save()
        messages.success(request, f'Batch job created with {len(job_names)} jobs.')
        return redirect('batch_job_page')


class DLQPageView(LoginRequiredMixin, ListView):
    model = DeadLetterQueue
    template_name = 'scheduler/dlq/list.html'
    context_object_name = 'dlq_entries'
    paginate_by = 20

    def get_queryset(self):
        return DeadLetterQueue.objects.filter(job__queue__project__organization__user=self.request.user).select_related('job', 'job__queue', 'job__queue__project').order_by('-created_at')


class DLQRetryView(LoginRequiredMixin, View):
    def post(self, request, pk):
        dlq_entry = get_object_or_404(DeadLetterQueue, pk=pk, job__queue__project__organization__user=request.user)
        job = dlq_entry.job
        
        job.status = 'QUEUED'
        job.retry_count = 0
        job.scheduled_at = timezone.now()
        job.save()
        
        dlq_entry.resolved_at = timezone.now()
        dlq_entry.resolved_by = 'user'
        dlq_entry.resolution_notes = 'Retried via UI'
        dlq_entry.save()
        
        messages.success(request, 'Job re-queued for execution.')
        return redirect('dlq_page')