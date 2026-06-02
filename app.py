from flask import Flask, request, jsonify
import requests
import os
import json
import uuid
from datetime import datetime

app = Flask(__name__)

OPENAI_KEY = os.environ.get("OPENAI_KEY")
TODOIST_TOKEN = os.environ.get("TODOIST_TOKEN")
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY")

PROJECT_MAP = {
    "work": "6RH9f45GMC49J67P",
    "home": "6RH9f43CvMjp9Vcp"
}

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

    provided_key = request.headers.get("X-Internal-Key")
    if INTERNAL_API_KEY and provided_key != INTERNAL_API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON body"}), 400

    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "No text provided"}), 400

    try:
        openai_response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-5.4-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a task extraction engine.\n\n"
                            "Extract all actionable tasks from the user input.\n\n"
                            "Return ONLY valid JSON in this format:\n\n"
                            "{\n"
                            '  "tasks": [\n'
                            "    {\n"
                            '      "title": "Short task title",\n'
                            '      "project": "work | home | other",\n'
                            '      "priority": 1-4,\n'
                            '      "labels": []\n'
                            "    }\n"
                            "  ]\n"
                            "}\n\n"
                            "Project rules:\n"
                            "- work = professional tasks, grants, writing, research, clients, meetings\n"
                            "- home = household, maintenance, errands, car, bills\n"
                            "- other = anything else\n\n"
                            "Priority rules:\n"
                            "4 = Urgent\n"
                            "3 = Important\n"
                            "2 = Medium\n"
                            "1 = Low\n\n"
                            "Rules:\n"
                            "- No commentary\n"
                            "- No markdown\n"
                            "- Only valid JSON"
                        )
                    },
                    {
                        "role": "user",
                        "content": text
                    }
                ]
            },
            timeout=20
        )

        if openai_response.status_code != 200:
            return jsonify({"error": "OpenAI failed"}), 500

        content = openai_response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        tasks = parsed.get("tasks", [])

    except Exception as e:
        print(f"[{request_id}] AI parsing failed:", str(e))
        return jsonify({"error": "AI parsing failed"}), 500

    # ✅ Fetch existing tasks for duplicate detection
    existing_response = requests.get(
        "https://api.todoist.com/api/v1/tasks",
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}"}
    )

    existing_titles = set()
    if existing_response.status_code == 200:
        existing_tasks = existing_response.json()
        existing_titles = {
            t["content"].lower().strip()
            for t in existing_tasks
        }

    created = 0
    skipped = 0

    for task in tasks:
        title = task.get("title", "").strip()
        normalized = title.lower()

        if normalized in existing_titles:
            print(f"[{request_id}] Skipping duplicate: {title}")
            skipped += 1
            continue

        project_key = task.get("project", "other").lower()
        project_id = PROJECT_MAP.get(project_key)

        payload = {
            "content": title,
            "priority": task.get("priority", 1)
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

        except Exception as e:
            print(f"[{request_id}] Todoist exception:", str(e))

    print(f"[{request_id}] Created {created}, Skipped {skipped}")

    return jsonify({
        "status": "success",
        "tasks_created": created,
        "tasks_skipped_duplicates": skipped,
        "tasks_parsed": len(tasks)
    })
