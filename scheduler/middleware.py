import time
from django.utils.deprecation import MiddlewareMixin
from django.http import JsonResponse
from django.core.cache import cache
from django.conf import settings
from .models import RateLimitRule


class RateLimitMiddleware(MiddlewareMixin):
    """Rate limiting middleware based on RateLimitRule model"""

    def process_request(self, request):
        # Skip rate limiting for non-API paths
        if not request.path.startswith('/api/'):
            return None

        # Get project from authentication
        if not hasattr(request, 'project') and not getattr(request, 'auth', None):
            # Try to get project from API key header
            api_key = request.headers.get('X-Project-Key')
            if api_key:
                try:
                    from .models import Project
                    project = Project.objects.get(api_key=api_key)
                    request.project = project
                except Project.DoesNotExist:
                    pass

        if not hasattr(request, 'project'):
            return None

        project = request.project
        endpoint = request.path
        method = request.method

        # Find matching rate limit rule
        try:
            rule = RateLimitRule.objects.get(
                project=project,
                endpoint=endpoint
            )
        except RateLimitRule.DoesNotExist:
            # No rate limit configured for this endpoint
            return None

        # Check rate limit
        cache_key = f"ratelimit:{project.id}:{endpoint}:{method}"
        current = cache.get(cache_key, 0)

        if current >= rule.max_requests:
            return JsonResponse(
                {
                    'error': 'Rate limit exceeded',
                    'limit': rule.max_requests,
                    'window_seconds': rule.window_seconds,
                    'retry_after': rule.window_seconds
                },
                status=429
            )

        # Increment counter
        cache.set(cache_key, current + 1, rule.window_seconds)

        # Add rate limit headers
        request.rate_limit = {
            'limit': rule.max_requests,
            'remaining': rule.max_requests - current - 1,
            'reset': rule.window_seconds
        }

        return None

    def process_response(self, request, response):
        # Add rate limit headers if available
        if hasattr(request, 'rate_limit'):
            response['X-RateLimit-Limit'] = str(request.rate_limit['limit'])
            response['X-RateLimit-Remaining'] = str(request.rate_limit['remaining'])
            response['X-RateLimit-Reset'] = str(request.rate_limit['reset'])
        return response