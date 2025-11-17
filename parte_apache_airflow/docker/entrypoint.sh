#!/bin/bash
# Entrypoint script for Airflow containers on ECS

set -e

# Function to wait for database
wait_for_db() {
    echo "Waiting for database to be ready..."
    
    # Check if using SQLite or PostgreSQL
    if [[ "${AIRFLOW__DATABASE__SQL_ALCHEMY_CONN}" == sqlite* ]]; then
        echo "Using SQLite - no wait needed"
        return 0
    fi
    
    # Extract DB connection details from environment for PostgreSQL
    DB_HOST="${AIRFLOW__DATABASE__SQL_ALCHEMY_CONN#*@}"
    DB_HOST="${DB_HOST%%/*}"
    DB_HOST="${DB_HOST%%:*}"
    
    MAX_TRIES=30
    TRIES=0
    
    while [ $TRIES -lt $MAX_TRIES ]; do
        if pg_isready -h "$DB_HOST" -U postgres > /dev/null 2>&1; then
            echo "Database is ready!"
            return 0
        fi
        
        echo "Database not ready yet (attempt $((TRIES+1))/$MAX_TRIES)..."
        sleep 5
        TRIES=$((TRIES+1))
    done
    
    echo "ERROR: Database did not become ready in time"
    exit 1
}

# Function to initialize database
init_db() {
    echo "Initializing Airflow database..."
    airflow db migrate
    
    # Create admin user if not exists
    airflow users create \
        --username "${AIRFLOW_ADMIN_USERNAME:-admin}" \
        --password "${AIRFLOW_ADMIN_PASSWORD:-admin}" \
        --firstname Admin \
        --lastname User \
        --role Admin \
        --email "${AIRFLOW_ADMIN_EMAIL:-admin@example.com}" \
        2>/dev/null || echo "Admin user already exists"
}

# Function to sync DAGs to EFS
sync_dags() {
    echo "Syncing DAGs from image to EFS..."
    if [ -d "/tmp/airflow-dags" ] && [ "$(ls -A /tmp/airflow-dags)" ]; then
        mkdir -p /opt/airflow/dags
        cp -r /tmp/airflow-dags/* /opt/airflow/dags/
        echo "DAGs synced successfully"
        ls -la /opt/airflow/dags/
    else
        echo "No DAGs found in /tmp/airflow-dags"
    fi
}

# Main execution
echo "Starting Airflow component: $1"

# Set AWS region if not set
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"

# Sync DAGs to EFS
sync_dags

# Wait for database to be ready
wait_for_db

# Always initialize database if needed (safe to run multiple times)
echo "Initializing database (if needed)..."
init_db

case "$1" in
    webserver)
        echo "Starting Airflow Webserver..."
        exec airflow webserver
        ;;
    scheduler)
        echo "Starting Airflow Scheduler..."
        exec airflow scheduler
        ;;
    worker)
        echo "Starting Airflow Worker..."
        exec airflow celery worker
        ;;
    *)
        echo "Unknown command: $1"
        echo "Usage: $0 {webserver|scheduler|worker}"
        exit 1
        ;;
esac
