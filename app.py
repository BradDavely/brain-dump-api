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
        print(f"[{request_id}] Missing environment variables")
        return jsonify({"error": "Server configuration error"}), 500

    data = request.get_json(silent=True)
    if not data:
        print(f"[{request_id}] Invalid JSON body")
        return jsonify({"error": "Invalid JSON body"}), 400

    text = data.get("text", "").strip()

    if not text:
        print(f"[{request_id}] Empty text")
        return jsonify({"error": "No text provided"}), 400

    print(f"[{request_id}] Calling OpenAI")

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
      "project": "work | life_admin | personal | health | other",
      "priority": 1-4,
      "labels": ["optional", "labels"]
    }
  ]
}

Rules:
- No commentary
- No markdown
- No explanation
- Only valid JSON
"""
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
            print(f"[{request_id}] OpenAI error: {openai_response.text}")
            return jsonify({"error": "OpenAI failed"}), 500

        content = openai_response.json()["choices"][0]["message"]["content"]

        parsed = json.loads(content)
        tasks = parsed.get("tasks", [])

        print(f"[{request_id}] Parsed {len(tasks)} tasks")

    except Exception as e:
        print(f"[{request_id}] AI parsing failed: {str(e)}")
        return jsonify({"error": "AI parsing failed"}), 500

    created = 0

    for task in tasks:
        try:
            todoist_response = requests.post(
                "https://api.todoist.com/api/v1/tasks",
                headers={
                    "Authorization": f"Bearer {TODOIST_TOKEN}",
                    "Content-Type": "application/json"
                },
                json={
                    "content": task.get("title"),
                    "priority": task.get("priority", 1)
                },
                timeout=10
            )

            if todoist_response.status_code == 200:
                created += 1
            else:
                print(f"[{request_id}] Todoist error: {todoist_response.text}")

        except Exception as e:
            print(f"[{request_id}] Todoist exception: {str(e)}")

    print(f"[{request_id}] Created {created} tasks")

    return jsonify({
        "status": "success",
        "tasks_created": created,
        "tasks_parsed": len(tasks),
        "request_id": request_id
    })
