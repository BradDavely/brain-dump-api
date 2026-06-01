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
    try:
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
                "model": "gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": "Extract actionable tasks. Each task must be on its own line. No numbering."
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
            return jsonify({
                "error": "OpenAI request failed",
                "details": openai_response.text
            }), 500

        try:
            content = openai_response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            return jsonify({
                "error": "Unexpected OpenAI response",
                "details": openai_response.text
            }), 500

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

        return jsonify({
            "status": "success",
            "tasks_created": created,
            "tasks_parsed": len(tasks)
        })

    except Exception as e:
        return jsonify({
            "error": "Server error",
            "details": str(e)
        }), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
