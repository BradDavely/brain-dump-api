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
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

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


def get_priority_value(priority):
    """
    Keeps priority safe.
    Your app uses:
    4 = urgent
    3 = important
    2 = medium
    1 = low
    """

    try:
        priority = int(priority)
    except Exception:
        return 1

    if priority not in [1, 2, 3, 4]:
        return 1

    return priority


def dedupe_parsed_tasks(tasks, request_id):
    """
    Removes duplicate tasks returned by OpenAI before anything is sent to Todoist.

    If the same normalized title appears more than once, this keeps one version.
    It keeps the highest priority version, since duplicate AI output may rank
    the same task differently.
    """

    deduped = {}

    for task in tasks:
        title = task.get("title", "").strip()

        if not title:
            print(f"[{request_id}] Dropping parsed task with empty title: {task}")
            continue

        normalized = normalize_title(title)

        if not normalized:
            print(f"[{request_id}] Dropping parsed task with invalid normalized title: {task}")
            continue

        priority = get_priority_value(task.get("priority", 1))

        cleaned_task = {
            "title": title,
            "project": task.get("project", "other"),
            "priority": priority,
            "labels": task.get("labels", [])
        }

        if normalized not in deduped:
            deduped[normalized] = cleaned_task
            continue

        existing_priority = get_priority_value(deduped[normalized].get("priority", 1))

        if priority > existing_priority:
            print(
                f"[{request_id}] Duplicate parsed task found. "
                f"Keeping higher priority version for: '{title}'"
            )
            deduped[normalized] = cleaned_task
        else:
            print(
                f"[{request_id}] Duplicate parsed task removed: '{title}'"
            )

    return list(deduped.values())


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
                        "Extract actionable tasks from the user's brain dump. "
                        "Use short, clear task titles. "
                        "Categorize each task as work, home, or other. "
                        "Assign priority using this scale: "
                        "4 = urgent, 3 = important, 2 = medium, 1 = low. "
                        "Only include real actionable tasks. "
                        "Do not include vague thoughts unless they can be turned into a useful task. "
                        "Important duplicate rule: if the same task appears more than once, "
                        "return it only one time. If duplicate versions seem to have different urgency, "
                        "keep the highest priority version only."
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

    if message.get("refusal"):
        print(f"[{request_id}] OpenAI refusal: {message.get('refusal')}")
        raise RuntimeError("OpenAI refused the request")

    content = message.get("content", "")

    if not content:
        print(f"[{request_id}] OpenAI returned empty content")
        raise RuntimeError("OpenAI returned empty content")

    parsed = json.loads(content)
    raw_tasks = parsed.get("tasks", [])

    print(f"[{request_id}] OpenAI returned {len(raw_tasks)} raw task(s)")

    deduped_tasks = dedupe_parsed_tasks(raw_tasks, request_id)

    print(f"[{request_id}] After parsed-task dedupe: {len(deduped_tasks)} task(s)")

    return deduped_tasks


def fetch_existing_todoist_titles(request_id):
    """
    Fetches all active Todoist task titles using cursor-based pagination.
    This helps prevent duplicates even when the account has more than one page of tasks.
    """

    existing_titles = set()
    cursor = None
    page_count = 0

    while True:
        params = {
            "limit": 200
        }

        if cursor:
            params["cursor"] = cursor

        try:
            response = requests.get(
                "https://api.todoist.com/api/v1/tasks",
                headers={
                    "Authorization": f"Bearer {TODOIST_TOKEN}"
                },
                params=params,
                timeout=10
            )

            if response.status_code != 200:
                print(
                    f"[{request_id}] Todoist existing task fetch failed: "
                    f"{response.status_code} {response.text}"
                )
                break

            response_json = response.json()
            page_count += 1

            existing_tasks = response_json.get("results", [])

            for task in existing_tasks:
                title = normalize_title(task.get("content", ""))

                if title:
                    existing_titles.add(title)

            cursor = response_json.get("next_cursor")

            if not cursor:
                break

        except Exception as e:
            print(f"[{request_id}] Todoist existing task fetch error: {str(e)}")
            break

    print(
        f"[{request_id}] Loaded {len(existing_titles)} existing Todoist task title(s) "
        f"from {page_count} page(s)"
    )

    return existing_titles


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
        print(f"[{request_id}] Unauthorized request blocked")
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True)

    if not data:
        print(f"[{request_id}] Invalid JSON body")
        return jsonify({"error": "Invalid JSON body"}), 400

    text = data.get("text", "").strip()

    if not text:
        print(f"[{request_id}] No text provided")
        return jsonify({"error": "No text provided"}), 400

    # Optional safety limit so huge accidental brain dumps do not create problems.
    if len(text) > 8000:
        print(f"[{request_id}] Text too long: {len(text)} characters")
        return jsonify({"error": "Text too long"}), 400

    # Call OpenAI with Structured Outputs
    try:
        tasks = get_openai_tasks(text, request_id)

    except Exception as e:
        print(f"[{request_id}] AI parsing failed: {str(e)}")
        return jsonify({"error": "AI parsing failed"}), 500

    print(f"[{request_id}] Ready to process {len(tasks)} deduped task(s)")

    # Fetch all existing active Todoist tasks using pagination
    existing_titles = fetch_existing_todoist_titles(request_id)

    created = 0
    skipped = 0
    failed = 0

    # Create Todoist tasks
    for task in tasks:
        title = task.get("title", "").strip()

        if not title:
            failed += 1
            print(f"[{request_id}] Skipped task because title was empty: {task}")
            continue

        normalized = normalize_title(title)

        if normalized in existing_titles:
            skipped += 1
            print(f"[{request_id}] Skipped duplicate task already in Todoist: '{title}'")
            continue

        project_key = task.get("project", "other").lower()

        if project_key not in ["work", "home", "other"]:
            print(
                f"[{request_id}] Invalid project '{project_key}' for task '{title}'. "
                "Defaulting to other."
            )
            project_key = "other"

        project_id = PROJECT_MAP.get(project_key)

        priority = get_priority_value(task.get("priority", 1))

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

            if todoist_response.status_code in [200, 201]:
                created += 1

                # Add it immediately so another matching parsed task in this same run cannot be posted.
                existing_titles.add(normalized)

                print(
                    f"[{request_id}] Created Todoist task: '{title}' "
                    f"with priority {priority}"
                )

            else:
                failed += 1
                print(
                    f"[{request_id}] Todoist task creation failed for '{title}': "
                    f"{todoist_response.status_code} {todoist_response.text}"
                )
                print(f"[{request_id}] Failed payload for '{title}': {json.dumps(payload)}")

        except Exception as e:
            failed += 1
            print(f"[{request_id}] Todoist task creation error for '{title}': {str(e)}")
            print(f"[{request_id}] Failed payload for '{title}': {json.dumps(payload)}")

    print(f"[{request_id}] Created {created}, Skipped {skipped}, Failed {failed}")

    return jsonify({
        "status": "success",
        "tasks_created": created,
        "tasks_skipped_duplicates": skipped,
        "tasks_failed": failed,
        "tasks_parsed": len(tasks)
    })
