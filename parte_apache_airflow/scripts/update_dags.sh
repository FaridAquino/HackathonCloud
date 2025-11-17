#!/bin/bash
# Update DAGs in Running Airflow ECS Tasks
# This script updates DAGs by copying them to EFS

set -e

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Configuration
REGION=${AWS_DEFAULT_REGION:-us-east-1}
PROJECT_NAME=${PROJECT_NAME:-alerta-utec-airflow}
ECS_CLUSTER=${ECS_CLUSTER_NAME:-"${PROJECT_NAME}"}

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}AlertaUTEC Airflow - Update DAGs${NC}"
echo -e "${GREEN}========================================${NC}"

echo -e "\n${BLUE}Choose update method:${NC}"
echo "  1. Copy DAGs to EFS (faster, no restart)"
echo "  2. Rebuild and redeploy Docker image (slower, full update)"
read -p "Enter choice (1 or 2): " CHOICE

if [ "$CHOICE" = "1" ]; then
    echo -e "\n${YELLOW}Method 1: Copying DAGs to EFS${NC}"
    echo "This requires:"
    echo "  - An EC2 instance with EFS mounted, OR"
    echo "  - AWS EFS File System Client access"
    
    # Get EFS File System ID
    EFS_ID=$(aws cloudformation describe-stacks \
        --stack-name ${PROJECT_NAME}-master \
        --region $REGION \
        --query 'Stacks[0].Outputs[?OutputKey==`EFSFileSystemId`].OutputValue' \
        --output text)
    
    if [ -z "$EFS_ID" ]; then
        echo -e "${RED}Error: Could not find EFS File System ID${NC}"
        exit 1
    fi
    
    echo -e "\n${BLUE}EFS File System ID: ${GREEN}$EFS_ID${NC}"
    
    echo -e "\n${YELLOW}Option A: Using EC2 Instance${NC}"
    echo "1. Launch a temporary EC2 instance in the same VPC"
    echo "2. Mount EFS: sudo mount -t nfs4 ${EFS_ID}.efs.${REGION}.amazonaws.com:/ /mnt/efs"
    echo "3. Copy DAGs: sudo cp -r dags/* /mnt/efs/dags/"
    echo "4. Wait 60 seconds for Airflow to detect changes"
    echo "5. Terminate EC2 instance"
    
    echo -e "\n${YELLOW}Option B: Using ECS Exec${NC}"
    echo "1. Enable ECS Exec on the scheduler service"
    echo "2. Run: aws ecs execute-command --cluster $ECS_CLUSTER --task <task-id> --container scheduler --interactive --command '/bin/bash'"
    echo "3. Inside container: cp /path/to/new/dags/* /opt/airflow/dags/"
    
    echo -e "\n${BLUE}For automated deployment, use Method 2 (rebuild image)${NC}"

elif [ "$CHOICE" = "2" ]; then
    echo -e "\n${YELLOW}Method 2: Rebuilding Docker Image${NC}"
    
    # Generate new tag
    NEW_TAG="v$(date +%Y%m%d-%H%M%S)"
    
    echo -e "\n${YELLOW}[1/3] Building and pushing new image...${NC}"
    ./scripts/build-and-push.sh $NEW_TAG
    
    echo -e "\n${YELLOW}[2/3] Updating ECS services...${NC}"
    
    # Get service names
    WEBSERVER_SERVICE="${PROJECT_NAME}-webserver"
    SCHEDULER_SERVICE="${PROJECT_NAME}-scheduler"
    
    # Update webserver service
    echo "Updating webserver service..."
    aws ecs update-service \
        --cluster $ECS_CLUSTER \
        --service $WEBSERVER_SERVICE \
        --force-new-deployment \
        --region $REGION > /dev/null
    
    # Update scheduler service
    echo "Updating scheduler service..."
    aws ecs update-service \
        --cluster $ECS_CLUSTER \
        --service $SCHEDULER_SERVICE \
        --force-new-deployment \
        --region $REGION > /dev/null
    
    echo -e "${GREEN}✓ Services updated${NC}"
    
    echo -e "\n${YELLOW}[3/3] Waiting for deployment...${NC}"
    echo "This may take 5-10 minutes..."
    
    # Wait for webserver
    echo "Waiting for webserver..."
    aws ecs wait services-stable \
        --cluster $ECS_CLUSTER \
        --services $WEBSERVER_SERVICE \
        --region $REGION
    
    # Wait for scheduler
    echo "Waiting for scheduler..."
    aws ecs wait services-stable \
        --cluster $ECS_CLUSTER \
        --services $SCHEDULER_SERVICE \
        --region $REGION
    
    echo -e "\n${GREEN}========================================${NC}"
    echo -e "${GREEN}✓ DAGs Updated Successfully!${NC}"
    echo -e "${GREEN}========================================${NC}"
    
    echo -e "\n${BLUE}New image tag: ${GREEN}$NEW_TAG${NC}"
    echo -e "${BLUE}Services restarted with updated DAGs${NC}"
    
    echo -e "\n${YELLOW}Next steps:${NC}"
    echo "  1. Access Airflow UI"
    echo "  2. Verify DAGs are visible"
    echo "  3. Enable/trigger DAGs as needed"
    
else
    echo -e "${RED}Invalid choice${NC}"
    exit 1
fi

echo -e "\n${BLUE}Monitoring commands:${NC}"
echo "  Check service status:"
echo "    aws ecs describe-services --cluster $ECS_CLUSTER --services ${PROJECT_NAME}-webserver --region $REGION"
echo ""
echo "  View logs:"
echo "    aws logs tail /ecs/$PROJECT_NAME --follow --region $REGION"
echo ""
echo "  List running tasks:"
echo "    aws ecs list-tasks --cluster $ECS_CLUSTER --region $REGION"
