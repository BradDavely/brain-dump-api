from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

OPENAI_KEY = os.environ.get("OPENAI_KEY")
TODOIST_TOKEN = os.environ.get("TODOIST_TOKEN")

@app.route("/braindump", methods=["POST"])
def braindump():
    data = request.json
    text = data.get("text", "")

    if not text:
        return jsonify({"error": "No text provided"}), 400

    # Call OpenAI
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "Extract actionable tasks. Each task on its own line. No numbering."},
                {"role": "user", "content": text}
            ]
        }
    )

    content = response.json()["choices"][0]["message"]["content"]
    tasks = [t.strip() for t in content.split("\n") if t.strip()]

    # Send each task to Todoist
    for task in tasks:
        requests.post(
            "https://api.todoist.com/api/v1/tasks",
            headers={
                "Authorization": f"Bearer {TODOIST_TOKEN}",
                "Content-Type": "application/json"
            },
            json={"content": task}
        )

    return jsonify({"status": "success", "tasks_created": len(tasks)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
