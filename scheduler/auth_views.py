from django.contrib.auth import views as auth_views
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django import forms
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db import transaction
from django.contrib.auth import get_user_model

from scheduler.models import Organization, Project, Queue

User = get_user_model()


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)
    organization_name = forms.CharField(max_length=255, required=True, label="Organization Name")
    project_name = forms.CharField(max_length=255, required=True, label="Project Name")

    class Meta:
        model = User
        fields = ("username", "email", "organization_name", "project_name", "password1", "password2")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            with transaction.atomic():
                user.save()
                # Create organization
                org = Organization.objects.create(
                    name=self.cleaned_data["organization_name"],
                    slug=self.cleaned_data["organization_name"].lower().replace(" ", "-")
                )
                # Create project with API key
                import uuid
                project = Project.objects.create(
                    organization=org,
                    name=self.cleaned_data["project_name"],
                    api_key=str(uuid.uuid4()),
                    is_active=True
                )
                # Create default queue
                Queue.objects.create(
                    project=project,
                    name="default",
                    priority=5,
                    concurrency_limit=5,
                    is_paused=False
                )
        return user


def register(request):
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            form.save()
            username = form.cleaned_data.get("username")
            messages.success(request, f"Account created for {username}! You can now log in.")
            return redirect("login")
    else:
        form = RegisterForm()
    return render(request, "scheduler/register.html", {"form": form})