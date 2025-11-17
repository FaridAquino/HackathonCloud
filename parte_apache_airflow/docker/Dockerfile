# Dockerfile for Apache Airflow on ECS Fargate
# Optimized for AlertaUTEC Hackathon

FROM apache/airflow:2.8.0-python3.11

# Switch to root to install system dependencies
USER root

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    postgresql-client \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create necessary directories
RUN mkdir -p /opt/airflow/dags /opt/airflow/logs /opt/airflow/plugins /opt/airflow/config \
    && chown -R airflow:root /opt/airflow

# Switch back to airflow user
USER airflow

# Set Airflow home
ENV AIRFLOW_HOME=/opt/airflow

# Copy requirements file
COPY --chown=airflow:root config/requirements.txt /opt/airflow/requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir --user -r /opt/airflow/requirements.txt

# Copy DAGs to temporary location (will be copied to EFS by entrypoint)
COPY --chown=airflow:root dags/ /tmp/airflow-dags/

# Copy configuration
COPY --chown=airflow:root config/airflow.cfg /opt/airflow/airflow.cfg

# Copy entrypoint script
COPY --chown=airflow:root docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Expose Airflow webserver port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Use custom entrypoint
ENTRYPOINT ["/entrypoint.sh"]

# Default command (can be overridden)
CMD ["webserver"]
