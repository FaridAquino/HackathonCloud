import json
import os
from datetime import datetime

import boto3

dynamodb = boto3.resource("dynamodb")
tokens_table = dynamodb.Table(os.environ["TOKENS_TABLE"])


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        token = body.get("token")

        if not token:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing field: token"})
            }

        resp = tokens_table.get_item(Key={"Token": token})

        if "Item" not in resp:
            return {
                "statusCode": 403,
                "body": json.dumps({"error": "Token no existe"})
            }

        item = resp["Item"]
        expires_at_str = item.get("ExpiresAt")

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

        # Token válido: puedes devolver datos básicos
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Token válido",
                "userId": item.get("UserId"),
                "role": item.get("Role"),
                "uuid": item.get("UUID")
            })
        }

    except Exception as e:
        print("Exception:", str(e))
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
