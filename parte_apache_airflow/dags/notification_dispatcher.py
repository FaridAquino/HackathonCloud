"""
DAG 2: Envío de Notificaciones por Email REALES a Usuarios
Envía emails REALES usando Amazon SNS (alternativa a SES para AWS Academy)
"""
from datetime import datetime
from airflow import DAG
from airflow.operators.python import PythonOperator
import boto3
import os
from collections import defaultdict

AWS_REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
INCIDENTS_TABLE = os.environ.get('DYNAMODB_TABLE', 'Incidents')
USERS_TABLE = os.environ.get('USERS_TABLE', 'api-hackathon-websocket-users-dev')
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'juan.velo@utec.edu.pe')

# SNS Topic ARN para emails (se crea automáticamente si no existe)
SNS_TOPIC_PREFIX = 'AlertaUTEC-User-'

def create_or_get_sns_topic(sns_client, user_email, user_uuid):
    """Crea o obtiene topic SNS para el usuario"""
    topic_name = f"{SNS_TOPIC_PREFIX}{user_uuid}"
    
    try:
        # Intentar crear topic (si ya existe, devuelve el existente)
        response = sns_client.create_topic(Name=topic_name)
        topic_arn = response['TopicArn']
        
        # Suscribir email si no está suscrito
        try:
            sns_client.subscribe(
                TopicArn=topic_arn,
                Protocol='email',
                Endpoint=user_email
            )
            print(f"   ✅ Topic SNS creado/verificado: {topic_name}")
        except Exception as e:
            print(f"   ⚠️  Ya suscrito o error: {str(e)[:100]}")
        
        return topic_arn
    except Exception as e:
        print(f"   ❌ Error creando topic: {str(e)}")
        return None

