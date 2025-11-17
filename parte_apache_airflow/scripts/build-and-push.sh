#!/bin/bash
# Build and Push Docker Image to ECR
# This script builds the Airflow Docker image and pushes it to Amazon ECR

set -e

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Configuration
REGION=${AWS_DEFAULT_REGION:-us-east-1}
ACCOUNT_ID=${AWS_ACCOUNT_ID}
ECR_REPOSITORY=${ECR_REPOSITORY:-alerta-utec-airflow}
IMAGE_TAG=${1:-latest}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}AlertaUTEC Airflow - Build and Push${NC}"
echo -e "${GREEN}========================================${NC}"

# Validate required variables
if [ -z "$ACCOUNT_ID" ]; then
    echo -e "${RED}Error: AWS_ACCOUNT_ID not set${NC}"
    echo "Please set it in .env file or export it"
    exit 1
fi

ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPOSITORY}"

echo -e "\n${YELLOW}Configuration:${NC}"
echo "  Region: $REGION"
echo "  Account ID: $ACCOUNT_ID"
echo "  Repository: $ECR_REPOSITORY"
echo "  Image Tag: $IMAGE_TAG"
echo "  ECR URI: $ECR_URI"

# Step 1: Login to ECR
echo -e "\n${YELLOW}[1/4] Logging in to Amazon ECR...${NC}"
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_URI

# Step 2: Build Docker image
echo -e "\n${YELLOW}[2/4] Building Docker image...${NC}"
docker build -t $ECR_REPOSITORY:$IMAGE_TAG -f docker/Dockerfile .

if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Docker build failed${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Docker image built successfully${NC}"

# Step 3: Tag image
echo -e "\n${YELLOW}[3/4] Tagging image...${NC}"
docker tag $ECR_REPOSITORY:$IMAGE_TAG $ECR_URI:$IMAGE_TAG

# Also tag as latest if not already
if [ "$IMAGE_TAG" != "latest" ]; then
    docker tag $ECR_REPOSITORY:$IMAGE_TAG $ECR_URI:latest
fi

echo -e "${GREEN}✓ Image tagged${NC}"

# Step 4: Push to ECR
echo -e "\n${YELLOW}[4/4] Pushing image to ECR...${NC}"
docker push $ECR_URI:$IMAGE_TAG

if [ "$IMAGE_TAG" != "latest" ]; then
    docker push $ECR_URI:latest
fi

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}✓ Image pushed successfully!${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "\nImage URI: ${GREEN}$ECR_URI:$IMAGE_TAG${NC}"
echo -e "\nYou can now deploy using this image with:"
echo -e "  ${YELLOW}./scripts/deploy.sh $IMAGE_TAG${NC}"
