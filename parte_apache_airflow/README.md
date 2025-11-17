# 🚀 AlertaUTEC - Apache Airflow on AWS ECS

Sistema de automatización de workflows para AlertaUTEC usando Apache Airflow desplegado en AWS ECS Fargate.

## 📋 Descripción

Este proyecto despliega Apache Airflow 2.8.0 en AWS ECS Fargate para automatizar tres workflows críticos del sistema AlertaUTEC:

1. **Clasificación automática de incidentes** por tipo y urgencia
2. **Envío de notificaciones por email** a áreas responsables (usando Amazon SNS)
3. **Generación periódica de reportes estadísticos**

## 🏗️ Arquitectura

### Componentes AWS

- **ECS Fargate**: Ejecuta contenedores sin gestión de servidores
  - Cluster: `alerta-utec-airflow-cluster`
  - Services: `webserver` (UI) y `scheduler` (orquestador)
- **ECR**: Repositorio de imágenes Docker
- **EFS**: Sistema de archivos compartido para DAGs y logs
- **VPC**: Red aislada con subnets públicas
- **Security Groups**: Control de acceso (puerto 8080 para UI)
- **DynamoDB**: Tablas de datos
  - `Incidents`: Incidentes con `tenant_id` (área)
  - `api-hackathon-websocket-users-dev`: Usuarios con roles y áreas
- **Amazon SNS**: Envío de emails reales a usuarios
- **CloudWatch Logs**: Logs centralizados en `/ecs/alerta-utec-airflow`

### DAGs Implementados

#### 1. `incident_classifier.py`
- **Frecuencia**: Cada minuto
- **Función**: Clasifica incidentes activos por prioridad y tipo
- **Tabla**: `Incidents` (Status='active')

#### 2. `notification_dispatcher.py` 🔔
- **Frecuencia**: Cada minuto
- **Función**: Envía emails REALES a usuarios PERSONAL y COORDINATOR
- **Lógica**:
  - Busca incidentes con `Status='Pendiente'` o `'EnAtencion'`
  - Agrupa por área (`tenant_id` del incidente)
  - Filtra usuarios por `Role='PERSONAL'` o `'COORDINATOR'` y `Area` coincidente
  - Crea topic SNS por usuario: `AlertaUTEC-User-{UUID}`
  - Suscribe email del usuario al topic
  - Publica notificación con lista de incidentes
- **Primera vez**: Usuario recibe email de confirmación de AWS SNS (debe hacer clic en "Confirm subscription")
- **Siguientes veces**: Emails automáticos con incidentes pendientes

#### 3. `report_generator.py`
- **Frecuencia**: Cada minuto
- **Función**: Genera estadísticas de incidentes (total, por estado, por prioridad, por área)
- **Salida**: Logs en CloudWatch

## 🚀 Despliegue

### Prerrequisitos

- AWS CLI configurado
- Docker instalado
- Cuenta AWS (Academy compatible)
- PowerShell (Windows)

### Paso 1: Construir y Subir Imagen Docker

```powershell
cd parte_apache_airflow

# Build
docker build -t alerta-utec-airflow:latest -f docker/Dockerfile .

# Login ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 858624593089.dkr.ecr.us-east-1.amazonaws.com

# Tag y Push
docker tag alerta-utec-airflow:latest 858624593089.dkr.ecr.us-east-1.amazonaws.com/alerta-utec-airflow:latest
docker push 858624593089.dkr.ecr.us-east-1.amazonaws.com/alerta-utec-airflow:latest
```

### Paso 2: Desplegar CloudFormation

```powershell
# Subir templates a S3
aws s3 sync cloudformation/ s3://alerta-utec-cfn-templates/ --exclude "*.md"

# Desplegar stack principal
aws cloudformation create-stack \
  --stack-name alerta-utec-airflow-master \
  --template-body file://cloudformation/master-stack.yaml \
  --parameters \
    ParameterKey=AirflowAdminPassword,ParameterValue=Admin2025! \
    ParameterKey=DynamoDBTableName,ParameterValue=Incidents \
  --capabilities CAPABILITY_IAM
```

### Paso 3: Actualizar DAGs

Si modificas los DAGs, necesitas reconstruir la imagen:

