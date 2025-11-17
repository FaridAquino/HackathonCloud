# Deployment Guide - AlertaUTEC Airflow

Guía paso a paso completa para desplegar Apache Airflow en Amazon ECS/Fargate.

## Índice

1. [Prerequisitos](#prerequisitos)
2. [Preparación del Entorno](#preparación-del-entorno)
3. [Build Docker Image](#build-docker-image)
4. [Deploy Infrastructure](#deploy-infrastructure)
5. [Verificación](#verificación)
6. [Post-Deployment](#post-deployment)
7. [Actualización de DAGs](#actualización-de-dags)
8. [Rollback](#rollback)
9. [Cleanup](#cleanup)

## Prerequisitos

### Software Required

- **AWS CLI** v2.x
  ```bash
  aws --version
  # aws-cli/2.x.x Python/3.x
  ```

- **Docker** 20.x+
  ```bash
  docker --version
  # Docker version 20.10.x
  ```

- **Bash** (Linux/Mac) or **Git Bash** (Windows)

- **jq** (JSON processor)
  ```bash
  # Ubuntu/Debian
  sudo apt-get install jq
  
  # Mac
  brew install jq
  
  # Windows (via chocolatey)
  choco install jq
  ```

### AWS Account Requirements

#### Permissions Needed

El usuario/rol de IAM debe tener permisos para:

```yaml
Services:
  - CloudFormation: FULL (create/update/delete stacks)
  - ECS: FULL (create clusters, services, task definitions)
  - ECR: FULL (create repositories, push images)
  - EFS: FULL (create file systems, mount targets)
  - RDS: FULL (create databases)
  - VPC: FULL (create VPCs, subnets, NAT, IGW)
  - EC2: FULL (for VPC resources, security groups)
  - IAM: LIMITED (create roles, attach policies)
  - S3: FULL (create buckets)
  - SSM: FULL (create parameters)
  - CloudWatch: FULL (create log groups, put metrics)
```

**Nota**: AWS Academy Learner Lab típicamente tiene estos permisos.

#### Service Quotas

Verificar límites antes de desplegar:

```bash
# ECS tasks
aws service-quotas get-service-quota \
    --service-code ecs \
    --quota-code L-3032A538 \
    --region us-east-1

# Fargate tasks
aws service-quotas get-service-quota \
    --service-code fargate \
    --quota-code L-36C53AD6 \
    --region us-east-1

# VPC
aws service-quotas get-service-quota \
    --service-code vpc \
    --quota-code L-F678F1CE \
    --region us-east-1
```

Mínimos requeridos:
- ✅ Fargate tasks per region: 2
- ✅ VPCs: 1
- ✅ NAT Gateways: 1
- ✅ Elastic IPs: 1

### DynamoDB Table

El equipo debe haber creado la tabla `Incidents` con:

```yaml
TableName: Incidents
PartitionKey: incidentId (String)
Attributes (opcionales):
  - classification (String)
  - priority (String)
  - status (String)
  - description (String)
  - location (String)
  - createdAt (String - ISO 8601)
  - notificationSent (Boolean)
```

Verificar existencia:
```bash
aws dynamodb describe-table --table-name Incidents --region us-east-1
```

## Preparación del Entorno

### Step 1: Clonar/Descargar Proyecto

```bash
cd ~/Downloads/UTEC_2025_2/Cloud_Computing/HACK/
# El proyecto ya está en: parte_apache_airflow/
```

### Step 2: Configurar AWS CLI

```bash
# Configurar credenciales
aws configure

# Verificar configuración
aws sts get-caller-identity
```

Output esperado:
```json
{
    "UserId": "AIDAI...",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/your-user"
}
```

### Step 3: Configurar Variables de Entorno

```bash
cd parte_apache_airflow

# Copiar template
cp .env.example .env

# Editar .env
nano .env  # o vim, code, etc.
```

**Archivo `.env` completo**:

```bash
# AWS Configuration
AWS_DEFAULT_REGION=us-east-1
AWS_ACCOUNT_ID=123456789012  # ← CAMBIAR al tuyo

# Project Configuration
PROJECT_NAME=alerta-utec-airflow
ECR_REPOSITORY=alerta-utec-airflow
ECS_CLUSTER_NAME=alerta-utec-airflow

# DynamoDB
DYNAMODB_TABLE=Incidents

# Airflow Admin (usado para login web)
AIRFLOW_ADMIN_USERNAME=admin
AIRFLOW_ADMIN_PASSWORD=changeme123  # ← CAMBIAR
AIRFLOW_ADMIN_EMAIL=admin@utec.edu.pe

# Database Connection (se configurará durante deploy)
AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql+psycopg2://...

# Airflow Security (se generará automáticamente durante deploy)
AIRFLOW__WEBSERVER__SECRET_KEY=changeme1234567890
```

**Obtener AWS Account ID**:
```bash
aws sts get-caller-identity --query Account --output text
```

### Step 4: Dar Permisos de Ejecución a Scripts

```bash
chmod +x scripts/*.sh
```

### Step 5: Validar Estructura del Proyecto

```bash
tree -L 2
```

Output esperado:
```
.
├── README.md
├── .env
├── .env.example
├── cloudformation/
│   ├── 01-network.yaml
│   ├── 02-storage.yaml
│   ├── 03-ecr.yaml
│   ├── 04-efs.yaml
│   ├── 05-ecs-cluster.yaml
│   ├── 06-alb.yaml
│   ├── 07-ecs-services.yaml
│   └── master-stack.yaml
├── config/
│   ├── airflow.cfg
│   └── requirements.txt
├── dags/
│   ├── incident_classifier.py
│   ├── notification_dispatcher.py
│   ├── reminder_sender.py
│   ├── report_generator.py
│   └── metrics_analyzer.py
├── docker/
│   ├── Dockerfile
│   ├── entrypoint.sh
│   └── .dockerignore
├── helpers/
│   ├── dynamodb_client.py
│   ├── sns_notifier.py
│   ├── incident_classifier.py
│   └── metrics_calculator.py
├── scripts/
│   ├── build-and-push.sh
│   ├── deploy.sh
│   ├── cleanup.sh
│   └── update_dags.sh
└── docs/
    ├── ARCHITECTURE.md
    ├── DEPLOYMENT_GUIDE.md
    └── TROUBLESHOOTING.md
```

## Build Docker Image

### Step 1: Crear ECR Repository (si no existe)

```bash
aws ecr create-repository \
    --repository-name alerta-utec-airflow \
    --region us-east-1 \
    --image-scanning-configuration scanOnPush=false
```

Si ya existe, ignorar el error.

### Step 2: Build y Push

```bash
./scripts/build-and-push.sh
```

**Proceso**:
1. ✅ Login to ECR
2. ✅ Build Docker image
3. ✅ Tag image as `latest`
4. ✅ Push to ECR

**Output esperado**:
```
========================================
AlertaUTEC Airflow - Build and Push
========================================

Configuration:
  Region: us-east-1
  Account ID: 123456789012
  Repository: alerta-utec-airflow
  Image Tag: latest
  ECR URI: 123456789012.dkr.ecr.us-east-1.amazonaws.com/alerta-utec-airflow

[1/4] Logging in to Amazon ECR...
Login Succeeded

[2/4] Building Docker image...
Step 1/15 : FROM apache/airflow:2.8.0-python3.11
...
✓ Docker image built successfully

[3/4] Tagging image...
✓ Image tagged

[4/4] Pushing image to ECR...
The push refers to repository [123456789012.dkr.ecr.us-east-1.amazonaws.com/alerta-utec-airflow]
...
✓ Image pushed successfully!

Image URI: 123456789012.dkr.ecr.us-east-1.amazonaws.com/alerta-utec-airflow:latest
```

**Tiempo estimado**: 5-10 minutos (depende de conexión a internet)

### Step 3 (Opcional): Verificar Imagen en ECR

```bash
aws ecr describe-images \
    --repository-name alerta-utec-airflow \
    --region us-east-1
```

## Deploy Infrastructure

### Step 1: Ejecutar Deploy Script

```bash
./scripts/deploy.sh
```

### Step 2: Ingresar Información Requerida

El script pedirá:

```
Enter RDS Database Password (min 8 chars): ********
Enter Airflow Admin Password (min 8 chars): ********
Enter DynamoDB Table Name (default: Incidents): [ENTER]
```

**Recomendaciones**:
- Database Password: Contraseña fuerte, mínimo 8 caracteres alfanuméricos
- Airflow Admin Password: Contraseña que usarás para login web
- DynamoDB Table: Dejar default `Incidents`

### Step 3: Confirmar Configuración

El script mostrará:
```
Configuration:
  Region: us-east-1
  Project: alerta-utec-airflow
  Stack Name: alerta-utec-airflow-master
  Templates Bucket: alerta-utec-airflow-cfn-templates-1234567890
  Image Tag: latest
  DynamoDB Table: Incidents
```

### Step 4: Monitorear Deployment

**Proceso**:
1. ✅ Create S3 bucket for templates (1 min)
2. ✅ Upload CloudFormation templates (30 sec)
3. ✅ Validate templates (30 sec)
4. ✅ Deploy CloudFormation stack (15-20 min)
5. ✅ Retrieve outputs (30 sec)

**Output en tiempo real**:
```
[1/5] Creating S3 bucket for CloudFormation templates...
make_bucket: alerta-utec-airflow-cfn-templates-1234567890
✓ Bucket created

[2/5] Uploading CloudFormation templates...
upload: cloudformation/01-network.yaml to s3://...
...
✓ Templates uploaded

[3/5] Validating CloudFormation templates...
✓ Templates validated

[4/5] Deploying CloudFormation stack...
This may take 15-20 minutes...

Waiting for stack creation...
```

**Tiempo total**: ~20 minutos

### Step 5: Deployment Success

```
========================================
✓ Deployment Complete!
========================================

Access Information:
  Airflow Web UI: http://alerta-utec-airflow-alb-1234567890.us-east-1.elb.amazonaws.com
  Username: admin
  Password: [the password you entered]

Infrastructure Details:
  ECR Repository: 123456789012.dkr.ecr.us-east-1.amazonaws.com/alerta-utec-airflow
  ECS Cluster: alerta-utec-airflow
  Region: us-east-1

Next Steps:
  1. Wait 2-3 minutes for services to fully start
  2. Access Airflow UI at: http://...
  3. Enable DAGs in the UI
  4. Monitor CloudWatch Logs: /ecs/alerta-utec-airflow

Important:
  - Database password and admin password are stored securely
  - To update DAGs: run ./scripts/update_dags.sh
  - To cleanup all resources: run ./scripts/cleanup.sh

Deployment info saved to: deployment-info.txt
```

## Verificación

### Step 1: Verificar Stack en CloudFormation

```bash
aws cloudformation describe-stacks \
    --stack-name alerta-utec-airflow-master \
    --region us-east-1 \
    --query 'Stacks[0].StackStatus'
```

Output esperado: `"CREATE_COMPLETE"`

### Step 2: Verificar ECS Services

```bash
# Webserver service
aws ecs describe-services \
    --cluster alerta-utec-airflow \
    --services alerta-utec-airflow-webserver \
    --region us-east-1 \
    --query 'services[0].{Status:status,Running:runningCount,Desired:desiredCount}'

# Scheduler service
aws ecs describe-services \
    --cluster alerta-utec-airflow \
    --services alerta-utec-airflow-scheduler \
    --region us-east-1 \
    --query 'services[0].{Status:status,Running:runningCount,Desired:desiredCount}'
```

Output esperado:
```json
{
    "Status": "ACTIVE",
    "Running": 1,
    "Desired": 1
}
```

### Step 3: Verificar Target Health en ALB

```bash
# Get target group ARN
TG_ARN=$(aws cloudformation describe-stacks \
    --stack-name alerta-utec-airflow-master \
    --region us-east-1 \
    --query 'Stacks[0].Outputs[?OutputKey==`WebserverTargetGroupArn`].OutputValue' \
    --output text)

# Check health
aws elbv2 describe-target-health \
    --target-group-arn $TG_ARN \
    --region us-east-1
```

Output esperado:
```json
{
    "TargetHealthDescriptions": [
        {
            "Target": {
                "Id": "10.0.10.x",
                "Port": 8080
            },
            "HealthCheckPort": "8080",
            "TargetHealth": {
                "State": "healthy"
            }
        }
    ]
}
```

### Step 4: Acceder a Airflow Web UI

1. Obtener URL del ALB:
   ```bash
   aws cloudformation describe-stacks \
       --stack-name alerta-utec-airflow-master \
       --region us-east-1 \
       --query 'Stacks[0].Outputs[?OutputKey==`AirflowWebURL`].OutputValue' \
       --output text
   ```

2. Abrir en navegador: `http://[ALB-DNS-NAME]`

3. Login:
   - Username: `admin`
   - Password: [la que ingresaste durante deploy]

4. Verificar DAGs visibles:
   - ✅ incident_classifier
   - ✅ notification_dispatcher
   - ✅ reminder_sender
   - ✅ report_generator
   - ✅ metrics_analyzer

### Step 5: Verificar CloudWatch Logs

```bash
# Ver logs recientes
aws logs tail /ecs/alerta-utec-airflow --follow --region us-east-1
```

Output esperado incluye:
```
2025-01-XX 12:34:56 webserver Starting Airflow webserver...
2025-01-XX 12:34:57 webserver [INFO] Database migration complete
2025-01-XX 12:34:58 webserver [INFO] Admin user created
2025-01-XX 12:35:00 webserver [INFO] Webserver listening on port 8080
2025-01-XX 12:35:01 scheduler Starting Airflow scheduler...
2025-01-XX 12:35:05 scheduler [INFO] Loading DAGs from /opt/airflow/dags
2025-01-XX 12:35:10 scheduler [INFO] 5 DAGs loaded successfully
```

## Post-Deployment

### Step 1: Habilitar DAGs

Por defecto, los DAGs están pausados. Habilitarlos desde UI:

1. Click en toggle a la izquierda de cada DAG
2. Estado debe cambiar a **green/running**

O via CLI:
```bash
# Habilitar todos los DAGs
aws ecs execute-command \
    --cluster alerta-utec-airflow \
    --task [TASK-ID] \
    --container webserver \
    --command "airflow dags unpause incident_classifier" \
    --interactive

# Repetir para cada DAG
```

### Step 2: Trigger Manual Test

```bash
# Trigger incident_classifier manualmente
airflow dags trigger incident_classifier
```

O desde UI: Click en "Play" button → "Trigger DAG"

### Step 3: Verificar Ejecución

En Airflow UI:
1. Click en DAG name
2. Ver "Graph" view
3. Verificar task success (verde)

### Step 4: Configurar SNS Topics (si es necesario)

Los DAGs crearán automáticamente los topics SNS, pero puedes suscribirte:

```bash
# High priority topic
aws sns subscribe \
    --topic-arn arn:aws:sns:us-east-1:123456789012:AlertaUTEC-HighPriority \
    --protocol email \
    --notification-endpoint your-email@utec.edu.pe

# Confirmar suscripción en email recibido
```

### Step 5: Verificar SES (para reports)

```bash
# Verificar email address
aws ses verify-email-identity \
    --email-address admin@utec.edu.pe \
    --region us-east-1

# Revisar inbox para confirmar
```

**Nota**: AWS Academy puede tener restricciones de SES. Verificar con instructor.

## Actualización de DAGs

### Método 1: Rebuild Docker Image (Recomendado)

```bash
./scripts/update_dags.sh
# Seleccionar opción 2
```

Este método:
1. Build nueva imagen con DAGs actualizados
2. Push a ECR con nuevo tag timestamped
3. Force new deployment en servicios ECS
4. Espera a que servicios estén estables

**Tiempo**: 10-15 minutos

### Método 2: Copiar a EFS (Más Rápido)

Requiere acceso temporal a EFS:

```bash
# Opción A: EC2 temporal
1. Launch t3.micro en misma VPC
2. Attach security group que permite 2049 to EFS
3. Mount EFS:
   sudo mount -t nfs4 fs-xxx.efs.us-east-1.amazonaws.com:/ /mnt/efs
4. Copiar DAGs:
   sudo cp -r dags/* /mnt/efs/dags/
5. Wait 60 seconds (Airflow auto-refresh)
6. Terminate EC2

# Opción B: ECS Exec (requiere enable ECS exec en service)
aws ecs update-service \
    --cluster alerta-utec-airflow \
    --service alerta-utec-airflow-scheduler \
    --enable-execute-command

# Luego exec into task y copiar files
```

**Tiempo**: 2-3 minutos

## Rollback

### Scenario 1: Rollback a Imagen Anterior

```bash
# Listar imágenes en ECR
aws ecr describe-images \
    --repository-name alerta-utec-airflow \
    --region us-east-1 \
    --query 'sort_by(imageDetails,& imagePushedAt)[-5:].[imageTags[0],imagePushedAt]'

# Deploy versión anterior
./scripts/deploy.sh v20250115-120000  # ← tag anterior
```

### Scenario 2: Rollback Stack Completo

```bash
# Delete current stack
./scripts/cleanup.sh

# Redeploy desde cero
./scripts/deploy.sh
```

**Advertencia**: Esto elimina la base de datos con todo el historial de ejecuciones.

## Cleanup

### Full Cleanup (Eliminar Todo)

```bash
./scripts/cleanup.sh
```

Output esperado:
```
========================================
AlertaUTEC Airflow - Cleanup
========================================

WARNING: This will delete ALL resources!
  - CloudFormation stacks
  - RDS Database (all data will be lost)
  - S3 Buckets (logs will be deleted)
  - ECR Images
  - EFS File System (DAGs will be deleted)
  - ECS Services and Tasks

Are you sure you want to continue? (yes/no): yes

[1/5] Getting resource information...
[2/5] Emptying S3 bucket...
✓ S3 bucket emptied
[3/5] Deleting ECR images...
✓ ECR images deleted
[4/5] Deleting CloudFormation stack...
This may take 10-15 minutes...
Waiting for stack deletion...
✓ Stack deleted
[5/5] Cleaning up templates bucket...
✓ Templates buckets deleted

========================================
✓ Cleanup Complete!
========================================

Note:
  - DynamoDB table was NOT deleted (managed by team)
  - SNS topics may still exist (low cost)
  - CloudWatch log groups may remain (will auto-expire)

All infrastructure has been removed
```

**Tiempo**: 10-15 minutos

### Partial Cleanup (Mantener Datos)

Si solo quieres apagar sin eliminar:

```bash
# Stop ECS services (no más costo de compute)
aws ecs update-service \
    --cluster alerta-utec-airflow \
    --service alerta-utec-airflow-webserver \
    --desired-count 0

aws ecs update-service \
    --cluster alerta-utec-airflow \
    --service alerta-utec-airflow-scheduler \
    --desired-count 0

# RDS seguirá cobrando (~$2.86/semana)
# NAT Gateway seguirá cobrando (~$7.56/semana)
```

Para restart:
```bash
aws ecs update-service \
    --cluster alerta-utec-airflow \
    --service alerta-utec-airflow-webserver \
    --desired-count 1

aws ecs update-service \
    --cluster alerta-utec-airflow \
    --service alerta-utec-airflow-scheduler \
    --desired-count 1
```

## Troubleshooting Common Issues

Ver [TROUBLESHOOTING.md](TROUBLESHOOTING.md) para detalles completos.

### Issue: Stack Creation Failed

```bash
# Ver error
aws cloudformation describe-stack-events \
    --stack-name alerta-utec-airflow-master \
    --region us-east-1 \
    --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`]'

# Delete failed stack y retry
aws cloudformation delete-stack --stack-name alerta-utec-airflow-master
./scripts/deploy.sh
```

### Issue: Tasks Not Starting

```bash
# Check stopped tasks
aws ecs describe-tasks \
    --cluster alerta-utec-airflow \
    --tasks $(aws ecs list-tasks --cluster alerta-utec-airflow --desired-status STOPPED --query 'taskArns[0]' --output text) \
    --query 'tasks[0].stoppedReason'
```

### Issue: Cannot Access Web UI

```bash
# Check ALB health
aws elbv2 describe-target-health --target-group-arn [ARN]

# Check logs
aws logs tail /ecs/alerta-utec-airflow --since 10m
```

## Next Steps

1. ✅ Deployment completado
2. ✅ Airflow accesible
3. ✅ DAGs habilitados

Ahora:
- Monitorear ejecuciones en Airflow UI
- Revisar CloudWatch Logs regularmente
- Probar cada DAG manualmente
- Configurar alertas si es necesario
- Documentar cualquier issue encontrado

---

**¿Necesitas ayuda?** Ver [TROUBLESHOOTING.md](TROUBLESHOOTING.md) o contactar al equipo.
