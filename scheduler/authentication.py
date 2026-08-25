from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import Project


class ProjectKeyAuthentication(BaseAuthentication):

    def authenticate(self, request):
        api_key = request.headers.get("X-Project-Key")

        if not api_key:
            raise AuthenticationFailed(
                "X-Project-Key header is required."
            )

        try:
            project = Project.objects.get(api_key=api_key)
        except Project.DoesNotExist:
            raise AuthenticationFailed(
                "Invalid project key."
            )

        return (None, project)