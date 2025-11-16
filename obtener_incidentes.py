

import json
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Attr

dynamodb = boto3.resource("dynamodb")
users_table = dynamodb.Table(os.environ["USERS_TABLE"])
tokens_table = dynamodb.Table(os.environ["TOKENS_TABLE"])
incidents_table = dynamodb.Table(os.environ["INCIDENTS_TABLE"])

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

        status_filter = params.get("status")          # Pendiente/EnAtencion/Resuelto, etc.
        priority_filter = params.get("priority")      # urgencia
        area_filter = params.get("area")              # permitir filtrar por área (autoridades)
        global_param = params.get("global")           # "true" / "false"
        type_filter = params.get("type")              # si luego agregas campo Type
        min_wait = params.get("minWaitMinutes")
        max_wait = params.get("maxWaitMinutes")
        tenant_id_param = params.get("tenant_id")     # opcional

        filter_expr = None

        def add_condition(current, new):
            if current is None:
                return new
            return current & new

        # ---------- Reglas por rol ----------
        if role == "COMMUNITY":
            # 1. usuario ve sus incidentes
            filter_expr = Attr("CreatedById").eq(user_id)

            # 8. tablero de avisos globales: si ?global=true, solo globales;
            # si no, podría ser responsabilidad del front llamar aparte.
            if global_param and global_param.lower() == "true":
                filter_expr = Attr("IsGlobal").eq(True)

        elif role == "COORDINATOR":
            # 2. coordinador ve incidentes de su área
            filter_expr = Attr("ResponsibleArea").eq(user_area)

        elif role == "PERSONAL":
            # 5. personal ve incidentes de su área o asignados a su nombre
            filter_expr = (
                Attr("ResponsibleArea").eq(user_area)
                | Attr("AssignedToPersonalId").eq(user_id)
            )

        elif role == "AUTHORITY":
            # 6. autoridades pueden ver todos: filter_expr se deja en None
            filter_expr = None

        # ---------- Filtros adicionales comunes ----------

        if status_filter:
            filter_expr = add_condition(filter_expr, Attr("Status").eq(status_filter))

        if priority_filter:
            filter_expr = add_condition(filter_expr, Attr("Priority").eq(priority_filter))

        if type_filter:
            # si en tu item agregas 'Type' o 'SubType', aquí lo usas
            filter_expr = add_condition(filter_expr, Attr("Type").eq(type_filter))

        if area_filter:
            filter_expr = add_condition(filter_expr, Attr("ResponsibleArea").eq(area_filter))

        if global_param:
            bool_global = global_param.lower() == "true"
            filter_expr = add_condition(filter_expr, Attr("IsGlobal").eq(bool_global))

        if tenant_id_param:
            filter_expr = add_condition(filter_expr, Attr("tenant_id").eq(tenant_id_param))

        scan_kwargs = {}
        if filter_expr is not None:
            scan_kwargs["FilterExpression"] = filter_expr

        # ---------- Scan de la tabla de incidentes ----------
        items = []
        resp = incidents_table.scan(**scan_kwargs)
        items.extend(resp.get("Items", []))

        while "LastEvaluatedKey" in resp:
            resp = incidents_table.scan(
                ExclusiveStartKey=resp["LastEvaluatedKey"], **scan_kwargs
            )
            items.extend(resp.get("Items", []))


        def compute_wait_minutes(incident):
            created_at_str = incident.get("CreatedAt")
            if not created_at_str:
                return None
            try:
                created_dt = parse_iso_to_utc(created_at_str)
                now_local = datetime.now(timezone.utc)
                diff = now_local - created_dt
                return diff.total_seconds() / 60.0
            except Exception:
                return None


        filtered_items = []
        for inc in items:
            wait_min = compute_wait_minutes(inc)
            if wait_min is not None:
                inc["WaitingMinutes"] = round(wait_min, 1)

            # aplicar min/max wait si se enviaron
            if min_wait is not None or max_wait is not None:
                if wait_min is None:
                    continue
                if min_wait is not None and wait_min < float(min_wait):
                    continue
                if max_wait is not None and wait_min > float(max_wait):
                    continue

            filtered_items.append(inc)

        filtered_items.sort(key=lambda x: x.get("CreatedAt", ""), reverse=True)

        return {
            "statusCode": 200,
            "body": json.dumps(filtered_items)
        }

    except Exception as e:
        print("Exception in obtener_incidentes:", str(e))
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }

