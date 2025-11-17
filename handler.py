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
sns_client = boto3.client('sns')
STATUS_TOPIC_ARN = os.environ.get('INCIDENT_STATUS_TOPIC_ARN')

def publish_incident_status_change(incident: dict, changed_by: dict | None = None):
    """
    Envía un mensaje a SNS cuando cambia el estado de un incidente.
    No rompe el flujo si SNS no está configurado o falla.
    """
    if not STATUS_TOPIC_ARN:
        print("[SNS] INCIDENT_STATUS_TOPIC_ARN no configurado, no se publica evento.")
        return

    try:
        message = {
            "tenant_id": incident.get("tenant_id"),
            "uuid": incident.get("uuid"),
            "title": incident.get("Title"),
            "status": incident.get("Status"),
            "createdById": incident.get("CreatedById"),
            "createdByName": incident.get("CreatedByName"),
            "responsibleArea": incident.get("ResponsibleArea"),
            "priority": incident.get("Priority"),
            "isGlobal": incident.get("IsGlobal"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "changedBy": changed_by or {}
        }

        sns_client.publish(
            TopicArn=STATUS_TOPIC_ARN,
            Subject="IncidentStatusChanged",
            Message=json.dumps(message)
        )
        print("[SNS] Evento de cambio de estado publicado correctamente.")
    except Exception as e:
        print(f"[SNS] Error publicando cambio de estado: {e}")
        

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
        users_table = dynamodb.Table(USERS_TABLE)
    except Exception as e:
        print(f"[Error Transmitir] No se pudieron cargar las tablas: {e}")
        return
    
    try:
        endpoint_url = f"https://{event['requestContext']['domainName']}/{event['requestContext']['stage']}"
        apigateway_client = boto3.client('apigatewaymanagementapi', endpoint_url=endpoint_url)
    except KeyError:
        print(f"[Error Transmitir] El evento no tiene 'requestContext' para el endpoint_url.")
        return

    incident_data = message_payload_dict.get('incident', {})
    incident_responsible_areas = incident_data.get('ResponsibleArea', [])
    incident_is_global = incident_data.get('IsGlobal', False)

    is_deletion_message = not incident_data
    
    if not incident_data:
        print("[Error Transmitir] No se encontró el objeto 'incident' en el payload.")
        return

    try:
        response = connections_table.scan(ProjectionExpression='connectionId, #r, Area, CreatedById',
                                          ExpressionAttributeNames={'#r': 'Role'})
        connections = response.get('Items', [])
    except Exception as e:
        print(f"[Error Transmitir] Fallo al escanear la tabla de conexiones: {e}")
        return

    print(f"Encontradas {len(connections)} conexiones para evaluar.")
    
    message_payload_str = json.dumps(message_payload_dict)

    for connection in connections:
        connection_id = connection['connectionId']
        user_role = connection.get('Role')
        user_area = connection.get('Area')
        
        send_message = False

        if is_deletion_message:
            # Requisito: Solo enviar si el rol es AUTHORITY
            if user_role == 'AUTHORITY':
                send_message = True
        else:

            if user_role == 'AUTHORITY':
                send_message = True
                
            elif user_role == 'PERSONAL' or user_role == 'COORDINATOR':
                if user_area and user_area in incident_responsible_areas:
                    send_message = True
                    
            elif user_role == 'COMMUNITY':
                if incident_is_global is True:
                    send_message = True
        
        if send_message:
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

    if (user_role != 'COMMUNITY'):
        print("Acceso denegado: Solo COMMUNITY puede crear incidentes.")
        return {'statusCode': 403, 'body': json.dumps('Acceso denegado. Solo COMMUNITY puede crear incidentes.')}
    
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
        Status = "Pendiente"

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
            'action': 'NewIncident',
            'incident': incidente
        }
        transmitir(event, transmission_payload)

        publish_incident_status_change(
            incidente,
            changed_by={
                "source": "WebSocket",
                "action": "CreateIncident",
                "role": user_role
            }
        )

        
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

    if (user_role != 'COMMUNITY' and user_role!='COORDINATOR'):
        return {'statusCode': 403, 'body': json.dumps('Acceso denegado. Solo COMMUNITY o COORDINATOR puede editar o eliminar incidentes.')}

    if (user_role=='COMMUNITY'):
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
                        'actionToDo': 'editedIncident',
                        'incident':response.get('Attributes')
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
                        'actionToDo': 'deletedIncident',
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
            print(f"Error en EditIncidentContent para COMMUNITY: {e}")
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
                    'body': json.dumps(f'Acción no permitida para COORDINATOR: {actionToDo}')
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
                    'status': 'priorityUpdated',
                    'incident': response.get('Attributes')
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
            print(f"Error en EditIncidentContent para COORDINATOR: {e}")
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

    if (user_role != 'PERSONAL' ):
        print("Acceso denegado: Solo PERSONAL puede asignarse incidentes.")
        return {'statusCode': 403, 'body': json.dumps('Acceso denegado. Solo PERSONAL puede asignarse incidentes.')}
    if not area:
        print("Acceso denegado: Área no definida para el PERSONAL.")
        return {'statusCode': 403, 'body': json.dumps('Acceso denegado. Área no definida para el PERSONAL.')}

    try:
        body = json.loads(event.get('body', '{}'))
        if (body['Area'] != area):
            print("Acceso denegado: El área no coincide con el área del PERSONAL.")
            return {'statusCode': 403, 'body': json.dumps('Acceso denegado. El área no coincide con el área del PERSONAL.')}
        
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
                'uuid': incident_uuid
            }]

            response2 = users_table.update_item(
                Key={
                    'Role': 'PERSONAL',
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
                'uuid': incident_uuid,
                'actionToDo': 'assigned',
                'incident': response.get('Attributes')
            }
            transmitir(event, transmission_payload)

                # SNS: incidente pasa a EnAtencion (PERSONAL se lo asigna)
            full_incident_resp = table.get_item(
                Key={
                    'tenant_id': tenant_id,
                    'uuid': incident_uuid
                }
            )
            full_incident = full_incident_resp.get('Item')
            if full_incident:
                publish_incident_status_change(
                    full_incident,
                    changed_by={
                        "source": "WebSocket",
                        "action": "StaffChooseIncident",
                        "role": user_role,
                        "personal_uuid": personal_uuid
                    }
                )


            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'Incidente asignado al PERSONAL', 'updatedAttributes': response.get('Attributes'), 'userUpdate': response2.get('Attributes')})
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

    if (user_role != 'COORDINATOR'):
        print("Acceso denegado: Solo COORDINATOR puede asignar o escribir comentarios en los incidentes.")
        return {'statusCode': 403, 'body': json.dumps('Acceso denegado. Solo COORDINATOR puede asignar o escribir comentarios en los incidentes.')}

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
                'body': json.dumps(f'Acción no permitida para COORDINATOR: {actionToDo}')
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

                new_task = [{
                    'tenant_id': tenant_id,
                    'uuid': incident_uuid
                }]

                response2 = user_table.update_item(
                    Key={
                        'Role': 'PERSONAL',
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

                transmission_payload = {
                    'action': 'CoordinatorAssignIncident',
                    'uuid': incident_uuid,
                    'actionToDo': 'assignedIncident',
                    'incident': response.get('Attributes'),
                    'user': response2.get('Attributes')
                }

                transmitir(event, transmission_payload)

                
                # SNS: EnAtencion por coordinador
                full_incident_resp = table.get_item(
                    Key={
                        'tenant_id': tenant_id,
                        'uuid': incident_uuid
                    }
                )
                full_incident = full_incident_resp.get('Item')
                if full_incident:
                    publish_incident_status_change(
                        full_incident,
                        changed_by={
                            "source": "WebSocket",
                            "action": "CoordinatorAssignIncident",
                            "role": user_role,
                            "assigned_to_id": assigned_to_id
                        }
                    )

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
                    'Role': 'COORDINATOR',
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
                    'uuid': incident_uuid,
                    'status': 'comment_added',
                    'incident': response.get('Attributes')
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

def SolvedIncident(event, context):
    connection_id = event['requestContext']['connectionId']
    connections_table = dynamodb.Table(CONNECTIONS_TABLE)
    users_table = dynamodb.Table(USERS_TABLE)
    table = dynamodb.Table(INCIDENTS_TABLE)

    conn_resp = connections_table.get_item(Key={'connectionId': connection_id})

    if 'Item' not in conn_resp:
        return {'statusCode': 403, 'body': json.dumps('Conexión no autorizada. Reconecte.')}
    
    user_role = conn_resp['Item'].get('Role')
    personal_uuid = conn_resp['Item'].get('CreatedById') # (Este es el UUID del usuario conectado)

    if (user_role != 'PERSONAL' and user_role != 'COORDINATOR'):
        return {'statusCode': 403, 'body': json.dumps('Acceso denegado. Solo Personal o COORDINATOR puede resolver incidentes.')}
    
    try:
        body = json.loads(event.get('body', '{}'))
        incidente_uuid = body.get('uuid')
        incidente_tenant_id = body.get('tenant_id')
        
        if not incidente_uuid or not incidente_tenant_id:
            raise ValueError("Faltan 'uuid' o 'tenant_id' en el body")
            
    except Exception as e:
        print(f"Error al parsear body: {e}")
        return {'statusCode': 400, 'body': json.dumps(f'Body mal formado: {str(e)}')}

    if (user_role == 'PERSONAL'):
        try:
            response = table.update_item(
                Key={
                    'tenant_id': incidente_tenant_id,
                    'uuid': incidente_uuid
                },
                UpdateExpression="SET #s = :s, #ra = :ra",
                ExpressionAttributeNames={
                    '#s': 'Status',
                    '#ra': 'ResolvedAt'
                },
                ExpressionAttributeValues={
                    ':s': 'Resuelto',
                    ':ra': datetime.now(timezone.utc).isoformat()
                },
                ReturnValues="UPDATED_NEW"
            )

            response2 = None
            try:
                user_data = users_table.get_item(
                    Key={
                        'Role': 'PERSONAL',
                        'UUID': personal_uuid
                    }
                )

                if 'Item' in user_data and 'ToList' in user_data['Item']:
                    old_list = user_data['Item']['ToList']

                    new_list = [
                        task for task in old_list 
                        if task.get('uuid') != incidente_uuid
                    ]
                    
                    response2 = users_table.update_item(
                        Key={
                            'Role': 'PERSONAL',
                            'UUID': personal_uuid
                        },
                        UpdateExpression="SET #tl = :nl",
                        ExpressionAttributeNames={'#tl': 'ToList'},
                        ExpressionAttributeValues={':nl': new_list},
                        ReturnValues="UPDATED_NEW"
                    )

            except Exception as e:
                print(f"Error al actualizar la ToList del PERSONAL: {e}")

            transmission_payload = {
                'action': 'EditIncidentContent',
                'uuid': incidente_uuid,
                'status': 'SolvedIncident',
                'incident': response.get('Attributes'),
                'user': response2.get('Attributes') if response2 else None
            }
            transmitir(event, transmission_payload)

            # SNS: Resuelto por PERSONAL
            full_incident_resp = table.get_item(
                Key={
                    'tenant_id': incidente_tenant_id,
                    'uuid': incidente_uuid
                }
            )
            full_incident = full_incident_resp.get('Item')
            if full_incident:
                publish_incident_status_change(
                    full_incident,
                    changed_by={
                        "source": "WebSocket",
                        "action": "SolvedIncident",
                        "role": user_role,
                        "personal_uuid": personal_uuid
                    }
                )

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Incidente marcado como resuelto', 
                    'response': response.get('Attributes'), 
                    'userUpdate': response2.get('Attributes') if response2 else None
                })
            }

        except Exception as e:
            print(f"Error en SolvedIncident para PERSONAL: {e}")
            return {'statusCode': 500, 'body': json.dumps(f'Error al procesar: {str(e)}')}

    else:
        try:
            new_comment = body.get("new_comment")
            if not new_comment:
                return {'statusCode': 400, 'body': json.dumps('Falta "new_comment" para escribir comentario')}
                
            new_comment_list = [{
                'Date': datetime.now(timezone.utc).isoformat(),
                'Comment': new_comment,
                'Role': 'COORDINATOR',
                'UserId': conn_resp['Item'].get('CreatedById')
            }]

            response = table.update_item(
                Key={
                    'tenant_id': incidente_tenant_id,
                    'uuid': incidente_uuid
                },
                UpdateExpression="SET #c = list_append(if_not_exists(#c, :empty_list), :c_val)",
                ExpressionAttributeNames={'#c': 'Comment'},
                ExpressionAttributeValues={
                    ':c_val': new_comment_list, 
                    ':empty_list': [] 
                },
                ReturnValues="UPDATED_NEW"
            )
                
            transmission_payload = {
                'action': 'EditIncidentContent',
                'uuid': incidente_uuid,
                'status': 'SolvedIncident',
                'incident': response.get('Attributes')
            }
            transmitir(event, transmission_payload)

            return {
                'statusCode': 200, 
                'body': json.dumps({'message': 'Comentario agregado al incidente resuelto', 'updatedAttributes': response.get('Attributes')})
            }

        except Exception as e:
            print(f"Error en SolvedIncident para COORDINATOR: {e}")
            return {'statusCode': 500, 'body': json.dumps(f'Error al procesar: {str(e)}')}

