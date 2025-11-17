#!/bin/bash

# Script to get the public IP of the Airflow webserver
# Usage: ./get-airflow-url.sh [PROJECT_NAME]

set -e

PROJECT_NAME=${1:-alerta-utec-airflow}
CLUSTER_NAME=$PROJECT_NAME
SERVICE_NAME="${PROJECT_NAME}-webserver"

echo "🔍 Finding Airflow webserver public IP..."
echo "   Cluster: $CLUSTER_NAME"
echo "   Service: $SERVICE_NAME"
echo ""

# Get the task ARN
TASK_ARN=$(aws ecs list-tasks \
    --cluster "$CLUSTER_NAME" \
    --service-name "$SERVICE_NAME" \
    --query 'taskArns[0]' \
    --output text)

if [ "$TASK_ARN" == "None" ] || [ -z "$TASK_ARN" ]; then
    echo "❌ No running tasks found for service $SERVICE_NAME"
    echo "   Make sure the webserver service is running."
    exit 1
fi

echo "✅ Found task: ${TASK_ARN##*/}"

# Get the network interface ID
ENI_ID=$(aws ecs describe-tasks \
    --cluster "$CLUSTER_NAME" \
    --tasks "$TASK_ARN" \
    --query 'tasks[0].attachments[0].details[?name==`networkInterfaceId`].value' \
    --output text)

if [ -z "$ENI_ID" ]; then
    echo "❌ Could not find network interface for task"
    exit 1
fi

echo "✅ Network interface: $ENI_ID"

# Get the public IP
PUBLIC_IP=$(aws ec2 describe-network-interfaces \
    --network-interface-ids "$ENI_ID" \
    --query 'NetworkInterfaces[0].Association.PublicIp' \
    --output text)

if [ "$PUBLIC_IP" == "None" ] || [ -z "$PUBLIC_IP" ]; then
    echo "❌ No public IP found. Make sure AssignPublicIp is ENABLED for the service."
    exit 1
fi

echo ""
echo "🎉 Airflow Web UI is available at:"
echo ""
echo "   http://$PUBLIC_IP:8080"
echo ""
echo "   Username: admin"
echo "   Password: (the one you set during deployment)"
echo ""
