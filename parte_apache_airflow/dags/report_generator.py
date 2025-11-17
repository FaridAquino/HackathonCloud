"""
DAG 3: Generación de Reportes Estadísticos
Genera reportes periódicos sobre incidentes cada minuto
"""
from datetime import datetime
from airflow import DAG
from airflow.operators.python import PythonOperator
import boto3
import os
from collections import Counter

AWS_REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE', 'Incidents')

def generate_report_task():
    """Genera reporte estadístico de incidentes"""
    dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
    table = dynamodb.Table(DYNAMODB_TABLE)
    
    # Obtener todos los incidentes
    response = table.scan()
    incidents = response.get('Items', [])
    
    print(f"📊 REPORTE DE INCIDENTES - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"=" * 60)
    
    # Estadísticas generales
    total = len(incidents)
    print(f"\n📈 Total de incidentes: {total}")
    
    # Por estado
    status_count = Counter([inc.get('Status', 'unknown') for inc in incidents])
    print(f"\n🔹 Por Estado:")
    for status, count in status_count.items():
        print(f"   • {status}: {count}")
    
    # Por prioridad
    priority_count = Counter([inc.get('Priority', 'unknown') for inc in incidents])
    print(f"\n🔸 Por Prioridad:")
    for priority, count in priority_count.items():
        print(f"   • {priority}: {count}")
    
    # Por área responsable
    all_areas = []
    for inc in incidents:
        areas = inc.get('ResponsibleArea', [])
        if isinstance(areas, list):
            all_areas.extend(areas)
    
    area_count = Counter(all_areas)
    print(f"\n🏢 Top 5 Áreas con más incidentes:")
    for area, count in area_count.most_common(5):
        print(f"   • {area}: {count}")
    
    print(f"\n" + "=" * 60)
    
    return {
        'total': total,
        'by_status': dict(status_count),
        'by_priority': dict(priority_count),
        'by_area': dict(area_count)
    }

# Configuración del DAG
default_args = {
    'owner': 'alerta-utec',
    'depends_on_past': False,
    'retries': 0,
}

with DAG(
    dag_id='report_generator',
    default_args=default_args,
    description='Generación periódica de reportes estadísticos',
    schedule_interval='* * * * *',  # Cada 1 minuto
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['reports', 'statistics', 'alerta-utec'],
) as dag:
    
    report_task = PythonOperator(
        task_id='generate_report',
        python_callable=generate_report_task,
    )
