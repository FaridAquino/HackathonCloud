import json
import os
from datetime import datetime

import boto3

dynamodb = boto3.resource("dynamodb")
incidents_table = dynamodb.Table(os.environ["DYNAMO_TABLE"])
tokens_table = dynamodb.Table(os.environ["TOKENS_TABLE"])


def _get_authorization_token(headers: dict) -> str | None:
    if not headers:
        return None

    # HttpApi puede enviar headers con mayúsculas/minúsculas distintas
    auth = headers.get("Authorization") or headers.get("authorization")
    if not auth:
        return None

    parts = auth.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def lambda_handler(event, context):
    try:
        headers = event.get("headers") or {}
        token = _get_authorization_token(headers)

        if not token:
            return {
                "statusCode": 401,
                "body": json.dumps({"error": "Missing or invalid Authorization header"})
            }

        # Buscar token
        token_resp = tokens_table.get_item(Key={"Token": token})

        if "Item" not in token_resp:
            return {
                "statusCode": 403,
                "body": json.dumps({"error": "Token no existe"})
            }

        token_item = token_resp["Item"]
        expires_at_str = token_item.get("ExpiresAt")

        if not expires_at_str:
            return {
                "statusCode": 403,
                "body": json.dumps({"error": "Token sin fecha de expiración"})
            }

        expires_at = datetime.fromisoformat(expires_at_str)
        now = datetime.utcnow()

        if now > expires_at:
            return {
                "statusCode": 403,
                "body": json.dumps({"error": "Token expirado"})
            }

        # Obtener parámetros de query string
        query_params = event.get("queryStringParameters") or {}
        tenant_id = query_params.get("tenant_id")
        uuid = query_params.get("uuid")

        # Validar parámetros requeridos
        if not tenant_id or not uuid:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "tenant_id y uuid son parámetros requeridos"})
            }

        # Buscar incidente específico en DynamoDB
        incident_resp = incidents_table.get_item(
            Key={
                "tenant_id": tenant_id,
                "uuid": uuid
            }
        )

        if "Item" not in incident_resp:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Incidente no encontrado"})
            }

        incident = incident_resp["Item"]

        return {
            "statusCode": 200,
            "body": json.dumps(incident, default=str)  # default=str para manejar tipos como datetime
        }

    except Exception as e:
        print("Exception:", str(e))
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }