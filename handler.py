import boto3
import os
import uuid
import json
from datetime import datetime, timezone

INCIDENTS_TABLE = os.environ.get('DYNAMO_TABLE')
CONNECTIONS_TABLE = os.environ.get('CONNECTIONS_TABLE')
TOKENS_TABLE = os.environ.get('TOKENS_TABLE')
USERS_TABLE = os.environ.get('USERS_TABLE')
dynamodb = boto3.resource('dynamodb')

def _validate_token_and_get_user(token_str):

    try:
        tokens_table_client = dynamodb.Table(TOKENS_TABLE)
        users_table_client = dynamodb.Table(USERS_TABLE)

        token_resp = tokens_table_client.get_item(Key={"Token": token_str})
        if "Item" not in token_resp:
            print("Auth Error: Token no existe")
            return None
        
        token_item = token_resp["Item"]
        
        expires_at_str = token_item.get("ExpiresAt")
        if not expires_at_str:
            print("Auth Error: Token no tiene ExpiresAt")
            return None

        expires_at_naive = datetime.fromisoformat(expires_at_str)

        expires_at_aware = expires_at_naive.replace(tzinfo=timezone.utc)

        if datetime.now(timezone.utc) > expires_at_aware:
            print(f"Auth Error: Token expirado. Expiró: {expires_at_aware}, Ahora: {datetime.now(timezone.utc)}")
            return None
        
        role = token_item["Role"]
        uuid = token_item["UUID"]
        user_resp = users_table_client.get_item(Key={"Role": role, "UUID": uuid})
        
        if "Item" not in user_resp:
            print("Auth Error: Usuario no encontrado")
            return None
            
        user = user_resp["Item"]
        user.pop("PasswordHash", None)
        return user
        
    except Exception as e:
        print(f"Auth Exception: {e}")
        return None

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
            token = query_params.get('token')

            if not token:
                print("Conexión rechazada: Falta 'token' en query string.")
                return {'statusCode': 401, 'body': "Falta 'token' en query string."}

            user = _validate_token_and_get_user(token)

            if user is None:
                print("Conexión rechazada: Token inválido o expirado.")
                return {'statusCode': 403, 'body': "Token inválido o expirado."}
            
            item = {
                'connectionId': connection_id,
                'CreatedById': user['UUID'],
                'Role': user['Role'],
                'Area': user.get('Area', None),
                'connectTime': datetime.now(timezone.utc).isoformat()
            }

            table.put_item(Item=item)
            
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

def lambda_handler(event, context):
    print(f"Evento recibido para lambda_handler: {event}")

    connection_id = event['requestContext']['connectionId']
    connections_table = dynamodb.Table(CONNECTIONS_TABLE)
        
    conn_resp = connections_table.get_item(Key={'connectionId': connection_id})
    if 'Item' not in conn_resp:
        return {'statusCode': 403, 'body': json.dumps('Conexión no autorizada. Reconecte.')}
            
    user_role = conn_resp['Item'].get('Role')

    if (user_role != 'Comunidad'):
        return {'statusCode': 403, 'body': json.dumps('Acceso denegado. Solo Comunidad puede crear incidentes.')}
    
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

