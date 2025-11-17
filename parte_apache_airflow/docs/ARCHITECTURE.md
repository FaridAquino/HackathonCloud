# Architecture Guide - AlertaUTEC Airflow

Esta guía detalla la arquitectura completa del componente Apache Airflow desplegado en Amazon ECS/Fargate.

## Índice

1. [Resumen Arquitectónico](#resumen-arquitectónico)
2. [Componentes AWS](#componentes-aws)
3. [Networking](#networking)
4. [Compute Layer](#compute-layer)
5. [Storage Layer](#storage-layer)
6. [Security](#security)
7. [Monitoring & Logging](#monitoring--logging)
8. [Data Flow](#data-flow)
9. [Escalabilidad](#escalabilidad)
10. [Cost Breakdown](#cost-breakdown)

## Resumen Arquitectónico

AlertaUTEC Airflow utiliza una arquitectura **serverless** basada en ECS Fargate, eliminando la necesidad de gestionar servidores EC2.

### Principios de Diseño

- **Serverless First**: ECS Fargate para compute sin gestión de instancias
- **High Availability**: Multi-AZ deployment para componentes críticos
- **Cost Optimized**: Configuraciones mínimas viables para hackathon
- **Security First**: Private subnets, security groups restrictivos, IAM roles específicos
- **Observability**: CloudWatch Logs, métricas custom, alarmas

## Componentes AWS

### 1. Amazon VPC (Virtual Private Cloud)

**CIDR**: `10.0.0.0/16`

```
VPC (10.0.0.0/16)
├── Public Subnets (para ALB y NAT)
│   ├── PublicSubnet1: 10.0.1.0/24 (us-east-1a)
│   └── PublicSubnet2: 10.0.2.0/24 (us-east-1b)
└── Private Subnets (para ECS tasks y RDS)
    ├── PrivateSubnet1: 10.0.10.0/24 (us-east-1a)
    └── PrivateSubnet2: 10.0.11.0/24 (us-east-1b)
```

**Routing**:
- Public subnets → Internet Gateway
- Private subnets → NAT Gateway (1 compartido para ahorrar costos)

### 2. Amazon ECS (Elastic Container Service)

**Cluster**: `alerta-utec-airflow`

**Capacity Providers**:
- `FARGATE`: Para webserver (siempre disponible)
- `FARGATE_SPOT`: Para scheduler/worker (hasta 70% descuento)

**Task Definitions**:

#### Webserver Task
```yaml
Family: alerta-utec-airflow-webserver
CPU: 512 (0.5 vCPU)
Memory: 1024 MB
Container:
  - Name: webserver
    Image: ECR_URI:TAG
    Port: 8080
    Command: ["webserver"]
    Volumes:
      - EFS /dags → /opt/airflow/dags
      - EFS /logs → /opt/airflow/logs
```

#### Scheduler Task
```yaml
Family: alerta-utec-airflow-scheduler
CPU: 512 (0.5 vCPU)
Memory: 1024 MB
Container:
  - Name: scheduler
    Command: ["scheduler"]
    Volumes:
      - EFS /dags → /opt/airflow/dags
      - EFS /logs → /opt/airflow/logs
```

### 3. Amazon ECR (Elastic Container Registry)

**Repository**: `alerta-utec-airflow`

**Lifecycle Policy**: Mantiene últimas 5 imágenes, elimina antiguas

**Image Tags**:
- `latest`: Siempre apunta a la última versión
- `vYYYYMMDD-HHMMSS`: Tags timestamped para rollback

### 4. Amazon EFS (Elastic File System)

**Mount Targets**: Uno en cada AZ (us-east-1a, us-east-1b)

**Access Points**:
1. **DAGs Access Point**
   - Path: `/dags`
   - UID: 50000 (airflow user)
   - GID: 0
   - Permissions: 755

2. **Logs Access Point**
   - Path: `/logs`
   - UID: 50000
   - GID: 0
   - Permissions: 755

**Performance Mode**: General Purpose  
**Throughput Mode**: Bursting  
**Lifecycle Policy**: Transition to IA after 7 days

### 5. Amazon RDS (Relational Database Service)

**Engine**: PostgreSQL 15.5

**Instance**: `db.t3.micro`
- vCPU: 1
- RAM: 1 GB
- Storage: 20 GB gp2

**Configuration**:
```yaml
MultiAZ: false                  # Single-AZ para ahorrar costos
BackupRetentionPeriod: 0        # Sin backups para hackathon
StorageEncrypted: false         # Sin encryption para ahorrar
PubliclyAccessible: false       # Solo acceso privado
DeletionProtection: false       # Facilitar cleanup
```

**Database Name**: `airflow`  
**Port**: 5432

### 6. Application Load Balancer (ALB)

**Scheme**: internet-facing

**Listeners**:
- HTTP:80 → Forward to Target Group

**Target Group**:
- Protocol: HTTP
- Port: 8080
- Target Type: IP
- Health Check: `/health`
  - Interval: 30s
  - Timeout: 10s
  - Healthy threshold: 2
  - Unhealthy threshold: 3
- Stickiness: Enabled (86400s)

### 7. Amazon S3

**Bucket**: `alerta-utec-airflow-logs-{timestamp}`

**Purpose**: Almacenar logs históricos y backups

**Lifecycle Policy**:
- Eliminar logs después de 7 días
- Abortar uploads incompletos después de 1 día

### 8. AWS Systems Manager Parameter Store

**Parameters**:
```
/alerta-utec-airflow/db/host
/alerta-utec-airflow/db/port
/alerta-utec-airflow/db/name
/alerta-utec-airflow/db/username
/alerta-utec-airflow/db/password (SecureString)
/alerta-utec-airflow/webserver/secret-key (SecureString)
```

## Networking

### Security Groups

#### ALB Security Group
```yaml
Ingress:
  - Port: 80
    Source: 0.0.0.0/0
    Description: HTTP from anywhere
Egress:
  - Port: 8080
    Destination: ECS Tasks SG
```

#### ECS Tasks Security Group
```yaml
Ingress:
  - Port: 8080
    Source: ALB SG
    Description: HTTP from ALB
Egress:
  - Port: 5432
    Destination: RDS SG
  - Port: 2049
    Destination: EFS SG
  - Port: 443
    Destination: 0.0.0.0/0 (AWS APIs)
```

#### RDS Security Group
```yaml
Ingress:
  - Port: 5432
    Source: ECS Tasks SG
    Description: PostgreSQL from ECS
```

#### EFS Security Group
```yaml
Ingress:
  - Port: 2049
    Source: ECS Tasks SG
    Description: NFS from ECS
```

### Network Flow

```
Internet
    ↓
Internet Gateway
    ↓
ALB (Public Subnets)
    ↓
ECS Tasks (Private Subnets)
    ↓
├─→ RDS (Private Subnets)
├─→ EFS (Private Subnets)
└─→ NAT Gateway → Internet (AWS APIs, DynamoDB, SNS, etc.)
```

## Compute Layer

### Container Configuration

**Base Image**: `apache/airflow:2.8.0-python3.11`

**Installed Dependencies**:
- boto3==1.34.0
- psycopg2-binary==2.9.9
- apache-airflow-providers-amazon==8.15.0

**Entrypoint Logic**:
1. Wait for RDS to be ready (`pg_isready`)
2. Initialize database (first run only)
3. Create admin user
4. Start appropriate service (webserver/scheduler)

**Environment Variables**:
```bash
AIRFLOW_HOME=/opt/airflow
AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql+psycopg2://...
AIRFLOW__WEBSERVER__SECRET_KEY=...
AWS_DEFAULT_REGION=us-east-1
DYNAMODB_TABLE=Incidents
```

### Executor

**LocalExecutor**: Ejecuta tareas en el mismo contenedor que el scheduler

**Razón**: Simplifica arquitectura, suficiente para 5 DAGs con baja frecuencia

**Alternativa**: CeleryExecutor con workers separados (más complejo y costoso)

## Storage Layer

### Airflow Metadata (RDS)

**Tablas principales**:
- `dag`: Definiciones de DAGs
- `dag_run`: Ejecuciones de DAGs
- `task_instance`: Estado de tareas individuales
- `log`: Logs de aplicación
- `connection`: Conexiones configuradas
- `variable`: Variables de Airflow

### DAGs Storage (EFS)

**Path**: `/opt/airflow/dags`

**Sincronización**: Automática cada 60 segundos (Airflow DAG processor)

**Update Strategy**:
1. Copy files to EFS via temporary EC2/ECS task
2. Rebuild Docker image with new DAGs

### Logs Storage

**Primary**: EFS (`/opt/airflow/logs`)  
**Backup**: CloudWatch Logs (7 días)

## Security

### IAM Roles

#### Task Execution Role
Permisos para:
- ECR: Pull images
- CloudWatch Logs: Write logs
- SSM: Read parameters

#### Task Role
Permisos para:
- DynamoDB: Read/Write incidents table
- SNS: Publish notifications
- SES: Send emails
- S3: Read/Write logs bucket
- CloudWatch: Put metric data
- SSM: Read parameters

### Secrets Management

- **Database Password**: SSM Parameter Store (SecureString)
- **Webserver Secret Key**: SSM Parameter Store (SecureString)
- **Airflow Admin Password**: CloudFormation parameter (no echo)

**NO SE GUARDAN EN**:
- ❌ Código fuente
- ❌ Docker image
- ❌ Variables de entorno plaintext

### Network Security

- ✅ ECS Tasks en private subnets
- ✅ RDS en private subnets
- ✅ EFS mount targets en private subnets
- ✅ Solo ALB es público
- ✅ Security Groups restrictivos (least privilege)

## Monitoring & Logging

### CloudWatch Logs

**Log Groups**:
- `/ecs/alerta-utec-airflow`: Todos los containers
  - `webserver` stream
  - `scheduler` stream

**Retention**: 7 días

### CloudWatch Metrics

**Custom Namespace**: `AlertaUTEC/Incidents`

**Metrics Published**:
- `TotalIncidents`: Count
- `PendingIncidents`: Count
- `ResolutionRate`: Percent
- `AverageResolutionTime`: Hours
- `IncidentsByPriority`: Count (by dimension)
- `IncidentsByClassification`: Count (by dimension)
- `ResponseTime`: Hours (by priority dimension)

**Frequency**: Every 6 hours (metrics_analyzer DAG)

### Health Checks

**ALB Target Health**:
- Endpoint: `/health`
- Expected: 200 OK
- Check every 30s

**Container Health**:
```bash
curl -f http://localhost:8080/health || exit 1
```
- Interval: 30s
- Timeout: 10s
- Retries: 3
- Start period: 60s

## Data Flow

### Incident Classification Flow

```
1. DynamoDB (Incidents table)
        ↓
2. DAG: incident_classifier (every 5 min)
        ↓
3. Helper: IncidentClassifier
        ├─ Analyze description/location
        └─ Apply classification rules
        ↓
4. Update DynamoDB with:
        ├─ classification
        ├─ priority
        └─ classifiedAt
```

### Notification Flow

```
1. DynamoDB (classified incidents)
        ↓
2. DAG: notification_dispatcher (every 10 min)
        ↓
3. Helper: SNSNotifier
        ├─ Ensure SNS topic exists
        └─ Publish message
        ↓
4. SNS Topic (by priority)
        ├─ High Priority
        ├─ Medium Priority
        └─ Low Priority
        ↓
5. Subscribers (email/SMS)
        ↓
6. Update DynamoDB (notificationSent=true)
```

### Report Generation Flow

```
1. DynamoDB (all incidents)
        ↓
2. DAG: report_generator (daily 8 AM)
        ↓
3. Helper: MetricsCalculator
        ├─ Calculate overall metrics
        ├─ Calculate location metrics
        └─ Calculate response metrics
        ↓
4. Generate HTML report
        ↓
5. Send via SES to admin@utec.edu.pe
```

## Escalabilidad

### Scaling Strategies

#### Vertical Scaling
```yaml
# Aumentar recursos por task
Webserver:
  CPU: 512 → 1024
  Memory: 1024 → 2048

Scheduler:
  CPU: 512 → 1024
  Memory: 1024 → 2048
```

#### Horizontal Scaling
```yaml
# Aumentar número de tasks
Webserver:
  DesiredCount: 1 → 2
  # ALB distribuye tráfico

# NO escalar scheduler (solo 1 necesario)
```

#### Database Scaling
```yaml
RDS:
  InstanceType: db.t3.micro → db.t3.small
  Storage: 20GB → 50GB
```

### Auto Scaling Configuration

```yaml
# Target Tracking Scaling
TargetCPU: 70%
TargetMemory: 80%
MinCapacity: 1
MaxCapacity: 4
ScaleOutCooldown: 300s
ScaleInCooldown: 600s
```

### Limits & Quotas

**Current Configuration**:
- Max concurrent DAG runs: 16 (parallelism)
- Max tasks per DAG: 8
- Total tasks: 5 DAGs × 1 task = 5 concurrent max

**Fargate Limits**:
- Max tasks per service: 1000
- CPU range: 0.25 vCPU - 16 vCPU
- Memory range: 512 MB - 120 GB

## Cost Breakdown

### Detailed Cost Analysis (1 Week)

#### 1. Compute (ECS Fargate)
```
Webserver:
  - vCPU: 0.5 @ $0.04048/hour
  - Memory: 1GB @ $0.004445/hour
  - Hours: 168 (7 days × 24h)
  - Cost: (0.5×0.04048 + 1×0.004445) × 168 = $4.15

Scheduler:
  - vCPU: 0.5 @ $0.04048/hour
  - Memory: 1GB @ $0.004445/hour
  - Hours: 168
  - Fargate Spot: 70% discount
  - Cost: (0.5×0.04048 + 1×0.004445) × 168 × 0.3 = $1.25

Total Compute: $5.40/week
```

#### 2. Database (RDS)
```
db.t3.micro:
  - $0.0170/hour × 168 hours = $2.86

Storage:
  - 20GB @ $0.115/GB-month
  - Weekly: 20 × 0.115 × 7/30 = $0.54

Total Database: $3.40/week
```

#### 3. Networking
```
NAT Gateway:
  - $0.045/hour × 168 hours = $7.56
  - Data processing: ~10GB @ $0.045/GB = $0.45
  
ALB:
  - $0.0225/hour × 168 hours = $3.78
  - LCU: ~0.5 @ $0.008/hour = $0.67

Total Networking: $12.46/week
```

#### 4. Storage
```
EFS:
  - Standard: ~1GB @ $0.30/GB-month
  - Weekly: 1 × 0.30 × 7/30 = $0.07

S3:
  - Storage: ~500MB @ $0.023/GB-month
  - Weekly: 0.5 × 0.023 × 7/30 = $0.003
  - Requests: Minimal

Total Storage: $0.07/week
```

#### 5. Other Services
```
CloudWatch Logs:
  - Ingestion: ~5GB @ $0.50/GB = $2.50
  - Storage: ~5GB × 7 days @ $0.03/GB-month = $0.035

SSM Parameter Store:
  - Standard: Free

ECR:
  - Storage: ~500MB @ $0.10/GB-month = $0.05
  - Weekly: 0.05 × 7/30 = $0.012

Total Other: $2.55/week
```

### Total Cost Summary

| Category | Weekly Cost | Percentage |
|----------|-------------|-----------|
| Compute (ECS) | $5.40 | 22.6% |
| Database (RDS) | $3.40 | 14.2% |
| Networking | $12.46 | 52.1% |
| Storage | $0.07 | 0.3% |
| CloudWatch | $2.55 | 10.7% |
| **TOTAL** | **$23.88** | **100%** |

**Nota**: Networking (NAT Gateway + ALB) representa ~52% del costo total.

### Cost Optimization Opportunities

#### For Production (beyond hackathon)

1. **Remove NAT Gateway** ($32/week saved)
   - Option: VPC Endpoints for AWS services
   - Option: Gateway endpoint for S3/DynamoDB (free)

2. **Use Fargate Spot for all tasks** ($2-3 saved)
   - 70% discount vs on-demand
   - Some interruption risk

3. **Reserved Capacity for RDS** (40% discount)
   - $1.30/week saved
   - Requires 1-year commitment

4. **Reduce CloudWatch retention** (minimal savings)
   - 7 days → 1 day
   - $0.50/week saved

5. **Use smaller instance types** (risky for stability)
   - RDS db.t3.micro → db.t4g.micro (ARM)
   - 10% cheaper

### Cost Comparison: ECS vs EC2

**Alternative: EC2-based deployment**
```
EC2 t3.small:
  - $0.0208/hour × 168 = $3.49
  - Storage: 20GB gp2 = $0.80
  - Data transfer: $0.50
  - Total: $4.79/week

Savings: $23.88 - $4.79 = $19.09/week

BUT: EC2 requires:
  - Server management
  - Security patching
  - Manual scaling
  - No automatic failover
```

**Verdict**: ECS Fargate es más caro pero mucho más fácil de gestionar para un hackathon de 1 semana.

## Disaster Recovery

### Backup Strategy

1. **RDS**: Sin backups automáticos (hackathon temporal)
2. **EFS**: Data persiste, no se elimina con stack
3. **DAGs**: En repo Git (fuente de verdad)
4. **CloudWatch Logs**: 7 días retención

### Recovery Scenarios

#### Scenario 1: Task Failure
- **Detection**: Health check fails
- **Action**: ECS auto-restart task
- **RTO**: 2-3 minutes

#### Scenario 2: Database Failure
- **Detection**: Connection errors
- **Action**: Manual restore from RDS snapshot (si existe)
- **RTO**: 30-60 minutes

#### Scenario 3: Complete Stack Deletion
- **Detection**: Stack deleted
- **Action**: Redeploy with `./scripts/deploy.sh`
- **RTO**: 20 minutes
- **Data Loss**: Metadata perdida, DAGs preserved

## Referencias

- [AWS ECS Best Practices](https://docs.aws.amazon.com/AmazonECS/latest/bestpracticesguide/)
- [Apache Airflow on ECS](https://airflow.apache.org/docs/apache-airflow/stable/howto/operator/ecs.html)
- [AWS Fargate Pricing](https://aws.amazon.com/fargate/pricing/)
- [EFS Performance](https://docs.aws.amazon.com/efs/latest/ug/performance.html)

---

**Última actualización**: 2025  
**Versión de arquitectura**: 2.0 (ECS/Fargate)
