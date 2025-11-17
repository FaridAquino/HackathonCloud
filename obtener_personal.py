import json
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr

dynamodb = boto3.resource("dynamodb")
users_table = dynamodb.Table(os.environ["USERS_TABLE"])
tokens_table = dynamodb.Table(os.environ["TOKENS_TABLE"])

def parse_iso_to_utc(s: str) -> datetime:
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

    # Si viene como "Bearer xxx"
    parts = auth.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]

    # Si viene solo el token
    return auth


def _get_user_from_token(token: str):
    """Valida token y devuelve (user, token_item) o (None, None)."""
    token_resp = tokens_table.get_item(Key={"Token": token})
    if "Item" not in token_resp:
        return None, None

    token_item = token_resp["Item"]
    expires_at_str = token_item.get("ExpiresAt")
    if not expires_at_str:
        return None, None
    expires_at = parse_iso_to_utc(expires_at_str)
    now = datetime.now(timezone.utc)
    if now > expires_at:
        return None, None

    # Obtener usuario por Role + UUID
    role = token_item["Role"]
    uuid = token_item["UUID"]

    user_resp = users_table.get_item(Key={"Role": role, "UUID": uuid})
    if "Item" not in user_resp:
        return None, None

    return user_resp["Item"], token_item


def lambda_handler(event, context):
    try:
        headers = event.get("headers") or {}
        token = _get_authorization_token(headers)

        if not token:
            return {
                "statusCode": 401,
                "body": json.dumps({"error": "Missing or invalid Authorization header"})
            }

        user, token_item = _get_user_from_token(token)
        if not user:
            return {
                "statusCode": 403,
                "body": json.dumps({"error": "Token inválido o expirado"})
            }

        role = user["Role"]
        user_id = user.get("UUID")
        user_area = user.get("Area")

        # Query params para filtros
        params = event.get("queryStringParameters") or {}

        area_filter = params.get("area")              # filtrar por área específica
        role_filter = params.get("role")              # filtrar por rol (PERSONAL, COORDINATOR, etc.)
        status_filter = params.get("status")          # si hay campo de estado activo/inactivo

        filter_expr = None

        def add_condition(current, new):
            if current is None:
                return new
            return current & new

        # ---------- Reglas por rol ----------
        if role == "COMMUNITY":
            # COMMUNITY no puede ver personal
            return {
                "statusCode": 403,
                "body": json.dumps({"error": "Acceso denegado: COMMUNITY no puede consultar personal"})
            }

        elif role == "COORDINATOR":
            # Coordinador ve personal de su área
            filter_expr = Attr("Area").eq(user_area)

        elif role == "PERSONAL":
            # Personal ve otros personal de su área
            filter_expr = Attr("Area").eq(user_area)

        elif role == "AUTHORITY":
            # Autoridades pueden ver todo el personal: filter_expr se deja en None
            filter_expr = None

        # ---------- Filtros adicionales comunes ----------

        if area_filter:
            filter_expr = add_condition(filter_expr, Attr("Area").eq(area_filter))

        if role_filter:
            filter_expr = add_condition(filter_expr, Attr("Role").eq(role_filter))

        if status_filter:
            # Si existe campo de estado en usuarios
            filter_expr = add_condition(filter_expr, Attr("Status").eq(status_filter))

        scan_kwargs = {}
        if filter_expr is not None:
            scan_kwargs["FilterExpression"] = filter_expr

        # ---------- Scan de la tabla de usuarios ----------
        items = []
        resp = users_table.scan(**scan_kwargs)
        items.extend(resp.get("Items", []))

        while "LastEvaluatedKey" in resp:
            resp = users_table.scan(
                ExclusiveStartKey=resp["LastEvaluatedKey"], **scan_kwargs
            )
            items.extend(resp.get("Items", []))

        # Filtrar solo roles de personal (PERSONAL, COORDINATOR)
        # y remover información sensible
        filtered_items = []
        for item in items:
            user_role = item.get("Role", "")
            if user_role in ["PERSONAL", "COORDINATOR"]:
                # Remover información sensible
                item.pop("PasswordHash", None)
                filtered_items.append(item)

        # Ordenar por nombre si existe
        filtered_items.sort(key=lambda x: x.get("Name", x.get("UUID", "")))

        return {
            "statusCode": 200,
            "body": json.dumps(filtered_items, default=str)
        }

    except Exception as e:
        print("Exception in obtener_personal:", str(e))
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }