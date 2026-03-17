#!/bin/bash
set -e

# Wait for dependent services (postgres, redis) if needed
wait_for_service() {
    local host="$1"
    local port="$2"
    local retries=30
    local wait=2

    echo "Waiting for ${host}:${port}..."
    while ! python -c "import socket; s=socket.create_connection(('${host}', ${port}), timeout=2); s.close()" 2>/dev/null; do
        retries=$((retries - 1))
        if [ "$retries" -le 0 ]; then
            echo "ERROR: ${host}:${port} not available after timeout"
            exit 1
        fi
        sleep "$wait"
    done
    echo "${host}:${port} is ready."
}

case "$1" in
    api)
        # Wait for Postgres and Redis
        wait_for_service "${POSTGRES_HOST:-postgres}" "${POSTGRES_PORT:-5432}"
        wait_for_service "${REDIS_HOST:-redis}" "${REDIS_PORT:-6379}"

        echo "Running migrations..."
        python manage.py migrate --noinput

        echo "Starting Gunicorn..."
        exec gunicorn config.wsgi:application \
            --bind 0.0.0.0:8000 \
            --workers "${GUNICORN_WORKERS:-3}" \
            --timeout 120 \
            --access-logfile - \
            --error-logfile -
        ;;

    worker)
        wait_for_service "${REDIS_HOST:-redis}" "${REDIS_PORT:-6379}"

        echo "Starting Celery worker..."
        exec celery -A config.celery worker \
            --loglevel="${CELERY_LOG_LEVEL:-info}" \
            --concurrency="${CELERY_CONCURRENCY:-2}"
        ;;

    beat)
        wait_for_service "${REDIS_HOST:-redis}" "${REDIS_PORT:-6379}"

        echo "Starting Celery beat..."
        exec celery -A config.celery beat \
            --loglevel="${CELERY_LOG_LEVEL:-info}"
        ;;

    consumer)
        wait_for_service "${KAFKA_HOST:-redpanda}" "${KAFKA_PORT:-9092}"

        echo "Starting analytics consumer..."
        exec python manage.py run_analytics_consumer
        ;;

    migrate)
        wait_for_service "${POSTGRES_HOST:-postgres}" "${POSTGRES_PORT:-5432}"

        echo "Running migrations..."
        exec python manage.py migrate --noinput
        ;;

    test)
        echo "Running tests..."
        exec python -m pytest
        ;;

    *)
        exec "$@"
        ;;
esac
