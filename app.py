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

# ✅ Project Routing Map
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

    # ✅ Secret Key Protection
    provided_key = request.headers.get("X-Internal-Key")
    if INTERNAL_API_KEY and provided_key != INTERNAL_API_KEY:
        print(f"[{request_id}] Unauthorized attempt")
        return jsonify({"error": "Unauthorized"}), 401

    if not OPENAI_KEY or not TODOIST_TOKEN:
        return jsonify({"error": "Server configuration error"}), 500

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
    "content": """
You are a task extraction engine.

Extract all actionable tasks from the user input.

Return ONLY valid JSON in this format:

{
  "tasks": [
    {
      "title": "Short task title",
      "project": "work | home | other",
      "priority": 1-4,
      "labels": []
    }
  ]
}

Project rules:
- work = professional tasks, clients, research, writing, grants, emails, meetings, job-related items
- home = household, maintenance, errands, car, bills, chores, family
- other = anything that does not clearly fit work or home

Priority rules:
4 = Urgent or time-sensitive
3 = Important
2 = Medium
1 = Low

Rules:
- No commentary
- No markdown
- Only valid JSON
"""
}},
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

    created = 0

    for task in tasks:
        try:
            project_key = task.get("project", "other").lower()
            project_id = PROJECT_MAP.get(project_key)

            payload = {
                "content": task.get("title"),
                "priority": task.get("priority", 1)
            }

            # ✅ Only add project_id if known
            if project_id:
                payload["project_id"] = project_id

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
            else:
                print(f"[{request_id}] Todoist error:", todoist_response.text)

        except Exception as e:
            print(f"[{request_id}] Todoist exception:", str(e))

    print(f"[{request_id}] Created {created} tasks")

    return jsonify({
        "status": "success",
        "tasks_created": created,
        "tasks_parsed": len(tasks)
    })