def send_notifications_task():
    """
    Envía emails REALES usando Amazon SNS.
    IMPORTANTE: tenant_id en Incidents = ÁREA del incidente
    """
    dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
    sns = boto3.client('sns', region_name=AWS_REGION)
    
    incidents_table = dynamodb.Table(INCIDENTS_TABLE)
    users_table = dynamodb.Table(USERS_TABLE)
    
    # 1. Obtener incidentes no resueltos (Pendiente o EnAtencion)
    print(f"🔍 Buscando incidentes no resueltos...")
    incidents_response = incidents_table.scan(
        FilterExpression='#status IN (:pendiente, :atencion)',
        ExpressionAttributeNames={'#status': 'Status'},
        ExpressionAttributeValues={
            ':pendiente': 'Pendiente',
            ':atencion': 'EnAtencion'
        }
    )
    
    incidents = incidents_response.get('Items', [])
    print(f"📋 Encontrados {len(incidents)} incidentes no resueltos")
    
    if not incidents:
        print("✅ No hay incidentes pendientes para notificar")
        return "No hay incidentes pendientes"
    
    # 2. Agrupar incidentes por área (usando tenant_id)
    incidents_by_area = defaultdict(list)
    for incident in incidents:
        # tenant_id es el ÁREA del incidente
        area = incident.get('tenant_id')
        if area:
            incidents_by_area[area].append(incident)
            print(f"  📌 Incidente {incident.get('uuid', 'N/A')[:8]} → Área: {area}")
    
    print(f"🏢 Incidentes agrupados en {len(incidents_by_area)} áreas: {list(incidents_by_area.keys())}")
    
    # 3. Obtener usuarios PERSONAL y COORDINATOR
    print(f"👥 Buscando usuarios PERSONAL y COORDINATOR...")
    users_personal = users_table.scan(
        FilterExpression='#role = :role',
        ExpressionAttributeNames={'#role': 'Role'},
        ExpressionAttributeValues={':role': 'PERSONAL'}
    ).get('Items', [])
    
    users_coordinator = users_table.scan(
        FilterExpression='#role = :role',
        ExpressionAttributeNames={'#role': 'Role'},
        ExpressionAttributeValues={':role': 'COORDINATOR'}
    ).get('Items', [])
    
    all_users = users_personal + users_coordinator
    print(f"📧 Encontrados {len(all_users)} usuarios (PERSONAL: {len(users_personal)}, COORDINATOR: {len(users_coordinator)})")
    
    # 4. Enviar emails por área
    emails_sent = 0
    errors = 0
    
    for user in all_users:
        email = user.get('Email')
        user_area = user.get('Area')
        name = user.get('Name', 'Usuario')
        role = user.get('Role')
        
        if not email or not user_area:
            print(f"⚠️  Usuario {user.get('UUID', 'unknown')} sin email o área")
            continue
        
        # Obtener incidentes del área del usuario
        user_incidents = incidents_by_area.get(user_area, [])
        
        if not user_incidents:
            continue  # No hay incidentes en su área
        
        # Construir HTML del email
        email_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; }}
                .header {{ background-color: #d32f2f; color: white; padding: 20px; }}
                .incident {{ border: 1px solid #ddd; margin: 10px 0; padding: 15px; border-radius: 5px; }}
                .priority-alta {{ border-left: 5px solid #d32f2f; }}
                .priority-media {{ border-left: 5px solid #ff9800; }}
                .priority-baja {{ border-left: 5px solid #4caf50; }}
                .footer {{ margin-top: 20px; font-size: 12px; color: #666; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>🚨 AlertaUTEC - Incidentes No Resueltos</h1>
            </div>
            
            <p>Hola <strong>{name}</strong> ({role}),</p>
            <p>Tienes <strong>{len(user_incidents)}</strong> incidente(s) no resuelto(s) en el área: <strong>{user_area}</strong></p>
            
            <h2>📋 Lista de Incidentes:</h2>
        """
        
        for idx, incident in enumerate(user_incidents, 1):
            priority = incident.get('Priority', 'baja').lower()
            priority_class = f"priority-{priority}"
            
            email_body += f"""
            <div class="incident {priority_class}">
                <h3>#{idx} - {incident.get('Title', 'Sin título')}</h3>
                <p><strong>Estado:</strong> {incident.get('Status', 'Desconocido')}</p>
                <p><strong>Prioridad:</strong> {incident.get('Priority', 'N/A')}</p>
                <p><strong>Tipo:</strong> {incident.get('Type', 'N/A')} - {incident.get('SubType', 'N/A')}</p>
                <p><strong>Descripción:</strong> {incident.get('Description', 'Sin descripción')}</p>
                <p><strong>Ubicación:</strong> {incident.get('Location', 'No especificada')}</p>
                <p><strong>ID:</strong> <code>{incident.get('uuid', 'N/A')}</code></p>
            </div>
            """
        
        email_body += f"""
            <div class="footer">
                <p>Este es un correo automático generado por Apache Airflow.</p>
                <p>Por favor, no responda a este email.</p>
                <p>Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            </div>
        </body>
        </html>
        """
        
        # Enviar email REAL usando Amazon SNS
        try:
            user_uuid = user.get('UUID', 'unknown')
            
            print(f"📤 Enviando email REAL a {email} ({name} - {user_area}): {len(user_incidents)} incidentes")
            
            # Crear mensaje de texto plano (SNS no soporta HTML directamente)
            message_text = f"""
AlertaUTEC - Incidentes No Resueltos

Hola {name} ({role}),

Tienes {len(user_incidents)} incidente(s) no resuelto(s) en el área: {user_area}

LISTA DE INCIDENTES:
{'='*60}
"""
            
            for idx, incident in enumerate(user_incidents, 1):
                message_text += f"""
Incidente #{idx}
--------------
Título: {incident.get('Title', 'Sin título')}
Estado: {incident.get('Status', 'Desconocido')}
Prioridad: {incident.get('Priority', 'N/A')}
Tipo: {incident.get('Type', 'N/A')} - {incident.get('SubType', 'N/A')}
Descripción: {incident.get('Description', 'Sin descripción')}
Ubicación: {incident.get('Location', 'No especificada')}
ID: {incident.get('uuid', 'N/A')}

"""
            
            message_text += f"""
{'='*60}
Este es un correo automático generado por Apache Airflow.
Sistema AlertaUTEC - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
            
            # Crear o obtener topic SNS para este usuario
            topic_arn = create_or_get_sns_topic(sns, email, user_uuid)
            
            if topic_arn:
                # Publicar mensaje en SNS topic
                response = sns.publish(
                    TopicArn=topic_arn,
                    Subject=f'AlertaUTEC - {len(user_incidents)} Incidente(s) Pendiente(s) en {user_area}',
                    Message=message_text
                )
                
                emails_sent += 1
                print(f"✅ Email REAL enviado a {email} (MessageId: {response.get('MessageId', 'N/A')[:20]}...)")
            else:
                errors += 1
                print(f"❌ No se pudo crear topic SNS para {email}")
            
        except Exception as e:
            errors += 1
            print(f"❌ Error enviando email REAL a {email}: {str(e)}")
    
    summary = f"Emails enviados: {emails_sent}, Errores: {errors}"
    print(f"\n{'='*60}")
    print(f"📊 RESUMEN: {summary}")
    print(f"{'='*60}\n")
    
    return summary

# Configuración del DAG
default_args = {
    'owner': 'alerta-utec',
    'depends_on_past': False,
    'retries': 0,
}

with DAG(
    dag_id='notification_dispatcher',
    default_args=default_args,
    description='Envío de notificaciones a áreas responsables',
    schedule_interval='* * * * *',  # Cada 1 minuto
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['notifications', 'sns', 'alerta-utec'],
) as dag:
    
    notify_task = PythonOperator(
        task_id='send_notifications',
        python_callable=send_notifications_task,
    )