def EditIncidentContent(event, context):
    
    connection_id = event['requestContext']['connectionId']
    connections_table = dynamodb.Table(CONNECTIONS_TABLE)
        
    conn_resp = connections_table.get_item(Key={'connectionId': connection_id})
    if 'Item' not in conn_resp:
        return {'statusCode': 403, 'body': json.dumps('Conexión no autorizada. Reconecte.')}
            
    user_role = conn_resp['Item'].get('Role')
    CreatedById= conn_resp['Item'].get('CreatedById')

    if (user_role != 'Comunidad' and user_role!='Coordinadores'):
        return {'statusCode': 403, 'body': json.dumps('Acceso denegado. Solo Comunidad o coordinadores puede editar o eliminar incidentes.')}

    if (user_role=='Comunidad'):
        try:
            body = json.loads(event.get('body', '{}'))

            tenant_id = body['tenant_id']
            CreatedById_body= body['CreatedById']
            uuid = body['uuid']
            actionToDo=body['actionToDo']

            if (CreatedById != CreatedById_body):
                return {'statusCode': 403, 'body': json.dumps('Acceso denegado. El usuario no coincide con el creador del incidente.')}
            
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
            print(f"Error en EditIncidentContent para comunidad: {e}")
            return {
                'statusCode': 500,
                'body': json.dumps(f'Error al procesar: {str(e)}')
            }
    else:
        try:
            body = json.loads(event.get('body', '{}'))
            tenant_id = body['tenant_id']
            uuid = body['uuid']
            actionToDo=body['actionToDo']

            if (actionToDo != "AjustarUrgencia"):
                return {
                    'statusCode': 400,
                    'body': json.dumps(f'Acción no permitida para coordinadores: {actionToDo}')
                }
            new_priority = body.get("new_priority")
            if not new_priority:
                return {'statusCode': 400, 'body': json.dumps('Falta "new_priority" para ajustar urgencia')}
            table = dynamodb.Table(INCIDENTS_TABLE)
            try:
                response = table.update_item(
                    Key={
                        'tenant_id': tenant_id,
                        'uuid': uuid
                    },
                    UpdateExpression="SET Priority = :p",
                    ExpressionAttributeValues={
                        ':p': new_priority
                    },
                    ReturnValues="UPDATED_NEW"
                )

                transmission_payload = {
                    'action': 'EditIncidentContent',
                    'uuid': uuid,
                    'status': 'priority_updated'
                }
                transmitir(event, transmission_payload)

                return {
                    'statusCode': 200, 
                    'body': json.dumps({'message': 'Urgencia ajustada', 'updatedAttributes': response.get('Attributes')})
                }
            except Exception as e:
                print(f"Error al actualizar prioridad: {e}")
                return {'statusCode': 500, 'body': json.dumps('Error al actualizar el ítem')}
            
        except Exception as e:
            print(f"Error en EditIncidentContent para coordinadores: {e}")
            return {
                'statusCode': 500,
                'body': json.dumps(f'Error al procesar: {str(e)}')
            }

def StaffChooseIncident(event, context):
    
    connection_id = event['requestContext']['connectionId']
    connections_table = dynamodb.Table(CONNECTIONS_TABLE)
    users_table = dynamodb.Table(USERS_TABLE)
        
    conn_resp = connections_table.get_item(Key={'connectionId': connection_id})
    if 'Item' not in conn_resp:
        return {'statusCode': 403, 'body': json.dumps('Conexión no autorizada. Reconecte.')}
    
    user_role = conn_resp['Item'].get('Role')
    area= conn_resp['Item'].get('Area')
    personal_uuid = conn_resp['Item'].get('CreatedById')

    if (user_role != 'Personal' ):
        return {'statusCode': 403, 'body': json.dumps('Acceso denegado. Solo Personal puede asignarse incidentes.')}
    if not area:
        return {'statusCode': 403, 'body': json.dumps('Acceso denegado. Área no definida para el personal.')}

    try:
        body = json.loads(event.get('body', '{}'))
        if (body['Area'] != area):
            return {'statusCode': 403, 'body': json.dumps('Acceso denegado. El área no coincide con el área del personal.')}
        
        tenant_id = body['tenant_id']
        incident_uuid = body['uuid']

        table = dynamodb.Table(INCIDENTS_TABLE)

        try:
            response = table.update_item(
                Key={
                    'tenant_id': tenant_id,
                    'uuid': incident_uuid
                },
                UpdateExpression="SET AssignedToPersonalId = :pid, Status = :s, ExecutingAt = :ea",
                ExpressionAttributeValues={
                    ':pid': personal_uuid,
                    ':s': 'EnAtencion',
                    ':ea': datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                },
                ReturnValues="UPDATED_NEW"
            )
            new_task = [{
                'tenant_id': tenant_id,
                'uuid': uuid
            }]

            response2 = users_table.update_item(
                Key={
                    'Role': 'Personal',
                    'UUID': personal_uuid
                },
                UpdateExpression="SET #tl = list_append(if_not_exists(#tl, :empty_list), :ia)",
                
                ExpressionAttributeNames={
                    '#tl': 'ToList'
                },
                ExpressionAttributeValues={
                    ':ia': new_task,
                    ':empty_list': []
                },
                ReturnValues="UPDATED_NEW"
            )
            

            transmission_payload = {
                'action': 'StaffChooseIncident',
                'uuid': uuid,
                'status': 'assigned',
                'response': response.get('Attributes')
            }
            transmitir(event, transmission_payload)

            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'Incidente asignado al personal', 'updatedAttributes': response.get('Attributes'), 'userUpdate': response2.get('Attributes')})
            }

        except Exception as e:
            print(f"Error al actualizar: {e}")
            return {'statusCode': 500, 'body': json.dumps('Error al actualizar el ítem')}

    except Exception as e:
        print(f"Error en StaffChooseIncident: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error al procesar: {str(e)}')
        }

