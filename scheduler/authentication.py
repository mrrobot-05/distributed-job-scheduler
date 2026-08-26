from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission

from .models import Project


class ProjectKeyAuthentication(BaseAuthentication):

    def authenticate(self, request):
        api_key = request.headers.get("X-Project-Key")

        if not api_key:
            raise AuthenticationFailed(
                "X-Project-Key header is required."
            )

        try:
            project = Project.objects.get(api_key=api_key, is_active=True)
        except Project.DoesNotExist:
            raise AuthenticationFailed(
                "Invalid project key."
            )

        return (None, project)


class IsProjectAuthenticated(BasePermission):
    """Allow access only if request.auth is set by ProjectKeyAuthentication."""

    def has_permission(self, request, view):
        return request.auth is not None
