import json
import urllib.request
import urllib.error
from datetime import datetime
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.views.decorators.csrf import csrf_exempt

from .dbcollection import global_db, profile_collection

# MongoDB Collections
push_tokens_collection = global_db["backend_diagnostics_push_tokens"]
notifications_collection = global_db["milestone_backend_notification"]

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def send_expo_push_notification(push_tokens, title, body, data=None):
    """
    Sends push notification via Expo HTTP API.
    push_tokens can be a single token string or a list of token strings.
    """
    if isinstance(push_tokens, str):
        push_tokens = [push_tokens]

    valid_tokens = [t for t in push_tokens if t and isinstance(t, str) and t.startswith("ExponentPushToken")]
    if not valid_tokens:
        return {"success": False, "error": "No valid Expo push tokens provided"}

    messages = []
    for token in valid_tokens:
        msg = {
            "to": token,
            "sound": "default",
            "title": title,
            "body": body,
            "data": data or {},
            "priority": "high",
        }
        messages.append(msg)

    try:
        req_data = json.dumps(messages).encode('utf-8')
        req = urllib.request.Request(
            EXPO_PUSH_URL,
            data=req_data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode('utf-8')
            res_json = json.loads(resp_body)
            return {"success": True, "response": res_json}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8') if e.fp else str(e)
        return {"success": False, "error": f"HTTP Error {e.code}: {err_body}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@api_view(['POST'])
@csrf_exempt
@permission_classes([AllowAny])
def register_push_token(request):
    """
    POST: Register or update Expo Push Token for a sample collector / user
    Payload: { "employeeId": "...", "expoPushToken": "...", "employeeName": "..." }
    """
    try:
        employee_id = request.data.get("employeeId") or request.data.get("employee_id")
        employee_name = request.data.get("employeeName") or request.data.get("employee_name") or request.data.get("sampleCollector")
        push_token = request.data.get("expoPushToken") or request.data.get("push_token")

        if not push_token:
            return Response(
                {"error": "expoPushToken is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        doc = {
            "expo_push_token": push_token,
            "updated_at": datetime.now()
        }
        if employee_id:
            doc["employee_id"] = str(employee_id)
        if employee_name:
            doc["employee_name"] = str(employee_name)

        # Upsert into push_tokens_collection by employee_id or employee_name
        query = {}
        if employee_id:
            query["employee_id"] = str(employee_id)
        elif employee_name:
            query["employee_name"] = str(employee_name)
        else:
            query["expo_push_token"] = push_token

        push_tokens_collection.update_one(
            query,
            {"$set": doc},
            upsert=True
        )

        # Also update profile_collection if matching profile found
        if employee_id:
            profile_collection.update_many(
                {"$or": [{"employeeId": str(employee_id)}, {"employee_id": str(employee_id)}]},
                {"$set": {"expo_push_token": push_token}}
            )

        return Response(
            {"message": "Push token registered successfully", "token": push_token},
            status=status.HTTP_200_OK
        )
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def get_push_tokens_for_collector(collector_name_or_id):
    """
    Fetches push token(s) for a given collector name or employee ID.
    """
    tokens = set()
    if not collector_name_or_id:
        return list(tokens)

    search_str = str(collector_name_or_id).strip()

    # Search in push_tokens_collection
    docs = list(push_tokens_collection.find({
        "$or": [
            {"employee_id": {"$regex": f"^{search_str}$", "$options": "i"}},
            {"employee_name": {"$regex": f"^{search_str}$", "$options": "i"}},
            {"expo_push_token": {"$exists": True}}
        ]
    }))

    for d in docs:
        tok = d.get("expo_push_token")
        if tok and tok.startswith("ExponentPushToken"):
            emp_id = str(d.get("employee_id", ""))
            emp_name = str(d.get("employee_name", ""))
            if search_str.lower() in [emp_id.lower(), emp_name.lower()]:
                tokens.add(tok)

    # Search in profile_collection if empty
    if not tokens:
        profiles = list(profile_collection.find({
            "$or": [
                {"employeeId": {"$regex": f"^{search_str}$", "$options": "i"}},
                {"employee_id": {"$regex": f"^{search_str}$", "$options": "i"}},
                {"name": {"$regex": f"^{search_str}$", "$options": "i"}}
            ]
        }))
        for p in profiles:
            tok = p.get("expo_push_token")
            if tok and tok.startswith("ExponentPushToken"):
                tokens.add(tok)

    # Fallback: if single device registered during development/testing
    if not tokens:
        all_docs = list(push_tokens_collection.find({"expo_push_token": {"$exists": True}}).limit(5))
        for d in all_docs:
            tok = d.get("expo_push_token")
            if tok and tok.startswith("ExponentPushToken"):
                tokens.add(tok)

    return list(tokens)


def queue_task_assigned_notification(collector, task_id, clinical_name=""):
    """
    Creates a PENDING notification record when a task is assigned.
    Also attempts immediate push if token exists.
    """
    try:
        title = "New Task Assigned"
        body = f"Task #{task_id} at {clinical_name or 'designated location'} has been assigned to you."
        data = {
            "task_id": task_id,
            "type": "TASK_ASSIGNED",
            "clinicalname": clinical_name,
        }

        notification_doc = {
            "task_id": task_id,
            "sample_collector": collector,
            "title": title,
            "body": body,
            "data": data,
            "status": "PENDING",
            "created_at": datetime.now(),
            "sent_at": None,
            "error": None
        }

        res = notifications_collection.insert_one(notification_doc)
        notification_id = res.inserted_id

        # Try immediate send
        tokens = get_push_tokens_for_collector(collector)
        if tokens:
            result = send_expo_push_notification(tokens, title, body, data)
            if result.get("success"):
                notifications_collection.update_one(
                    {"_id": notification_id},
                    {"$set": {"status": "SENT", "sent_at": datetime.now()}}
                )
            else:
                notifications_collection.update_one(
                    {"_id": notification_id},
                    {"$set": {"error": result.get("error")}}
                )

        return notification_id
    except Exception as e:
        print(f"Error queuing notification: {e}")
        return None


def process_pending_notifications():
    """
    Polls MongoDB for pending notifications in milestone_backend_notification,
    fetches target collector push token, and sends via Expo API.
    Returns count of successfully processed notifications.
    """
    processed_count = 0
    try:
        pending_items = list(notifications_collection.find({"status": "PENDING"}))
        for item in pending_items:
            doc_id = item.get("_id")
            collector = item.get("sample_collector")
            title = item.get("title", "Task Notification")
            body = item.get("body", "You have a new update.")
            data = item.get("data", {})

            tokens = get_push_tokens_for_collector(collector)
            if not tokens:
                notifications_collection.update_one(
                    {"_id": doc_id},
                    {"$set": {"error": f"No Expo push token found for collector: {collector}"}}
                )
                continue

            result = send_expo_push_notification(tokens, title, body, data)
            if result.get("success"):
                notifications_collection.update_one(
                    {"_id": doc_id},
                    {"$set": {"status": "SENT", "sent_at": datetime.now(), "error": None}}
                )
                processed_count += 1
            else:
                notifications_collection.update_one(
                    {"_id": doc_id},
                    {"$set": {"status": "FAILED", "error": result.get("error")}}
                )

    except Exception as e:
        print(f"Error in process_pending_notifications: {e}")

    return processed_count
