from django.db import connection
from django.http import HttpResponse, JsonResponse
from django_redis import get_redis_connection


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


def readyz(request):
    checks = {}

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"error: {e.__class__.__name__}"

    try:
        get_redis_connection("default").ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e.__class__.__name__}"

    try:
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        if executor.migration_plan(targets):
            checks["migrations"] = "pending"
        else:
            checks["migrations"] = "ok"
    except Exception as e:
        checks["migrations"] = f"error: {e.__class__.__name__}"

    healthy = all(v == "ok" for v in checks.values())
    return JsonResponse(checks, status=200 if healthy else 503)
