#!/bin/bash
# Deploy AlertaUTEC Airflow Infrastructure to AWS
# This script deploys the complete infrastructure using CloudFormation

set -e

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Configuration
REGION=${AWS_DEFAULT_REGION:-us-east-1}
PROJECT_NAME=${PROJECT_NAME:-alerta-utec-airflow}
STACK_NAME="${PROJECT_NAME}-master"
TEMPLATES_BUCKET="${PROJECT_NAME}-cfn-templates-$(date +%s)"
IMAGE_TAG=${1:-latest}

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}AlertaUTEC Airflow - Deploy${NC}"
echo -e "${GREEN}========================================${NC}"

# Prompt for required parameters
read -sp "Enter RDS Database Password (min 8 chars): " DB_PASSWORD
echo
if [ ${#DB_PASSWORD} -lt 8 ]; then
    echo -e "${RED}Error: Password must be at least 8 characters${NC}"
    exit 1
fi

read -sp "Enter Airflow Admin Password (min 8 chars): " ADMIN_PASSWORD
echo
if [ ${#ADMIN_PASSWORD} -lt 8 ]; then
    echo -e "${RED}Error: Password must be at least 8 characters${NC}"
    exit 1
fi

read -p "Enter DynamoDB Table Name (default: Incidents): " DYNAMODB_TABLE
DYNAMODB_TABLE=${DYNAMODB_TABLE:-Incidents}

# Generate webserver secret key
WEBSERVER_SECRET=$(openssl rand -hex 16)

echo -e "\n${YELLOW}Configuration:${NC}"
echo "  Region: $REGION"
echo "  Project: $PROJECT_NAME"
echo "  Stack Name: $STACK_NAME"
echo "  Templates Bucket: $TEMPLATES_BUCKET"
echo "  Image Tag: $IMAGE_TAG"
echo "  DynamoDB Table: $DYNAMODB_TABLE"

# Step 1: Create S3 bucket for templates
echo -e "\n${YELLOW}[1/5] Creating S3 bucket for CloudFormation templates...${NC}"
aws s3 mb s3://$TEMPLATES_BUCKET --region $REGION 2>/dev/null || true
aws s3api put-public-access-block \
    --bucket $TEMPLATES_BUCKET \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" \
    2>/dev/null || true

echo -e "${GREEN}✓ Bucket created${NC}"

# Step 2: Upload templates
echo -e "\n${YELLOW}[2/5] Uploading CloudFormation templates...${NC}"
aws s3 sync cloudformation/ s3://$TEMPLATES_BUCKET/ --exclude "*" --include "*.yaml"
echo -e "${GREEN}✓ Templates uploaded${NC}"

# Step 3: Validate master template
echo -e "\n${YELLOW}[3/5] Validating CloudFormation templates...${NC}"
aws cloudformation validate-template \
    --template-url https://${TEMPLATES_BUCKET}.s3.${REGION}.amazonaws.com/master-stack.yaml \
    --region $REGION > /dev/null

echo -e "${GREEN}✓ Templates validated${NC}"

# Step 4: Deploy stack
echo -e "\n${YELLOW}[4/5] Deploying CloudFormation stack...${NC}"
echo "This may take 15-20 minutes..."

aws cloudformation create-stack \
    --stack-name $STACK_NAME \
    --template-url https://${TEMPLATES_BUCKET}.s3.${REGION}.amazonaws.com/master-stack.yaml \
    --parameters \
        ParameterKey=ProjectName,ParameterValue=$PROJECT_NAME \
        ParameterKey=DBPassword,ParameterValue=$DB_PASSWORD \
        ParameterKey=AirflowAdminPassword,ParameterValue=$ADMIN_PASSWORD \
        ParameterKey=AirflowWebserverSecretKey,ParameterValue=$WEBSERVER_SECRET \
        ParameterKey=DynamoDBTableName,ParameterValue=$DYNAMODB_TABLE \
        ParameterKey=ECRImageTag,ParameterValue=$IMAGE_TAG \
        ParameterKey=TemplatesBucketName,ParameterValue=$TEMPLATES_BUCKET \
    --capabilities CAPABILITY_NAMED_IAM \
    --region $REGION

# Wait for stack creation
echo -e "\n${BLUE}Waiting for stack creation...${NC}"
aws cloudformation wait stack-create-complete \
    --stack-name $STACK_NAME \
    --region $REGION

# Step 5: Get outputs
echo -e "\n${YELLOW}[5/5] Retrieving stack outputs...${NC}"
OUTPUTS=$(aws cloudformation describe-stacks \
    --stack-name $STACK_NAME \
    --region $REGION \
    --query 'Stacks[0].Outputs' \
    --output json)

AIRFLOW_URL=$(echo $OUTPUTS | jq -r '.[] | select(.OutputKey=="AirflowWebURL") | .OutputValue')
ECR_URI=$(echo $OUTPUTS | jq -r '.[] | select(.OutputKey=="ECRRepositoryUri") | .OutputValue')
ECS_CLUSTER=$(echo $OUTPUTS | jq -r '.[] | select(.OutputKey=="ECSClusterName") | .OutputValue')

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}✓ Deployment Complete!${NC}"
echo -e "${GREEN}========================================${NC}"

echo -e "\n${BLUE}Access Information:${NC}"
echo -e "  Airflow Web UI: ${GREEN}$AIRFLOW_URL${NC}"
echo -e "  Username: ${YELLOW}admin${NC}"
echo -e "  Password: ${YELLOW}[the password you entered]${NC}"

echo -e "\n${BLUE}Infrastructure Details:${NC}"
echo -e "  ECR Repository: $ECR_URI"
echo -e "  ECS Cluster: $ECS_CLUSTER"
echo -e "  Region: $REGION"

echo -e "\n${BLUE}Next Steps:${NC}"
echo "  1. Wait 2-3 minutes for services to fully start"
echo "  2. Access Airflow UI at: $AIRFLOW_URL"
echo "  3. Enable DAGs in the UI"
echo "  4. Monitor CloudWatch Logs: /ecs/$PROJECT_NAME"

echo -e "\n${YELLOW}Important:${NC}"
echo "  - Database password and admin password are stored securely"
echo "  - To update DAGs: run ./scripts/update_dags.sh"
echo "  - To cleanup all resources: run ./scripts/cleanup.sh"

# Save deployment info
cat > deployment-info.txt <<EOF
AlertaUTEC Airflow Deployment Information
==========================================
Deployment Date: $(date)
Region: $REGION
Stack Name: $STACK_NAME
Templates Bucket: $TEMPLATES_BUCKET

Airflow Web UI: $AIRFLOW_URL
ECR Repository: $ECR_URI
ECS Cluster: $ECS_CLUSTER

DynamoDB Table: $DYNAMODB_TABLE
EOF

echo -e "\n${GREEN}Deployment info saved to: deployment-info.txt${NC}"
