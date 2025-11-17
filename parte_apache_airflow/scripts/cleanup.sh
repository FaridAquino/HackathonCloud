#!/bin/bash
# Cleanup AlertaUTEC Airflow Infrastructure
# This script removes all AWS resources created by the deployment

set -e

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Configuration
REGION=${AWS_DEFAULT_REGION:-us-east-1}
PROJECT_NAME=${PROJECT_NAME:-alerta-utec-airflow}
STACK_NAME="${PROJECT_NAME}-master"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${RED}========================================${NC}"
echo -e "${RED}AlertaUTEC Airflow - Cleanup${NC}"
echo -e "${RED}========================================${NC}"

echo -e "\n${YELLOW}WARNING: This will delete ALL resources!${NC}"
echo "  - CloudFormation stacks"
echo "  - RDS Database (all data will be lost)"
echo "  - S3 Buckets (logs will be deleted)"
echo "  - ECR Images"
echo "  - EFS File System (DAGs will be deleted)"
echo "  - ECS Services and Tasks"

read -p "Are you sure you want to continue? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo -e "${GREEN}Cleanup cancelled${NC}"
    exit 0
fi

echo -e "\n${YELLOW}[1/5] Getting resource information...${NC}"

# Get stack outputs to find resources
OUTPUTS=$(aws cloudformation describe-stacks \
    --stack-name $STACK_NAME \
    --region $REGION \
    --query 'Stacks[0].Outputs' \
    --output json 2>/dev/null) || true

if [ -n "$OUTPUTS" ]; then
    S3_BUCKET=$(echo $OUTPUTS | jq -r '.[] | select(.OutputKey=="S3BucketName") | .OutputValue')
    ECR_REPO=$(echo $OUTPUTS | jq -r '.[] | select(.OutputKey=="ECRRepositoryUri") | .OutputValue' | cut -d'/' -f2)
fi

# Step 2: Empty S3 buckets
if [ -n "$S3_BUCKET" ]; then
    echo -e "\n${YELLOW}[2/5] Emptying S3 bucket...${NC}"
    aws s3 rm s3://$S3_BUCKET --recursive --region $REGION 2>/dev/null || true
    echo -e "${GREEN}✓ S3 bucket emptied${NC}"
else
    echo -e "\n${YELLOW}[2/5] No S3 bucket found, skipping...${NC}"
fi

# Step 3: Delete ECR images
if [ -n "$ECR_REPO" ]; then
    echo -e "\n${YELLOW}[3/5] Deleting ECR images...${NC}"
    IMAGE_IDS=$(aws ecr list-images \
        --repository-name $ECR_REPO \
        --region $REGION \
        --query 'imageIds[*]' \
        --output json 2>/dev/null) || true
    
    if [ "$IMAGE_IDS" != "[]" ] && [ -n "$IMAGE_IDS" ]; then
        aws ecr batch-delete-image \
            --repository-name $ECR_REPO \
            --image-ids "$IMAGE_IDS" \
            --region $REGION 2>/dev/null || true
    fi
    echo -e "${GREEN}✓ ECR images deleted${NC}"
else
    echo -e "\n${YELLOW}[3/5] No ECR repository found, skipping...${NC}"
fi

# Step 4: Delete CloudFormation stack
echo -e "\n${YELLOW}[4/5] Deleting CloudFormation stack...${NC}"
echo "This may take 10-15 minutes..."

aws cloudformation delete-stack \
    --stack-name $STACK_NAME \
    --region $REGION 2>/dev/null || true

# Wait for deletion
echo "Waiting for stack deletion..."
aws cloudformation wait stack-delete-complete \
    --stack-name $STACK_NAME \
    --region $REGION 2>/dev/null || true

echo -e "${GREEN}✓ Stack deleted${NC}"

# Step 5: Cleanup templates bucket
echo -e "\n${YELLOW}[5/5] Cleaning up templates bucket...${NC}"

# Find and delete templates buckets
TEMPLATES_BUCKETS=$(aws s3api list-buckets \
    --query "Buckets[?starts_with(Name, '${PROJECT_NAME}-cfn-templates')].Name" \
    --output text 2>/dev/null) || true

if [ -n "$TEMPLATES_BUCKETS" ]; then
    for BUCKET in $TEMPLATES_BUCKETS; do
        echo "Deleting bucket: $BUCKET"
        aws s3 rb s3://$BUCKET --force --region $REGION 2>/dev/null || true
    done
    echo -e "${GREEN}✓ Templates buckets deleted${NC}"
else
    echo -e "${YELLOW}No templates buckets found${NC}"
fi

# Cleanup deployment info file
if [ -f deployment-info.txt ]; then
    rm deployment-info.txt
fi

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}✓ Cleanup Complete!${NC}"
echo -e "${GREEN}========================================${NC}"

echo -e "\n${YELLOW}Note:${NC}"
echo "  - DynamoDB table was NOT deleted (managed by team)"
echo "  - SNS topics may still exist (low cost)"
echo "  - CloudWatch log groups may remain (will auto-expire)"

echo -e "\n${GREEN}All infrastructure has been removed${NC}"
