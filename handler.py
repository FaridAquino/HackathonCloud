import boto3
import os
import uuid
import json
from datetime import datetime

INCIDENTS_TABLE = os.environ.get('DYNAMO_TABLE')
CONNECTIONS_TABLE = os.environ.get('CONNECTIONS_TABLE')

dynamodb = boto3.resource('dynamodb')


def lambda_handler(event, context):
    print(f"Evento recibido para lambda_handler: {event}")
    
    try:
        body = json.loads(event.get('body', '{}'))

        tenant_id = body['tenant_id']
        uuidv1 = str(uuid.uuid1())
        subType = body['subType']
        Title = body['Title']
        Description = body['Description']

        ResponsibleArea = [tenant_id]
        
        if (subType == 10 or subType == 13):
            ResponsibleArea.extend(["Bienestar", "ServicioMedico"])
        elif (subType == 14):
            ResponsibleArea.extend(["Bienestar", "ServicioMedico", "InfraestructuraMantenimiento"])
        elif (subType == 43):
            ResponsibleArea.extend(["ServicioMedico", "Seguridad"])
        elif (subType == 45):
            ResponsibleArea.append("InfraestructuraMantenimiento")

        CreatedById = body['CreatedById']
        CreatedByName = body['CreatedByName']
        Status = "active"

        if (subType==10 or subType==13 or subType==14 or subType==43):
            Priority="critica"
        elif(subType==11 or subType==45):
            Priority="alta"
        elif(subType==12 or subType==31 or subType==32 or subType==33 or subType==34 or subType==46 or subType==53):
            Priority="media"
        else:
            Priority="baja"

        if (subType==11 or subType==12 or subType==21 or subType==22 or subType==31 or subType==32 or subType==33or subType==41 or subType==51 or subType==52 or subType==53):
            IsGlobal=True
        else:
            IsGlobal=False

        CreatedAt = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        
        incidente = {
            'tenant_id': tenant_id,
            'uuid': uuidv1 + "#" + str(subType),
            'Title': Title,
            'Description': Description,
            'ResponsibleArea': ResponsibleArea,
            'CreatedById': CreatedById,
            'CreatedByName': CreatedByName,
            'Status': Status,
            'Priority': Priority,
            'IsGlobal': IsGlobal,
            'CreatedAt': CreatedAt,
            'ExecutingAt': None,
            'ResolvedAt': None,
            'LocationTower': body['LocationTower'],
            'LocationFloor': body['LocationFloor'],
            'LocationArea': body['LocationArea'],
            'Reference': body['Reference'],
            'AssignedToPersonalId': None,
            'Comment': None,
            'PendienteReasignacion': False
        }

        table = dynamodb.Table(INCIDENTS_TABLE)
        response = table.put_item(Item=incidente)

        connections_table = dynamodb.Table(CONNECTIONS_TABLE)
        
        response = connections_table.scan(
            ProjectionExpression='connectionId' # Solo necesitamos el connectionId
        )
        connections = response.get('Items', [])
        print(f"Encontradas {len(connections)} conexiones activas para transmitir.")
        # 3a. Configurar el cliente de API Gateway Management API
        endpoint_url = f"https://{event['requestContext']['domainName']}/{event['requestContext']['stage']}"
        apigateway_client = boto3.client('apigatewaymanagementapi', endpoint_url=endpoint_url)

        message_payload = json.dumps(incidente)

        for connection in connections:
            connection_id = connection['connectionId']
            try:
                apigateway_client.post_to_connection(
                    ConnectionId=connection_id,
                    Data=message_payload.encode('utf-8')
                )
                print(f"Incidente enviado a {connection_id}")
            
            except apigateway_client.exceptions.GoneException:
                
                print(f"Conexión muerta {connection_id} encontrada. Limpiando...")
                connections_table.delete_item(Key={'connectionId': connection_id})
            
            except Exception as e:
                print(f"Error enviando a {connection_id}: {e}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Incidente publicado y transmitido', 'responseId': incidente['uuid']})
        }
        
    except Exception as e:
        print(f"Error en lambda_handler: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error al procesar: {str(e)}')
        }


def connection_manager(event, context):

    connection_id = event['requestContext']['connectionId']
    route_key = event['requestContext']['routeKey']

    if not CONNECTIONS_TABLE:
        print("Error: CONNECTIONS_TABLE no está definida en las variables de entorno.")
        return {'statusCode': 500, 'body': 'Error de configuración del servidor.'}
        
    table = dynamodb.Table(CONNECTIONS_TABLE)

    if route_key == '$connect':
        try:
            query_params = event.get('queryStringParameters', {})
            created_by_id = query_params.get('CreatedById')

            if not created_by_id:
                print("Conexión rechazada: Falta 'CreatedById' en query string.")
                return {'statusCode': 400, 'body': "Falta 'CreatedById' en query string."}

            item = {
                'connectionId': connection_id,  # Clave Primaria
                'CreatedById': created_by_id
            }
            table.put_item(Item=item)
            print(f"Conexión guardada: {connection_id} para usuario {created_by_id}")
            
            return {'statusCode': 200, 'body': 'Conectado.'}

        except Exception as e:
            print(f"Error en $connect: {e}")
            return {'statusCode': 500, 'body': 'Fallo en $connect.'}

    elif route_key == '$disconnect':
        try:
            table.delete_item(
                Key={'connectionId': connection_id}
            )
            print(f"Conexión eliminada: {connection_id}")
            
            return {'statusCode': 200, 'body': 'Desconectado.'}
            
        except Exception as e:
            print(f"Error en $disconnect (no crítico): {e}")
            return {'statusCode': 200, 'body': 'Desconectado con error de limpieza.'}

    return {'statusCode': 500, 'body': 'Error en connection_manager.'}

def default_handler(event, context):
    print(f"Ruta $default invocada. Evento: {event}")
    return {
        'statusCode': 404,
        'body': json.dumps("Acción no reconocida.")
    }