```powershell
# Rebuild y push
docker build -t alerta-utec-airflow:latest -f docker/Dockerfile .
docker tag alerta-utec-airflow:latest 858624593089.dkr.ecr.us-east-1.amazonaws.com/alerta-utec-airflow:latest
docker push 858624593089.dkr.ecr.us-east-1.amazonaws.com/alerta-utec-airflow:latest

# Force redeploy de servicios ECS
aws ecs update-service --cluster alerta-utec-airflow-cluster --service alerta-utec-airflow-scheduler --force-new-deployment
aws ecs update-service --cluster alerta-utec-airflow-cluster --service alerta-utec-airflow-webserver --force-new-deployment
```

## 🔍 Acceso y Monitoreo

### Airflow Web UI

```
URL: http://98.80.228.20:8080
Usuario: admin
Contraseña: Admin2025!
```

### Ver Logs de DAGs

**Opción 1: Airflow UI**
1. Abre http://98.80.228.20:8080
2. Click en el DAG (ej: `notification_dispatcher`)
3. Click en el cuadrado de la ejecución
4. Click en "Log"

**Opción 2: CloudWatch Logs**

```powershell
# Logs en tiempo real
aws logs tail /ecs/alerta-utec-airflow --follow --format short

# Filtrar por DAG específico
aws logs tail /ecs/alerta-utec-airflow --follow --format short | Select-String -Pattern "notification_dispatcher"

# Últimos 3 minutos
aws logs tail /ecs/alerta-utec-airflow --since 3m --format short
```

### Verificar Estado de Servicios

```powershell
# Ver servicios ECS
aws ecs describe-services \
  --cluster alerta-utec-airflow-cluster \
  --services alerta-utec-airflow-webserver alerta-utec-airflow-scheduler

# Ver tasks corriendo
aws ecs list-tasks --cluster alerta-utec-airflow-cluster

# Ver logs específicos
aws logs tail /ecs/alerta-utec-airflow --since 5m
```

## 📧 Sistema de Notificaciones por Email

### ¿Cómo funciona?

El DAG `notification_dispatcher.py` usa **Amazon SNS** (no SES, que está bloqueado en AWS Academy) para enviar emails REALES:

1. **Cada minuto**, el DAG escanea la tabla `Incidents` buscando:
   - `Status = 'Pendiente'` o `'EnAtencion'`
   
2. **Agrupa incidentes** por área (`tenant_id`)

3. **Busca usuarios** en `api-hackathon-websocket-users-dev` que:
   - Tengan `Role = 'PERSONAL'` o `'COORDINATOR'`
   - Su `Area` coincida con el `tenant_id` del incidente

4. **Por cada usuario**:
   - Crea/obtiene topic SNS: `AlertaUTEC-User-{UUID}`
   - Suscribe el email del usuario
   - Publica mensaje con lista de incidentes

### Primera Vez (Confirmación SNS)

**El usuario recibirá un email de AWS SNS**:
```
Subject: AWS Notification - Subscription Confirmation
From: no-reply@sns.amazonaws.com

You have chosen to subscribe to the topic:
arn:aws:sns:us-east-1:858624593089:AlertaUTEC-User-{UUID}

To confirm this subscription, click or visit the link below:
[Confirm subscription]
```

⚠️ **IMPORTANTE**: El usuario DEBE hacer clic en "Confirm subscription" para recibir notificaciones futuras.

### Emails Siguientes (Notificaciones)

Una vez confirmado, recibirá emails como:
```
Subject: 🚨 AlertaUTEC - 3 Incidente(s) Pendiente(s) en Mantenimiento
From: no-reply@sns.amazonaws.com

¡Hola Maria Garcia!

Tienes 3 incidente(s) pendiente(s) en el área de Mantenimiento:

📌 Incidente: Fuga de agua
   Prioridad: Alta
   Estado: Pendiente
   Creado: 2025-11-16T10:30:00

📌 Incidente: Luz fundida
   Prioridad: Media
   Estado: EnAtencion
   Creado: 2025-11-16T09:15:00
```

### Verificar Envío de Emails

```powershell
# Ver topics SNS creados
aws sns list-topics --region us-east-1 | Select-String "AlertaUTEC"

# Ver suscripciones
aws sns list-subscriptions --region us-east-1

# Ver logs del DAG
aws logs tail /ecs/alerta-utec-airflow --follow | Select-String -Pattern "SNS|email|Notificando"
```

## 🧪 Prueba del Sistema

### 1. Verificar Usuarios