def AuthorityManageIncidents(event,context):
    connection_id = event['requestContext']['connectionId']
    connections_table = dynamodb.Table(CONNECTIONS_TABLE)
    
    conn_resp = connections_table.get_item(Key={'connectionId': connection_id})
    if 'Item' not in conn_resp:
        return {'statusCode': 403, 'body': json.dumps('Conexión no autorizada. Reconecte.')}
    
    user_role = conn_resp['Item'].get('Role')

    if (user_role != 'COORDINATOR'):
        return {'statusCode': 403, 'body': json.dumps('Acceso denegado. Solo COORDINATOR puede cerrar incidentes.')}

    try:
        body = json.loads(event.get('body', '{}'))
        incidente_tenant_id = body['tenant_id']
        incidente_uuid = body['uuid']
        actionToDo=body['actionToDo']
        table = dynamodb.Table(INCIDENTS_TABLE)

        if (actionToDo!= "Reasignar" and actionToDo!="Cerrar"):
            return {
                'statusCode': 400,
                'body': json.dumps(f'Acción no permitida para COORDINATOR: {actionToDo}')
            }
        
        if (actionToDo == "Reasignar"):
            try:
                response = table.update_item(
                    Key={
                        'tenant_id': incidente_tenant_id,
                        'uuid': incidente_uuid
                    },
                    UpdateExpression="SET PendienteReasignacion = :p",
                    ExpressionAttributeValues={
                        ':p': True
                    },
                    ReturnValues="UPDATED_NEW"
                )

                transmission_payload = {
                    'action': 'AuthorityManageIncidents',
                    'uuid': incidente_uuid,
                    'actionToDo': 'ReassignmentPending',
                    'incident': response.get('Attributes')
                }
                transmitir(event, transmission_payload)

                return {
                    'statusCode': 200,
                    'body': json.dumps({'message': 'Incidente pendiente de reasignación por coordinador', 'updatedAttributes': response.get('Attributes')})
                }

            except Exception as e:
                print(f"Error al actualizar: {e}")
                return {'statusCode': 500, 'body': json.dumps('Error al actualizar el ítem')}
        else:
            try:
                response = table.update_item(
                    Key={
                        'tenant_id': incidente_tenant_id,
                        'uuid': incidente_uuid
                    },
                    UpdateExpression="SET #s = :s, #ra = :ra", 
                    ExpressionAttributeNames={
                        '#s': 'Status',
                        '#ra': 'ResolvedAt' 
                    },
                    ExpressionAttributeValues={
                        ':s': 'Resuelto',
                        ':ra': datetime.now(timezone.utc).isoformat() 
                    },
                    ReturnValues="UPDATED_NEW"
                )
                transmission_payload = {
                    'action': 'AuthorityManageIncidents',
                    'uuid': incidente_uuid,
                    'actionToDo': 'ClosedIncident',
                    'incident': response.get('Attributes')
                }
                transmitir(event, transmission_payload)

                # SNS: Resuelto/Cerrado por COORDINATOR
                full_incident_resp = table.get_item(
                    Key={
                        'tenant_id': incidente_tenant_id,
                        'uuid': incidente_uuid
                    }
                )
                full_incident = full_incident_resp.get('Item')
                if full_incident:
                    publish_incident_status_change(
                        full_incident,
                        changed_by={
                            "source": "WebSocket",
                            "action": "AuthorityManageIncidents",
                            "role": user_role
                        }
                    )

            except Exception as e:
                print(f"Error al actualizar: {e}")
                return {'statusCode': 500, 'body': json.dumps('Error al actualizar el ítem')}

    except Exception as e:
        print(f"Error en AuthorityManageIncidents: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error al procesar: {str(e)}')
        }