def CoordinatorAssignIncident(event,context):
    connection_id = event['requestContext']['connectionId']
    connections_table = dynamodb.Table(CONNECTIONS_TABLE)
    
    conn_resp = connections_table.get_item(Key={'connectionId': connection_id})
    if 'Item' not in conn_resp:
        return {'statusCode': 403, 'body': json.dumps('Conexión no autorizada. Reconecte.')}
    
    user_role = conn_resp['Item'].get('Role')

    if (user_role != 'Coordinadores'):
        return {'statusCode': 403, 'body': json.dumps('Acceso denegado. Solo Coordinadores puede asignar o escribir comentarios en los incidentes.')}

    try:
        body = json.loads(event.get('body', '{}'))
        tenant_id = body['tenant_id']
        incident_uuid = body['uuid']
        actionToDo=body['actionToDo']

        table = dynamodb.Table(INCIDENTS_TABLE)
        user_table=dynamodb.Table(USERS_TABLE)
        
        if (actionToDo!= "Asignar" and actionToDo!="EscribirComentario"):
            return {
                'statusCode': 400,
                'body': json.dumps(f'Acción no permitida para coordinadores: {actionToDo}')
            }
        
        if (actionToDo == "Asignar"):
            try:
                assigned_to_id = body.get('AssignedToPersonalId')
                response = table.update_item(
                    Key={
                        'tenant_id': tenant_id,
                        'uuid': incident_uuid
                    },
                    UpdateExpression="SET AssignedToPersonalId = :pid, Status = :s, ExecutingAt = :ea",
                    ExpressionAttributeValues={
                        ':pid': assigned_to_id,
                        ':s': 'EnAtencion',
                        ':ea': datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                    },
                    ReturnValues="UPDATED_NEW"
                )

                transmission_payload = {
                    'action': 'CoordinatorAssignIncident',
                    'uuid': uuid,
                    'status': 'assigned',
                    'response': response.get('Attributes')
                }

                new_task = [{
                    'tenant_id': tenant_id,
                    'uuid': uuid
                }]

                response2 = user_table.update_item(
                    Key={
                        'Role': 'Personal',
                        'UUID': assigned_to_id
                    },
                    UpdateExpression="SET #tl = list_append(if_not_exists(#tl, :empty_list), :ia)",
                    ExpressionAttributeNames={
                        '#tl': 'ToList'
                    },
                    ExpressionAttributeValues={
                        ':ia': new_task,
                        ':empty_list': []
                    },
                    ReturnValues="UPDATED_NEW"
                )

                transmitir(event, transmission_payload)

                return {
                    'statusCode': 200,
                    'body': json.dumps({'message': 'Incidente asignado por coordinador', 'updatedAttributes': response.get('Attributes'), 'userUpdate': response2.get('Attributes')})
                }

            except Exception as e:
                print(f"Error al actualizar: {e}")
                return {'statusCode': 500, 'body': json.dumps('Error al actualizar el ítem')}
        
        else:
            try:
                new_comment = body.get("new_comment")
                if not new_comment:
                    return {'statusCode': 400, 'body': json.dumps('Falta "new_comment" para escribir comentario')}
                
                new_comment_list = [{
                    'Date': datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    'Comment': new_comment,
                    'Role': 'Coordinadores',
                    'UserId': conn_resp['Item'].get('CreatedById')
                }]

                response = table.update_item(
                    Key={
                        'tenant_id': tenant_id,
                        'uuid': uuid
                    },
                    UpdateExpression="SET #c = list_append(if_not_exists(#c, :empty_list), :c_val)",
                    
                    ExpressionAttributeNames={
                        '#c': 'Comment' 
                    },
                    ExpressionAttributeValues={
                        ':c_val': new_comment_list, 
                        ':empty_list': [] 
                    },
                    ReturnValues="UPDATED_NEW"
                )

                transmission_payload = {
                    'action': 'CoordinatorAssignIncident',
                    'uuid': uuid,
                    'status': 'comment_added',
                    'response': response.get('Attributes')
                }

                transmitir(event, transmission_payload)

                return {
                    'statusCode': 200, 
                    'body': json.dumps({'message': 'Comentario agregado', 'updatedAttributes': response.get('Attributes')})
                }
            except Exception as e:
                print(f"Error al actualizar comentario: {e}")
                return {'statusCode': 500, 'body': json.dumps('Error al actualizar el ítem')}

    except Exception as e:
        print(f"Error en CoordinatorAssignIncident: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error al procesar: {str(e)}')
        }