```powershell
aws dynamodb scan \
  --table-name api-hackathon-websocket-users-dev \
  --filter-expression "#r IN (:p, :c)" \
  --expression-attribute-names '{"#r":"Role"}' \
  --expression-attribute-values '{":p":{"S":"PERSONAL"},":c":{"S":"COORDINATOR"}}' \
  --region us-east-1 \
  --query 'Items[*].[Email.S, Role.S, Area.S]' \
  --output table
```

### 2. Crear Incidente de Prueba

```powershell
# Crear archivo JSON
@'
{
  "uuid": {"S": "test-001-uuid"},
  "Status": {"S": "Pendiente"},
  "tenant_id": {"S": "Mantenimiento"},
  "Title": {"S": "Prueba de notificaciones"},
  "Description": {"S": "Incidente de prueba"},
  "Priority": {"S": "Alta"},
  "CreatedAt": {"S": "2025-11-16T04:00:00Z"},
  "CreatedByName": {"S": "Test User"},
  "CreatedById": {"S": "test-user-123"},
  "PendienteReasignacion": {"BOOL": false},
  "IsGlobal": {"BOOL": false},
  "LocationArea": {"S": "Area de prueba"},
  "LocationFloor": {"S": "Piso 1"},
  "LocationTower": {"S": "Torre A"}
}
'@ | Out-File -FilePath temp_incident.json -Encoding ASCII -NoNewline

# Insertar en DynamoDB
aws dynamodb put-item --table-name Incidents --region us-east-1 --item file://temp_incident.json
Remove-Item temp_incident.json
```

### 3. Monitorear Ejecución

Espera 1 minuto y revisa los logs:

```powershell
aws logs tail /ecs/alerta-utec-airflow --since 2m --follow | Select-String -Pattern "notification_dispatcher|SNS|Notificando"
```

### 4. Verificar Email

El usuario con `Area='Mantenimiento'` recibirá:
1. **Primera vez**: Email de confirmación de AWS SNS
2. **Después de confirmar**: Email con el incidente TEST-001

## 🔧 Variables de Entorno

Configuradas en `cloudformation/07-ecs-services.yaml`:

```yaml
Environment:
  - Name: AIRFLOW__CORE__EXECUTOR
    Value: LocalExecutor
  - Name: AIRFLOW__CORE__SQL_ALCHEMY_CONN
    Value: sqlite:////opt/airflow/airflow.db
  - Name: AIRFLOW__CORE__LOAD_EXAMPLES
    Value: "False"
  - Name: AWS_DEFAULT_REGION
    Value: us-east-1
  - Name: DYNAMODB_TABLE
    Value: Incidents
  - Name: USERS_TABLE
    Value: api-hackathon-websocket-users-dev
  - Name: FROM_EMAIL
    Value: juan.velo@utec.edu.pe
```

## 📂 Estructura del Proyecto

```
parte_apache_airflow/
├── cloudformation/          # Templates CloudFormation
│   ├── 01-network.yaml     # VPC, Subnets, Security Groups
│   ├── 03-ecr.yaml         # Repositorio ECR
│   ├── 04-efs.yaml         # Sistema de archivos EFS
│   ├── 05-ecs-cluster.yaml # Cluster ECS
│   ├── 07-ecs-services.yaml # Task definitions y services
│   └── master-stack.yaml   # Orquestador principal
├── config/
│   ├── airflow.cfg         # Configuración Airflow
│   └── requirements.txt    # Dependencias Python
├── dags/                   # DAGs de Airflow
│   ├── incident_classifier.py
│   ├── notification_dispatcher.py
│   └── report_generator.py
├── docker/
│   ├── Dockerfile          # Imagen custom de Airflow
│   └── entrypoint.sh       # Script de inicialización
└── scripts/                # Scripts de utilidad
    ├── build-and-push.sh   # Build y push a ECR
    ├── deploy.sh           # Despliegue CloudFormation
    └── get-airflow-url.sh  # Obtener URL de Airflow
```

## 🐛 Troubleshooting

### DAG no aparece en la UI

**Problema**: Los DAGs no se muestran en Airflow UI

**Solución**: Verificar que EFS esté montado correctamente

