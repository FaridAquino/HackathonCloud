# Troubleshooting Guide - AlertaUTEC Airflow

Guía completa para resolver problemas comunes en el deployment de Airflow en ECS/Fargate.

## Índice

1. [Deployment Issues](#deployment-issues)
2. [ECS Task Issues](#ecs-task-issues)
3. [Database Connection Issues](#database-connection-issues)
4. [EFS Mount Issues](#efs-mount-issues)
5. [ALB & Health Check Issues](#alb--health-check-issues)
6. [DAG Execution Issues](#dag-execution-issues)
7. [AWS Service Limits](#aws-service-limits)
8. [Performance Issues](#performance-issues)
9. [Logging & Debugging](#logging--debugging)
10. [Common Error Messages](#common-error-messages)

## Deployment Issues

### Issue: CloudFormation Stack Creation Failed

**Symptoms**:
- Stack status: `CREATE_FAILED` or `ROLLBACK_COMPLETE`
- Deployment script stops with error

**Diagnosis**:

```bash
# Check stack events
aws cloudformation describe-stack-events \
    --stack-name alerta-utec-airflow-master \
    --region us-east-1 \
    --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`].[LogicalResourceId,ResourceStatusReason]' \
    --output table
```

**Common Causes & Solutions**:

#### 1. Insufficient IAM Permissions

**Error**: `User: arn:aws:iam::xxx:user/xxx is not authorized to perform: iam:CreateRole`

**Solution**:
```bash
# Verify IAM permissions
aws iam get-user --query 'User.Arn'

# If using AWS Academy, ensure you're using the correct credentials
aws configure list
```

#### 2. Service Quota Exceeded

**Error**: `You have exceeded the limit of VPCs in this region`

**Solution**:
```bash
# Check current VPC count
aws ec2 describe-vpcs --query 'Vpcs[*].[VpcId,Tags[?Key==`Name`].Value|[0]]' --output table

# Request quota increase (if available)
aws service-quotas request-service-quota-increase \
    --service-code vpc \
    --quota-code L-F678F1CE \
    --desired-value 10

# Or delete unused VPCs
aws ec2 delete-vpc --vpc-id vpc-xxx
```

#### 3. ECR Repository Already Exists

**Error**: `Repository with name 'alerta-utec-airflow' already exists`

**Solution**:
```bash
# This is usually OK - the stack will use the existing repo
# If you want to start fresh, delete the repo first:
aws ecr delete-repository \
    --repository-name alerta-utec-airflow \
    --force \
    --region us-east-1
```

#### 4. S3 Bucket Name Conflict

**Error**: `Bucket name already exists`

**Solution**:
```bash
# Edit deploy.sh to generate unique bucket name
# Already implemented with timestamp suffix
# Ensure TEMPLATES_BUCKET has unique suffix in script
```

**General Cleanup & Retry**:

```bash
# Delete failed stack
aws cloudformation delete-stack \
    --stack-name alerta-utec-airflow-master \
    --region us-east-1

# Wait for deletion
aws cloudformation wait stack-delete-complete \
    --stack-name alerta-utec-airflow-master \
    --region us-east-1

# Retry deployment
./scripts/deploy.sh
```

### Issue: Templates Bucket Upload Failed

**Symptoms**:
- Error during `[2/5] Uploading CloudFormation templates...`
- S3 access denied

**Solution**:

```bash
# Check bucket exists
aws s3 ls | grep cfn-templates

# Check bucket policy
aws s3api get-public-access-block --bucket [BUCKET-NAME]

# If needed, recreate bucket
aws s3 mb s3://alerta-utec-airflow-cfn-templates-$(date +%s) --region us-east-1

# Retry upload
aws s3 sync cloudformation/ s3://[BUCKET-NAME]/ --exclude "*" --include "*.yaml"
```

## ECS Task Issues

### Issue: ECS Tasks Failing to Start

**Symptoms**:
- Service shows `RUNNING: 0` despite `DESIRED: 1`
- Tasks appear in "Stopped" status immediately

**Diagnosis**:

```bash
# List recent stopped tasks
aws ecs list-tasks \
    --cluster alerta-utec-airflow \
    --desired-status STOPPED \
    --region us-east-1

# Get details of most recent stopped task
TASK_ARN=$(aws ecs list-tasks \
    --cluster alerta-utec-airflow \
    --desired-status STOPPED \
    --region us-east-1 \
    --query 'taskArns[0]' \
    --output text)

aws ecs describe-tasks \
    --cluster alerta-utec-airflow \
    --tasks $TASK_ARN \
    --region us-east-1 \
    --query 'tasks[0].{StoppedReason:stoppedReason,Containers:containers[*].{Name:name,Reason:reason,ExitCode:exitCode}}'
```

**Common Causes & Solutions**:

#### 1. Image Pull Failed

**Error**: `CannotPullContainerError: Error response from daemon`

**Solution**:
```bash
# Verify image exists in ECR
aws ecr describe-images \
    --repository-name alerta-utec-airflow \
    --region us-east-1

# If no images, rebuild and push
./scripts/build-and-push.sh

# Check ECR permissions in Task Execution Role
aws iam get-role-policy \
    --role-name alerta-utec-airflow-ecs-execution-role \
    --policy-name AmazonECSTaskExecutionRolePolicy
```

#### 2. Task Execution Role Missing Permissions

**Error**: `ResourceInitializationError: unable to pull secrets or registry auth`

**Solution**:
```bash
# Verify Task Execution Role has correct policies
aws iam list-attached-role-policies \
    --role-name alerta-utec-airflow-ecs-execution-role

# Should include:
# - AmazonECSTaskExecutionRolePolicy
# - SSMParameterAccess policy

# Manually attach if missing
aws iam attach-role-policy \
    --role-name alerta-utec-airflow-ecs-execution-role \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
```

#### 3. EFS Mount Failed

**Error**: `ResourceInitializationError: failed to invoke EFS utils commands to set up EFS volumes`

**Solution**:
```bash
# Check EFS mount targets
aws efs describe-mount-targets \
    --file-system-id [EFS-ID] \
    --region us-east-1

# Verify security group allows port 2049 from ECS tasks
aws ec2 describe-security-groups \
    --group-ids [EFS-SG-ID] \
    --query 'SecurityGroups[0].IpPermissions'

# Check EFS access points
aws efs describe-access-points \
    --file-system-id [EFS-ID] \
    --region us-east-1
```

#### 4. Database Connection Failed (During Init)

**Error**: Container exits with code 1, logs show "Could not connect to database"

**Solution**:
```bash
# Check RDS instance status
aws rds describe-db-instances \
    --db-instance-identifier [DB-ID] \
    --query 'DBInstances[0].{Status:DBInstanceStatus,Endpoint:Endpoint.Address}'

# Verify security group allows port 5432
aws ec2 describe-security-groups \
    --group-ids [RDS-SG-ID]

# Test connection from another task/EC2
psql -h [RDS-ENDPOINT] -U airflow -d airflow

# Check SSM parameters are accessible
aws ssm get-parameter \
    --name /alerta-utec-airflow/db/password \
    --with-decryption \
    --region us-east-1
```

### Issue: Tasks Running but Not Healthy

**Symptoms**:
- Tasks in `RUNNING` status
- But ALB shows targets as "unhealthy"
- Can't access web UI

**Diagnosis**:

```bash
# Check task health
aws ecs describe-tasks \
    --cluster alerta-utec-airflow \
    --tasks $(aws ecs list-tasks --cluster alerta-utec-airflow --service-name alerta-utec-airflow-webserver --query 'taskArns[0]' --output text) \
    --query 'tasks[0].healthStatus'

# Check container logs
aws logs tail /ecs/alerta-utec-airflow \
    --follow \
    --filter-pattern "webserver" \
    --since 5m
```

**Common Causes & Solutions**:

#### 1. Health Check Failing

**Error**: ALB shows "Target.FailedHealthChecks"

**Solution**:
```bash
# Check if webserver is listening on port 8080
aws ecs execute-command \
    --cluster alerta-utec-airflow \
    --task [TASK-ID] \
    --container webserver \
    --command "/bin/bash" \
    --interactive

# Inside container:
curl -v http://localhost:8080/health
netstat -tlnp | grep 8080

# Check entrypoint.sh is running correctly
ps aux | grep airflow
```

#### 2. Database Migration Not Complete

**Error**: Webserver starts but crashes immediately

**Solution**:
```bash
# Check scheduler logs for migration
aws logs filter-log-events \
    --log-group-name /ecs/alerta-utec-airflow \
    --filter-pattern "migration" \
    --region us-east-1

# Manually run migration if needed (via exec)
airflow db migrate
```

## Database Connection Issues

### Issue: "FATAL: password authentication failed"

**Symptoms**:
- Tasks fail to start
- Logs show "password authentication failed for user airflow"

**Solution**:

```bash
# Verify SSM parameter has correct password
aws ssm get-parameter \
    --name /alerta-utec-airflow/db/password \
    --with-decryption \
    --query 'Parameter.Value' \
    --output text

# If password is wrong, update it:
# 1. Update SSM parameter
aws ssm put-parameter \
    --name /alerta-utec-airflow/db/password \
    --value "NEW_PASSWORD" \
    --type SecureString \
    --overwrite

# 2. Reset RDS master password
aws rds modify-db-instance \
    --db-instance-identifier [DB-ID] \
    --master-user-password "NEW_PASSWORD" \
    --apply-immediately

# 3. Force new deployment
aws ecs update-service \
    --cluster alerta-utec-airflow \
    --service alerta-utec-airflow-webserver \
    --force-new-deployment
```

### Issue: "could not connect to server: Connection timed out"

**Symptoms**:
- Database connection timeout
- No response from RDS

**Solution**:

```bash
# Check RDS is in correct VPC/subnets
aws rds describe-db-instances \
    --db-instance-identifier [DB-ID] \
    --query 'DBInstances[0].DBSubnetGroup.VpcId'

# Compare with ECS tasks VPC
aws ecs describe-tasks \
    --cluster alerta-utec-airflow \
    --tasks [TASK-ARN] \
    --query 'tasks[0].attachments[0].details[?name==`subnetId`].value'

# Check security group allows 5432 from ECS tasks
aws ec2 describe-security-group-rules \
    --filters Name=group-id,Values=[RDS-SG-ID] \
    --query 'SecurityGroupRules[?FromPort==`5432`]'
```

## EFS Mount Issues

### Issue: "failed to resolve fs-xxx.efs.region.amazonaws.com"

**Symptoms**:
- Tasks fail during mount
- DNS resolution error for EFS

**Solution**:

```bash
# Verify EFS file system exists
aws efs describe-file-systems \
    --file-system-id [EFS-ID] \
    --region us-east-1

# Check mount targets are available
aws efs describe-mount-targets \
    --file-system-id [EFS-ID] \
    --region us-east-1 \
    --query 'MountTargets[*].{SubnetId:SubnetId,IP:IpAddress,State:LifeCycleState}'

# Verify DNS resolution from VPC
# Launch temporary EC2 in same VPC and test:
nslookup fs-xxx.efs.us-east-1.amazonaws.com
```

### Issue: Permission Denied on EFS

**Symptoms**:
- Tasks running
- Logs show "Permission denied" when accessing /opt/airflow/dags

**Solution**:

```bash
# Check EFS access point configuration
aws efs describe-access-points \
    --file-system-id [EFS-ID] \
    --query 'AccessPoints[*].{Path:RootDirectory.Path,UID:PosixUser.Uid,GID:PosixUser.Gid,Permissions:RootDirectory.CreationInfo.Permissions}'

# Should show:
# - UID: 50000 (airflow user)
# - GID: 0
# - Permissions: 755

# If wrong, recreate access point or manually fix permissions via EC2:
# 1. Mount EFS on EC2
sudo mount -t nfs4 fs-xxx.efs.us-east-1.amazonaws.com:/ /mnt/efs
# 2. Fix permissions
sudo chown -R 50000:0 /mnt/efs/dags
sudo chmod -R 755 /mnt/efs/dags
```

## ALB & Health Check Issues

### Issue: ALB Target Group Has No Targets

**Symptoms**:
- ALB DNS resolves
- But returns 503 Service Unavailable
- Target group shows 0 healthy targets

**Diagnosis**:

```bash
# Get target group ARN
TG_ARN=$(aws cloudformation describe-stacks \
    --stack-name alerta-utec-airflow-master \
    --query 'Stacks[0].Outputs[?OutputKey==`WebserverTargetGroupArn`].OutputValue' \
    --output text)

# Check target health
aws elbv2 describe-target-health \
    --target-group-arn $TG_ARN \
    --query 'TargetHealthDescriptions[*].{Target:Target.Id,Port:Target.Port,State:TargetHealth.State,Reason:TargetHealth.Reason}'
```

**Common States**:

#### 1. State: "initial"

**Meaning**: Target is being registered, wait a few minutes

**Action**: Wait 2-3 minutes, then check again

#### 2. State: "unhealthy", Reason: "Target.FailedHealthChecks"

**Meaning**: Health check endpoint returning non-200 status

**Solution**:
```bash
# Check webserver logs
aws logs tail /ecs/alerta-utec-airflow \
    --filter-pattern "webserver" \
    --since 10m

# Manually test health endpoint
TASK_IP=$(aws ecs describe-tasks \
    --cluster alerta-utec-airflow \
    --tasks [TASK-ARN] \
    --query 'tasks[0].attachments[0].details[?name==`privateIPv4Address`].value' \
    --output text)

# From EC2 in same VPC:
curl -v http://$TASK_IP:8080/health
```

#### 3. State: "unused", Reason: "Target.NotInUse"

**Meaning**: Target group not attached to any service

**Solution**:
```bash
# Verify service configuration
aws ecs describe-services \
    --cluster alerta-utec-airflow \
    --services alerta-utec-airflow-webserver \
    --query 'services[0].loadBalancers'

# Should show target group ARN
# If missing, update service to attach target group
```

### Issue: "504 Gateway Timeout" When Accessing UI

**Symptoms**:
- ALB accessible
- Targets healthy
- But requests timeout after 60 seconds

**Solution**:

```bash
# Check ALB idle timeout (default 60s)
ALB_ARN=$(aws cloudformation describe-stacks \
    --stack-name alerta-utec-airflow-master \
    --query 'Stacks[0].Outputs[?OutputKey==`ALBArn`].OutputValue' \
    --output text)

aws elbv2 describe-load-balancer-attributes \
    --load-balancer-arn $ALB_ARN \
    --query 'Attributes[?Key==`idle_timeout.timeout_seconds`]'

# Increase if needed (Airflow UI can be slow on first load)
aws elbv2 modify-load-balancer-attributes \
    --load-balancer-arn $ALB_ARN \
    --attributes Key=idle_timeout.timeout_seconds,Value=300

# Also check webserver is responding
aws logs tail /ecs/alerta-utec-airflow --filter-pattern "webserver" --since 5m
```

## DAG Execution Issues

### Issue: DAGs Not Appearing in UI

**Symptoms**:
- Airflow UI accessible
- But no DAGs shown
- Or only example DAGs visible

**Diagnosis**:

```bash
# Check if DAGs are in EFS
# Via ECS Exec (if enabled):
aws ecs execute-command \
    --cluster alerta-utec-airflow \
    --task [SCHEDULER-TASK-ID] \
    --container scheduler \
    --command "/bin/bash" \
    --interactive

# Inside container:
ls -la /opt/airflow/dags/
cat /opt/airflow/dags/incident_classifier.py

# Check scheduler logs for parsing errors
aws logs filter-log-events \
    --log-group-name /ecs/alerta-utec-airflow \
    --filter-pattern "scheduler" \
    --start-time $(date -u -d '10 minutes ago' +%s)000
```

**Common Causes & Solutions**:

#### 1. DAGs Not Copied to EFS

**Solution**:
```bash
# Rebuild and redeploy image with DAGs
./scripts/build-and-push.sh
./scripts/update_dags.sh  # Select option 2
```

#### 2. DAG Parsing Errors

**Error**: Logs show "Failed to import: /opt/airflow/dags/xxx.py"

**Solution**:
```bash
# Check Python syntax locally
python -m py_compile dags/incident_classifier.py

# Check imports are available
docker run -it [ECR-URI]:latest python -c "from helpers.dynamodb_client import DynamoDBClient; print('OK')"

# Fix syntax errors and redeploy
```

#### 3. load_examples=True

**Symptoms**: Only example DAGs visible, not custom ones

**Solution**:
```bash
# Verify airflow.cfg has load_examples = False
grep load_examples config/airflow.cfg

# Should show: load_examples = False
# If not, fix and rebuild image
```

### Issue: DAG Runs Failing with "No module named 'helpers'"

**Symptoms**:
- DAGs visible in UI
- Can trigger manually
- But all tasks fail with ImportError

**Solution**:

```bash
# Verify helpers are copied into Docker image
docker run -it [ECR-URI]:latest ls -la /opt/airflow/helpers/

# Should show all 4 helper files
# If missing, check Dockerfile COPY commands and rebuild

# Also verify sys.path in DAGs
# Each DAG should have:
import sys
sys.path.append('/opt/airflow')
```

### Issue: Tasks Failing with AWS Permission Errors

**Error**: `botocore.exceptions.ClientError: An error occurred (AccessDenied)`

**Solution**:

```bash
# Check Task Role (not Execution Role) has correct permissions
aws iam get-role-policy \
    --role-name alerta-utec-airflow-airflow-task-role \
    --policy-name AirflowPermissions

# Should have permissions for:
# - DynamoDB: GetItem, PutItem, Scan, Query
# - SNS: Publish, CreateTopic
# - SES: SendEmail
# - CloudWatch: PutMetricData
# - S3: GetObject, PutObject

# If missing, update CloudFormation template and redeploy stack
```

## AWS Service Limits

### Issue: "LimitExceededException: Cannot create more than X"

**Common Limits**:

```bash
# Check Fargate task limit
aws service-quotas get-service-quota \
    --service-code fargate \
    --quota-code L-36C53AD6 \
    --region us-east-1

# Check VPC limit
aws service-quotas get-service-quota \
    --service-code vpc \
    --quota-code L-F678F1CE \
    --region us-east-1

# Check NAT Gateway limit
aws service-quotas get-service-quota \
    --service-code vpc \
    --quota-code L-FE5A380F \
    --region us-east-1
```

**Solution**:

```bash
# Request quota increase
aws service-quotas request-service-quota-increase \
    --service-code [SERVICE-CODE] \
    --quota-code [QUOTA-CODE] \
    --desired-value [NEW-VALUE]

# Or clean up unused resources
aws ec2 describe-vpcs --query 'Vpcs[*].VpcId'
# Delete unused VPCs
```

## Performance Issues

### Issue: Airflow UI Very Slow

**Symptoms**:
- Pages take 10+ seconds to load
- Task logs don't appear

**Solutions**:

#### 1. Increase Webserver Resources

Edit CloudFormation template `07-ecs-services.yaml`:
```yaml
WebserverTaskDefinition:
  Cpu: '512' → '1024'
  Memory: '1024' → '2048'
```

Redeploy stack.

#### 2. Enable Gunicorn Workers

Edit `config/airflow.cfg`:
```ini
[webserver]
workers = 2
worker_refresh_interval = 30
```

Rebuild image and redeploy.

#### 3. Optimize Database

```bash
# Check RDS CPU/Memory usage
aws cloudwatch get-metric-statistics \
    --namespace AWS/RDS \
    --metric-name CPUUtilization \
    --dimensions Name=DBInstanceIdentifier,Value=[DB-ID] \
    --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
    --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
    --period 300 \
    --statistics Average

# If consistently >80%, upgrade instance type
aws rds modify-db-instance \
    --db-instance-identifier [DB-ID] \
    --db-instance-class db.t3.small \
    --apply-immediately
```

### Issue: DAGs Taking Too Long to Run

**Symptoms**:
- Tasks queue for long time before running
- Execution takes much longer than expected

**Solutions**:

#### 1. Increase Parallelism

Edit `config/airflow.cfg`:
```ini
[core]
parallelism = 8 → 16
dag_concurrency = 8 → 16
max_active_runs_per_dag = 1 → 2
```

#### 2. Use Separate Worker Tasks

Currently using LocalExecutor. For better performance:

1. Switch to CeleryExecutor
2. Add worker tasks to ECS
3. Add Redis/SQS for task queue

(More complex, not needed for hackathon)

## Logging & Debugging

### Useful CloudWatch Log Queries

```bash
# All webserver logs from last hour
aws logs filter-log-events \
    --log-group-name /ecs/alerta-utec-airflow \
    --log-stream-name-prefix webserver \
    --start-time $(date -u -d '1 hour ago' +%s)000

# Errors only
aws logs filter-log-events \
    --log-group-name /ecs/alerta-utec-airflow \
    --filter-pattern "ERROR" \
    --start-time $(date -u -d '1 hour ago' +%s)000

# Specific DAG execution
aws logs filter-log-events \
    --log-group-name /ecs/alerta-utec-airflow \
    --filter-pattern "incident_classifier" \
    --start-time $(date -u -d '30 minutes ago' +%s)000

# Database connection attempts
aws logs filter-log-events \
    --log-group-name /ecs/alerta-utec-airflow \
    --filter-pattern "postgres" \
    --start-time $(date -u -d '15 minutes ago' +%s)000
```

### Enable ECS Exec for Interactive Debugging

```bash
# Enable ECS Exec on service
aws ecs update-service \
    --cluster alerta-utec-airflow \
    --service alerta-utec-airflow-webserver \
    --enable-execute-command \
    --region us-east-1

# Get running task ID
TASK_ID=$(aws ecs list-tasks \
    --cluster alerta-utec-airflow \
    --service-name alerta-utec-airflow-webserver \
    --query 'taskArns[0]' \
    --output text | rev | cut -d'/' -f1 | rev)

# Exec into container
aws ecs execute-command \
    --cluster alerta-utec-airflow \
    --task $TASK_ID \
    --container webserver \
    --command "/bin/bash" \
    --interactive

# Inside container, can run:
airflow dags list
airflow dags test incident_classifier 2025-01-01
python -c "from helpers.dynamodb_client import DynamoDBClient; print(DynamoDBClient().get_all_incidents())"
```

### Check Airflow Config

```bash
# View rendered config
aws ecs execute-command \
    --cluster alerta-utec-airflow \
    --task [TASK-ID] \
    --container webserver \
    --command "airflow config list" \
    --interactive

# Check specific value
aws ecs execute-command \
    --cluster alerta-utec-airflow \
    --task [TASK-ID] \
    --container webserver \
    --command "airflow config get-value core sql_alchemy_conn" \
    --interactive
```

## Common Error Messages

### "ResourceInitializationError: unable to pull secrets or registry auth"

**Cause**: Task Execution Role can't access ECR or SSM

**Solution**: Check IAM role permissions, verify SSM parameters exist

### "CannotStartContainerError: Error response from daemon: OCI runtime create failed"

**Cause**: Container configuration invalid or resource limits too low

**Solution**: Check CPU/Memory settings in task definition, verify entrypoint script syntax

### "Task failed to start due to: ResourceInitializationError"

**Cause**: Generic init error, check CloudWatch Logs for specific issue

**Solution**: Review logs with:
```bash
aws logs tail /ecs/alerta-utec-airflow --since 10m
```

### "Scheduler heartbeat got sent to the past"

**Cause**: Clock skew or database connection issues

**Solution**: Restart scheduler task, check RDS connectivity

### "Invalid connection string"

**Cause**: AIRFLOW__DATABASE__SQL_ALCHEMY_CONN malformed

**Solution**: Verify format: `postgresql+psycopg2://user:pass@host:port/dbname`

## Emergency Procedures

### Procedure 1: Complete Service Restart

```bash
# Stop all tasks
aws ecs update-service --cluster alerta-utec-airflow --service alerta-utec-airflow-webserver --desired-count 0
aws ecs update-service --cluster alerta-utec-airflow --service alerta-utec-airflow-scheduler --desired-count 0

# Wait 30 seconds
sleep 30

# Start services
aws ecs update-service --cluster alerta-utec-airflow --service alerta-utec-airflow-webserver --desired-count 1
aws ecs update-service --cluster alerta-utec-airflow --service alerta-utec-airflow-scheduler --desired-count 1
```

### Procedure 2: Force Database Reset

```bash
# WARNING: Loses all Airflow metadata (task history, connections, etc.)

# Exec into scheduler
aws ecs execute-command --cluster alerta-utec-airflow --task [TASK-ID] --container scheduler --command "/bin/bash" --interactive

# Inside container:
airflow db reset --yes
airflow db migrate
airflow users create --username admin --password [PASSWORD] --firstname Admin --lastname User --role Admin --email admin@utec.edu.pe
exit

# Restart services
aws ecs update-service --cluster alerta-utec-airflow --service alerta-utec-airflow-scheduler --force-new-deployment
```

### Procedure 3: Complete Stack Rebuild

```bash
# Last resort if everything is broken

# 1. Cleanup
./scripts/cleanup.sh

# 2. Wait for complete deletion
aws cloudformation wait stack-delete-complete --stack-name alerta-utec-airflow-master

# 3. Rebuild image
./scripts/build-and-push.sh

# 4. Redeploy
./scripts/deploy.sh
```

## Getting Help

### Information to Collect Before Asking

1. **CloudFormation Stack Status**:
   ```bash
   aws cloudformation describe-stacks --stack-name alerta-utec-airflow-master --query 'Stacks[0].StackStatus'
   ```

2. **ECS Service Status**:
   ```bash
   aws ecs describe-services --cluster alerta-utec-airflow --services alerta-utec-airflow-webserver alerta-utec-airflow-scheduler
   ```

3. **Recent Logs** (last 100 lines):
   ```bash
   aws logs tail /ecs/alerta-utec-airflow --since 10m > logs.txt
   ```

4. **Task Failures**:
   ```bash
   aws ecs list-tasks --cluster alerta-utec-airflow --desired-status STOPPED | head -5
   ```

5. **Security Group Rules**:
   ```bash
   aws ec2 describe-security-groups --group-ids [SG-IDs]
   ```

### Contact Points

- **Team Lead**: [nombre]
- **AWS Support**: [si tienen Business Support]
- **Instructor**: [nombre del profesor]

---

**Última actualización**: 2025  
**Mantenido por**: Equipo AlertaUTEC
