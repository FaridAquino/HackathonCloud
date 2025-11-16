import json
import os
from datetime import datetime, timezone

import boto3

dynamodb = boto3.resource("dynamodb")
users_table = dynamodb.Table(os.environ["USERS_TABLE"])
tokens_table = dynamodb.Table(os.environ["TOKENS_TABLE"])


def parse_iso_to_utc(s: str):
    if s.endswith("Z"):
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _get_authorization_token(headers: dict):
    if not headers:
        return None
    auth = (
        headers.get("Authorization")
        or headers.get("authorization")
        or headers.get("Bearer")
        or headers.get("bearer")
    )
    if not auth:
        return None

    parts = auth.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return auth


def _get_user_from_token(token: str):
    resp = tokens_table.get_item(Key={"Token": token})
    if "Item" not in resp:
        return None

    token_item = resp["Item"]
    expires_at_str = token_item.get("ExpiresAt")
    if not expires_at_str:
        return None

    expires_at = parse_iso_to_utc(expires_at_str)
    now = datetime.now(timezone.utc)
    if now > expires_at:
        return None

    role = token_item["Role"]
    uuid = token_item["UUID"]

    user_resp = users_table.get_item(Key={"Role": role, "UUID": uuid})
    if "Item" not in user_resp:
        return None

    return user_resp["Item"]


def lambda_handler(event, context):
    try:
        # 1. Validar token y que sea AUTHORITY
        headers = event.get("headers") or {}
        token = _get_authorization_token(headers)

        if not token:
            return {
                "statusCode": 401,
                "body": json.dumps({"error": "Missing or invalid Authorization header"})
            }

        auth_user = _get_user_from_token(token)
        if not auth_user:
            return {
                "statusCode": 403,
                "body": json.dumps({"error": "Token inválido o expirado"})
            }

        if auth_user.get("Role") != "AUTHORITY":
            return {
                "statusCode": 403,
                "body": json.dumps({"error": "Solo usuarios con rol AUTHORITY pueden asignar roles y áreas"})
            }

        # 2. Leer body
        body = json.loads(event.get("body") or "{}")

        current_role = body.get("currentRole")
        uuid = body.get("uuid")
        new_role = body.get("newRole")
        new_area = body.get("newArea")

        if not current_role or not uuid:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing required fields: currentRole, uuid"})
            }

        # Normalizar: si vienen strings vacíos, tratarlos como None
        if isinstance(new_role, str) and new_role.strip() == "":
            new_role = None
        if isinstance(new_area, str) and new_area.strip() == "":
            new_area = None

        # newRole y/o newArea: al menos uno debe venir
        if new_role is None and new_area is None:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Debe enviar newRole, newArea o ambos"})
            }

        # 3. Traer usuario objetivo
        user_resp = users_table.get_item(Key={"Role": current_role, "UUID": uuid})
        if "Item" not in user_resp:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Usuario objetivo no encontrado"})
            }

        target_user = user_resp["Item"]
        old_role = current_role

        # CASO A: SOLO CAMBIO DE ÁREA (sin newRole)
        if new_role is None:
            # Aquí newArea sí debe existir (ya validamos al inicio que viene al menos uno)
            if new_area is None:
                return {
                    "statusCode": 400,
                    "body": json.dumps({"error": "No hay cambios para aplicar"})
                }

            users_table.update_item(
                Key={"Role": old_role, "UUID": uuid},
                UpdateExpression="SET #a = :newArea",
                ExpressionAttributeNames={"#a": "Area"},
                ExpressionAttributeValues={":newArea": new_area}
            )

            target_user["Area"] = new_area

            return {
                "statusCode": 200,
                "body": json.dumps({
                    "message": "Área actualizada correctamente",
                    "user": target_user
                })
            }

        # CASO B: newRole viene y es igual al rol actual
        if new_role == old_role:
            # Si no hay newArea, realmente no hay nada que cambiar
            if new_area is None:
                return {
                    "statusCode": 400,
                    "body": json.dumps({"error": "No hay cambios para aplicar (mismo rol y sin nueva área)"})
                }

            users_table.update_item(
                Key={"Role": old_role, "UUID": uuid},
                UpdateExpression="SET #a = :newArea",
                ExpressionAttributeNames={"#a": "Area"},
                ExpressionAttributeValues={":newArea": new_area}
            )

            target_user["Area"] = new_area

            return {
                "statusCode": 200,
                "body": json.dumps({
                    "message": "Área actualizada correctamente (rol sin cambios)",
                    "user": target_user
                })
            }

        # CASO C: CAMBIO DE ROL (newRole distinto al actual)
        new_item = dict(target_user)
        new_item["Role"] = new_role
        if new_area is not None:
            new_item["Area"] = new_area
        new_item["UpdatedAt"] = datetime.now(timezone.utc).isoformat()

        # Insertar nuevo usuario con el nuevo rol
        users_table.put_item(Item=new_item)
        # Borrar el registro con el rol antiguo
        users_table.delete_item(Key={"Role": old_role, "UUID": uuid})

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Rol y área actualizados correctamente",
                "user": new_item
            })
        }

    except Exception as e:
        print("Exception in assign_role_area:", str(e))
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
