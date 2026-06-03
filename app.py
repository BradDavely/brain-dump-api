from flask import Flask, request, jsonify
import requests
import os
import json
import uuid
import re
from datetime import datetime

app = Flask(__name__)

# -----------------------------
# Required Environment Variables
# -----------------------------

REQUIRED_ENV_VARS = [
    "OPENAI_KEY",
    "TODOIST_TOKEN",
    "INTERNAL_API_KEY"
]

missing_env_vars = [key for key in REQUIRED_ENV_VARS if not os.environ.get(key)]

if missing_env_vars:
    raise RuntimeError(
        "Missing required environment variables: "
        + ", ".join(missing_env_vars)
    )

OPENAI_KEY = os.environ.get("OPENAI_KEY")
TODOIST_TOKEN = os.environ.get("TODOIST_TOKEN")
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY")

# Optional:
# You can set OPENAI_MODEL in Railway if you want to control the model there.
# If you do not set it, this default will be used.
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")

PROJECT_MAP = {
    "work": "6RH9f45GMC49J67P",
    "home": "6RH9f43CvMjp9Vcp"
}


# -----------------------------
# Helper Functions
# -----------------------------

def normalize_title(title):
    title = title.lower()
    title = re.sub(r"[^\w\s]", "", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip()


def get_openai_tasks(text, request_id):
    """
    Sends the brain dump text to OpenAI and returns structured task data.
    Uses Structured Outputs so the response must match the JSON schema.
    """

    task_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {
                            "type": "string"
                        },
                        "project": {
                            "type": "string",
                            "enum": ["work", "home", "other"]
                        },
                        "priority": {
                            "type": "integer",
                            "enum": [1, 2, 3, 4]
                        },
                        "labels": {
                            "type": "array",
                            "items": {
                                "type": "string"
                            }
                        }
                    },
                    "required": [
                        "title",
                        "project",
                        "priority",
                        "labels"
                    ]
                }
            }
        },
        "required": ["tasks"]
    }

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": OPENAI_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a task extraction engine. "
                        "Extract all actionable tasks from the user's brain dump. "
                        "Use short, clear task titles. "
                        "Categorize each task as work, home, or other. "
                        "Assign priority using this scale: "
                        "4 = urgent, 3 = important, 2 = medium, 1 = low. "
                        "Only include real actionable tasks. "
                        "Do not include vague thoughts unless they can be turned into a useful task."
                    )
                },
                {
                    "role": "user",
                    "content": text
                }
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "brain_dump_tasks",
                    "strict": True,
                    "schema": task_schema
                }
            }
        },
        timeout=20
    )

    if response.status_code != 200:
        print(f"[{request_id}] OpenAI failed: {response.status_code} {response.text}")
        raise RuntimeError("OpenAI request failed")

    response_json = response.json()
    message = response_json["choices"][0]["message"]

    # Structured Outputs may return a refusal instead of schema content.
    if message.get("refusal"):
        print(f"[{request_id}] OpenAI refusal: {message.get('refusal')}")
        raise RuntimeError("OpenAI refused the request")

    content = message.get("content", "")

    if not content:
        print(f"[{request_id}] OpenAI returned empty content")
        raise RuntimeError("OpenAI returned empty content")

    parsed = json.loads(content)
    return parsed.get("tasks", [])


# -----------------------------
# Routes
# -----------------------------

@app.route("/")
def home():
    return "Brain Dump API is running"


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


@app.route("/braindump", methods=["POST"])
def braindump():
    request_id = str(uuid.uuid4())
    print(f"[{request_id}] Incoming request at {datetime.utcnow().isoformat()}")

    # Secret Key Protection
    provided_key = request.headers.get("X-Internal-Key")

    if provided_key != INTERNAL_API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "Invalid JSON body"}), 400

    text = data.get("text", "").strip()

    if not text:
        return jsonify({"error": "No text provided"}), 400

    # Optional safety limit so huge accidental brain dumps do not create problems.
    if len(text) > 8000:
        return jsonify({"error": "Text too long"}), 400

    # Call OpenAI with Structured Outputs
    try:
        tasks = get_openai_tasks(text, request_id)

    except Exception as e:
        print(f"[{request_id}] AI parsing failed: {str(e)}")
        return jsonify({"error": "AI parsing failed"}), 500

    # Fetch existing Todoist tasks for duplicate checking
    existing_titles = set()

    try:
        existing_response = requests.get(
            "https://api.todoist.com/api/v1/tasks",
            headers={
                "Authorization": f"Bearer {TODOIST_TOKEN}"
            },
            timeout=10
        )

        if existing_response.status_code == 200:
            existing_json = existing_response.json()
            existing_tasks = existing_json.get("results", [])

            for task in existing_tasks:
                title = normalize_title(task.get("content", ""))

                if title:
                    existing_titles.add(title)
        else:
            print(
                f"[{request_id}] Todoist existing task fetch failed: "
                f"{existing_response.status_code} {existing_response.text}"
            )

    except Exception as e:
        print(f"[{request_id}] Todoist existing task fetch error: {str(e)}")

    created = 0
    skipped = 0
    failed = 0

    # Create Todoist tasks
    for task in tasks:
        title = task.get("title", "").strip()

        if not title:
            failed += 1
            continue

        normalized = normalize_title(title)

        if normalized in existing_titles:
            skipped += 1
            continue

        project_key = task.get("project", "other").lower()

        if project_key not in ["work", "home", "other"]:
            project_key = "other"

        project_id = PROJECT_MAP.get(project_key)

        priority = task.get("priority", 1)

        if priority not in [1, 2, 3, 4]:
            priority = 1

        payload = {
            "content": title,
            "priority": priority
        }

        if project_id:
            payload["project_id"] = project_id

        try:
            todoist_response = requests.post(
                "https://api.todoist.com/api/v1/tasks",
                headers={
                    "Authorization": f"Bearer {TODOIST_TOKEN}",
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=10
            )

            if todoist_response.status_code == 200:
                created += 1
                existing_titles.add(normalized)
            else:
                failed += 1
                print(
                    f"[{request_id}] Todoist task creation failed: "
                    f"{todoist_response.status_code} {todoist_response.text}"
                )

        except Exception as e:
            failed += 1
            print(f"[{request_id}] Todoist task creation error: {str(e)}")

    print(f"[{request_id}] Created {created}, Skipped {skipped}, Failed {failed}")

    return jsonify({
        "status": "success",
        "tasks_created": created,
        "tasks_skipped_duplicates": skipped,
        "tasks_failed": failed,
        "tasks_parsed": len(tasks)
    })