```powershell
# Verificar mount en task
aws ecs describe-tasks --cluster alerta-utec-airflow-cluster --tasks <task-arn>

# Rebuild y redeploy
docker build -t alerta-utec-airflow:latest -f docker/Dockerfile .
docker push 858624593089.dkr.ecr.us-east-1.amazonaws.com/alerta-utec-airflow:latest
aws ecs update-service --cluster alerta-utec-airflow-cluster --service alerta-utec-airflow-scheduler --force-new-deployment
```

### No se envían emails

**Problema**: El DAG corre exitosamente pero no llegan emails

**Posibles causas**:
1. **No hay incidentes Pendientes/EnAtencion**: Verificar con:
   ```powershell
   aws dynamodb scan --table-name Incidents --filter-expression "#s IN (:p, :e)" --expression-attribute-names '{"#s":"Status"}' --expression-attribute-values '{":p":{"S":"Pendiente"},":e":{"S":"EnAtencion"}}' --query 'Count'
   ```

2. **No hay usuarios en el área**: Verificar que existan usuarios PERSONAL/COORDINATOR con `Area` que coincida con `tenant_id` del incidente

3. **Email no confirmado**: Usuario debe confirmar suscripción SNS la primera vez

### Security Group bloqueado

**Problema**: No puedo acceder a Airflow UI

**Solución**: Agregar tu IP al Security Group

```powershell
$MY_IP = (Invoke-WebRequest -Uri "https://checkip.amazonaws.com" -UseBasicParsing).Content.Trim()
aws ec2 authorize-security-group-ingress --group-id sg-0aef6a7e220a39301 --protocol tcp --port 8080 --cidr "$MY_IP/32"
```

### Logs no aparecen

**Problema**: No veo logs en CloudWatch

**Solución**: Verificar IAM role del task

```powershell
# Ver logs directamente del contenedor
aws ecs execute-command --cluster alerta-utec-airflow-cluster --task <task-id> --container webserver --command "/bin/bash" --interactive
```

## 📊 Monitoreo y Métricas

### Verificar Ejecuciones de DAGs

```powershell
# Número de ejecuciones exitosas/fallidas (desde Airflow UI)
# O desde logs:
aws logs filter-log-events \
  --log-group-name /ecs/alerta-utec-airflow \
  --filter-pattern "state=success" \
  --start-time $(date -u -d '1 hour ago' +%s)000
```

### Verificar Topics SNS

```powershell
# Listar todos los topics AlertaUTEC
aws sns list-topics --region us-east-1 --query 'Topics[?contains(TopicArn, `AlertaUTEC`)]'

# Ver detalles de suscripciones
aws sns list-subscriptions --region us-east-1 --query 'Subscriptions[?contains(TopicArn, `AlertaUTEC`)]'
```

## 🔐 Seguridad

- **VPC**: Red aislada con subnets públicas
- **Security Groups**: Solo puerto 8080 (Airflow UI) y 2049 (EFS)
- **IAM Roles**: Permisos mínimos necesarios (DynamoDB, SNS, CloudWatch)
- **Secrets**: Password de Airflow en CloudFormation Parameters (NoEcho)
- **SNS**: Confirmación obligatoria de suscripción (doble opt-in)

## 📝 Notas Importantes

- ⚠️ **AWS Academy**: SES está bloqueado, por eso se usa SNS
- ⏱️ **Frecuencia**: DAGs ejecutan cada minuto (ajustable en `schedule_interval`)
- 🔄 **tenant_id = ÁREA**: En tabla Incidents, `tenant_id` representa el área del incidente
- 👥 **Roles**: Solo notifica a usuarios PERSONAL y COORDINATOR
- ✉️ **Primera vez**: Usuarios deben confirmar suscripción SNS
- 💾 **SQLite**: Airflow usa SQLite (no RDS) para simplificar

## 🚀 Próximos Pasos

- [ ] Configurar alertas CloudWatch para DAG failures
- [ ] Agregar dashboard Grafana para métricas
- [ ] Implementar retry logic más robusto
- [ ] Optimizar queries DynamoDB con índices
- [ ] Agregar tests unitarios para DAGs
- [ ] Configurar autoscaling para ECS tasks

## 📞 Soporte

- **Logs**: `aws logs tail /ecs/alerta-utec-airflow --follow`
- **UI**: http://98.80.228.20:8080 (admin / Admin2025!)
- **CloudWatch**: Buscar log group `/ecs/alerta-utec-airflow`

---

**Desarrollado para AlertaUTEC Hackathon - UTEC 2025**
