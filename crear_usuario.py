
import json
import os
from datetime import datetime, timedelta
import hashlib
import uuid

import boto3

dynamodb = boto3.resource("dynamodb")
users_table = dynamodb.Table(os.environ["USERS_TABLE"])
tokens_table = dynamodb.Table(os.environ["TOKENS_TABLE"])


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")

        role = body.get("role")
        email = body.get("email")
        password = body.get("password")

        # Ahora exigimos role, email y password
        if not role or not email or not password:
            return {
                "statusCode": 400,
                "body": json.dumps({
                    "error": "Missing required fields: role, email, password"
                })
            }

        # UUID generado automáticamente
        generated_uuid = str(uuid.uuid4())
        password_hash = hash_password(password)

        # Crear usuario
        user_item = {
            "Role": role,
            "UUID": generated_uuid,
            "UserId": body.get("userId"),
            "FullName": body.get("fullName"),
            "Email": email,
            "Area": body.get("area"),
            "CommunityCode": body.get("communityCode"),
            "Status": body.get("status", "ACTIVE"),
            "CreatedAt": datetime.utcnow().isoformat(),
            "ToList": body.get("toList", []),
            "PasswordHash": password_hash,
        }

        users_table.put_item(
            Item=user_item,
            ConditionExpression="attribute_not_exists(#r) AND attribute_not_exists(#u)",
            ExpressionAttributeNames={
                "#r": "Role",
                "#u": "UUID"
            }
        )

        # Generar token de acceso inicial (como si hiciera login automático)
        token = str(uuid.uuid4())
        now = datetime.utcnow()
        expires_at = now + timedelta(minutes=60)

        token_item = {
            "Token": token,
            "Email": email,
            "UserId": user_item.get("UserId"),
            "Role": role,
            "UUID": generated_uuid,
            "CreatedAt": now.isoformat(),
            "ExpiresAt": expires_at.isoformat(),
        }

        tokens_table.put_item(Item=token_item)

        return {
            "statusCode": 201,
            "body": json.dumps({
                "message": "Usuario creado correctamente",
                "role": role,
                "uuid": generated_uuid,
                "token": token,
                "expiresAt": expires_at.isoformat()
            })
        }

    except Exception as e:
        print("Exception:", str(e))
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }




