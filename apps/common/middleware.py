import json

from django.http import JsonResponse

from apps.common.models import IdempotencyKey

IDEMPOTENCY_HEADER = "HTTP_IDEMPOTENCY_KEY"


class IdempotencyKeyMiddleware:
    """Middleware that checks Idempotency-Key header on POST requests.

    If the key was seen before, return the cached response.
    Otherwise, let the request through and cache the response.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method != "POST":
            return self.get_response(request)

        idem_key = request.META.get(IDEMPOTENCY_HEADER)
        if not idem_key:
            return self.get_response(request)

        # Check for existing key
        try:
            existing = IdempotencyKey.objects.get(key=idem_key)
            return JsonResponse(
                existing.response_body,
                status=existing.response_status,
                safe=False,
            )
        except IdempotencyKey.DoesNotExist:
            pass

        response = self.get_response(request)

        # Cache the response for this key
        if response.get("Content-Type", "").startswith("application/json"):
            try:
                body = json.loads(response.content)
            except (json.JSONDecodeError, ValueError):
                body = {}

            IdempotencyKey.objects.create(
                key=idem_key,
                response_status=response.status_code,
                response_body=body,
            )

        return response
