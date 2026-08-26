import time
from django.utils.deprecation import MiddlewareMixin
from django.http import JsonResponse
from django.core.cache import cache
from django.conf import settings
from .models import RateLimitRule


def _get_client_ip(request):
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


AUTH_RATE_LIMIT_MAX = 20
AUTH_RATE_LIMIT_WINDOW = 60


class RateLimitMiddleware(MiddlewareMixin):
    """Rate limiting middleware based on RateLimitRule model"""

    def process_request(self, request):
        # Rate limit auth endpoints (login/register) by IP
        if request.path in ('/login/', '/register/') and request.method == 'POST':
            ip = _get_client_ip(request)
            if ip:
                cache_key = f"authratelimit:{ip}:{request.path}"
                current = cache.get(cache_key, 0)
                if current >= AUTH_RATE_LIMIT_MAX:
                    return JsonResponse(
                        {
                            'error': 'Too many authentication attempts. Please try again later.',
                        },
                        status=429
                    )
                cache.set(cache_key, current + 1, AUTH_RATE_LIMIT_WINDOW)

        # Skip API rate limiting for non-API paths
        if not request.path.startswith('/api/'):
            return None

        # Get project from authentication
        if not hasattr(request, 'project') and not getattr(request, 'auth', None):
            # Try to get project from API key header
            api_key = request.headers.get('X-Project-Key')
            if api_key:
                try:
                    from .models import Project
                    project = Project.objects.get(api_key=api_key, is_active=True)
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