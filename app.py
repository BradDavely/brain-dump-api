from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

OPENAI_KEY = os.environ.get("OPENAI_KEY")
TODOIST_TOKEN = os.environ.get("TODOIST_TOKEN")

@app.route("/")
def home():
    return "Brain Dump API is running"

@app.route("/braindump", methods=["POST"])
def braindump():
    if not OPENAI_KEY or not TODOIST_TOKEN:
        return jsonify({"error": "Missing environment variables"}), 500

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON body"}), 400

    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "No text provided"}), 400

    # Call OpenAI
    openai_response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "gpt-5.4-mini",
            "messages": [
                {"role": "system", "content": "Extract actionable tasks. Each task must be on its own line. No numbering."},
                {"role": "user", "content": text}
            ]
        },
        timeout=20
    )

    if openai_response.status_code != 200:
        return jsonify({"error": "OpenAI failed", "details": openai_response.text}), 500

    content = openai_response.json()["choices"][0]["message"]["content"]
    tasks = [t.strip() for t in content.split("\n") if t.strip()]

    created = 0

    for task in tasks:
        todoist_response = requests.post(
            "https://api.todoist.com/api/v1/tasks",
            headers={
                "Authorization": f"Bearer {TODOIST_TOKEN}",
                "Content-Type": "application/json"
            },
            json={"content": task},
            timeout=10
        )

        if todoist_response.status_code == 200:
            created += 1

    return jsonify({"status": "success", "tasks_created": created})
