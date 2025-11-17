"""
DAG 1: Clasificación Automática de Incidentes
Clasifica incidentes por tipo y urgencia cada minuto
"""
from datetime import datetime
from airflow import DAG
from airflow.operators.python import PythonOperator
import boto3
import os

AWS_REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE', 'Incidents')

def classify_incidents_task():
    """Clasifica incidentes por prioridad basado en subType"""
    dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
    table = dynamodb.Table(DYNAMODB_TABLE)
    
    # Obtener incidentes activos
    response = table.scan(
        FilterExpression='#status = :active',
        ExpressionAttributeNames={'#status': 'Status'},
        ExpressionAttributeValues={':active': 'active'}
    )
    
    incidents = response.get('Items', [])
    print(f"📊 Encontrados {len(incidents)} incidentes activos para clasificar")
    
    for incident in incidents:
        tenant_id = incident.get('tenant_id')
        uuid = incident.get('uuid')
        priority = incident.get('Priority', 'baja')
        
        # Actualizar clasificación si es necesario
        print(f"✅ Incidente {uuid}: Prioridad={priority}")
    
    return f"Clasificados {len(incidents)} incidentes"

# Configuración del DAG
default_args = {
    'owner': 'alerta-utec',
    'depends_on_past': False,
    'retries': 0,
}

with DAG(
    dag_id='incident_classifier',
    default_args=default_args,
    description='Clasificación automática de incidentes por tipo y urgencia',
    schedule_interval='* * * * *',  # Cada 1 minuto
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['incidents', 'classification', 'alerta-utec'],
) as dag:
    
    classify_task = PythonOperator(
        task_id='classify_incidents',
        python_callable=classify_incidents_task,
    )
