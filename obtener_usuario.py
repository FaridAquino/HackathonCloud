


import json
import os
from datetime import datetime

import boto3

dynamodb = boto3.resource("dynamodb")
users_table = dynamodb.Table(os.environ["USERS_TABLE"])
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

        # Con Role y UUID del token, obtener usuario
        role = token_item["Role"]
        uuid = token_item["UUID"]

        user_resp = users_table.get_item(
            Key={
                "Role": role,
                "UUID": uuid
            }
        )

        if "Item" not in user_resp:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Usuario no encontrado"})
            }

        user = user_resp["Item"]
        # No devolver el hash de contraseña
        user.pop("PasswordHash", None)

        return {
            "statusCode": 200,
            "body": json.dumps(user)
        }

    except Exception as e:
        print("Exception:", str(e))
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
