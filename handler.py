import boto3
import os
import uuid
import json
from datetime import datetime

INCIDENTS_TABLE = os.environ.get('DYNAMO_TABLE')
CONNECTIONS_TABLE = os.environ.get('CONNECTIONS_TABLE')

dynamodb = boto3.resource('dynamodb')

def transmitir(event, message_payload_dict):

    try:
        connections_table = dynamodb.Table(CONNECTIONS_TABLE)
    except Exception as e:
        print(f"[Error Transmitir] No se pudo cargar CONNECTIONS_TABLE: {e}")
        return
        
    try:
        endpoint_url = f"https://{event['requestContext']['domainName']}/{event['requestContext']['stage']}"
        apigateway_client = boto3.client('apigatewaymanagementapi', endpoint_url=endpoint_url)
    except KeyError:
        print(f"[Error Transmitir] El evento no tiene 'requestContext' para el endpoint_url.")
        return

    try:
        response = connections_table.scan(ProjectionExpression='connectionId')
        connections = response.get('Items', [])
    except Exception as e:
        print(f"[Error Transmitir] Fallo al escanear la tabla de conexiones: {e}")
        return # No se puede continuar

    print(f"Encontradas {len(connections)} conexiones para transmitir.")
    
    message_payload_str = json.dumps(message_payload_dict)

    # 5. Iterar y enviar el mensaje
    for connection in connections:
        connection_id = connection['connectionId']
        try:
            apigateway_client.post_to_connection(
                ConnectionId=connection_id,
                Data=message_payload_str.encode('utf-8')
            )
        except apigateway_client.exceptions.GoneException:
            print(f"[Info Transmitir] Conexión muerta {connection_id}. Limpiando.")
            connections_table.delete_item(Key={'connectionId': connection_id})
        except Exception as e:
            print(f"[Error Transmitir] No se pudo enviar a {connection_id}: {e}")

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

        transmission_payload = {
            'action': 'NewIncident', # Usamos una acción diferente para el cliente
            'incident': incidente
        }
        transmitir(event, transmission_payload)
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

def EditIncidentContent(event, context):
    try:
        body = json.loads(event.get('body', '{}'))

        tenant_id = body['tenant_id']
        uuid = body['uuid']
        actionToDo=body['actionToDo']
        
        if (actionToDo=="Editar"):
            table = dynamodb.Table(INCIDENTS_TABLE)
            new_title = body.get("new_title")
            new_description = body.get('new_description')

            if not new_title or not new_description:
                return {'statusCode': 400, 'body': json.dumps('Faltan "new_title" o "new_description" para editar')}
            
            try:
                response = table.update_item(
                    Key={
                        'tenant_id': tenant_id,
                        'uuid': uuid
                    },
                    UpdateExpression="SET #title = :t, #desc = :d",
                    ExpressionAttributeNames={
                        '#title': 'Title',
                        '#desc': 'Description'
                    },
                    ExpressionAttributeValues={
                        ':t': new_title,
                        ':d': new_description
                    },
                    ReturnValues="UPDATED_NEW"
                )
                
                transmission_payload = {
                    'action': 'EditIncidentContent',
                    'uuid': uuid,
                    'status': 'assigned'
                }
                transmitir(event, transmission_payload)

                return {
                    'statusCode': 200, 
                    'body': json.dumps({'message': 'Incidente editado', 'updatedAttributes': response.get('Attributes')})
                }

            except Exception as e:
                print(f"Error al actualizar: {e}")
                return {'statusCode': 500, 'body': json.dumps('Error al actualizar el ítem')}

        elif (actionToDo == "Eliminar"):
            print(f"Procesando eliminación para: {uuid} en tenant {tenant_id}")
            
            try:
                table = dynamodb.Table(INCIDENTS_TABLE)
                
                table.delete_item(
                    Key={
                        'tenant_id': tenant_id,
                        'uuid': uuid
                    }
                )
                
                print(f"Incidente {uuid} eliminado exitosamente de DynamoDB.")

                transmission_payload = {
                    'action': 'IncidentDeleted',
                    'tenant_id': tenant_id,
                    'uuid': uuid
                }
                transmitir(event, transmission_payload)

                return {
                    'statusCode': 200,
                    'body': json.dumps({'message': 'Incidente eliminado y notificado', 'deletedUuid': uuid})
                }

            except Exception as e:
                print(f"Error al eliminar el ítem: {e}")
                return {
                    'statusCode': 500,
                    'body': json.dumps(f'Error en DynamoDB al eliminar: {str(e)}')
                }

        else:
            return {
                'statusCode': 400,
                'body': json.dumps(f'Acción no reconocida: {actionToDo}')
            }

    except Exception as e:
        print(f"Error en EditIncidentContent: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error al procesar: {str(e)}')
        }
    