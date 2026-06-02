from flask import Flask, request, jsonify
import requests
import os
import json
import uuid
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

app = Flask(__name__)

OPENAI_KEY = os.environ.get("OPENAI_KEY")
TODOIST_TOKEN = os.environ.get("TODOIST_TOKEN")
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY")

EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_TO = os.environ.get("EMAIL_TO")

PROJECT_MAP = {
    "work": "6RH9f45GMC49J67P",
    "home": "6RH9f43CvMjp9Vcp"
}

# ------------------------
# Utilities
# ------------------------

def normalize_title(title):
    title = title.lower()
    title = re.sub(r'[^\w\s]', '', title)
    title = re.sub(r'\s+', ' ', title)
    return title.strip()

def require_auth(req):
    provided_key = req.headers.get("X-Internal-Key")
    if INTERNAL_API_KEY and provided_key != INTERNAL_API_KEY:
        return False
    return True

def send_email(subject, body_text, body_html):
    if not EMAIL_USER or not EMAIL_PASSWORD or not EMAIL_TO:
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO

    part1 = MIMEText(body_text, "plain")
    part2 = MIMEText(body_html, "html")

    msg.attach(part1)
    msg.attach(part2)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.send_message(msg)

# ------------------------
# Routes
# ------------------------

@app.route("/")
def home():
    return "Brain Dump API is running"

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

# ------------------------
# Brain Dump Endpoint
# ------------------------

@app.route("/braindump", methods=["POST"])
def braindump():
    if not require_auth(request):
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
                            "Extract actionable tasks.\n"
                            "Return JSON with tasks containing title, project (work|home|other), priority 1-4."
                        )
                    },
                    {"role": "user", "content": text}
                ]
            }
        )

        content = openai_response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        tasks = parsed.get("tasks", [])

    except Exception:
        return jsonify({"error": "AI parsing failed"}), 500

    # Duplicate detection
    existing_titles = set()
    existing_response = requests.get(
        "https://api.todoist.com/api/v1/tasks",
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}"}
    )

    if existing_response.status_code == 200:
        existing_tasks = existing_response.json().get("results", [])
        for t in existing_tasks:
            existing_titles.add(normalize_title(t.get("content", "")))

    created = 0
    skipped = 0

    for task in tasks:
        title = task.get("title", "").strip()
        normalized = normalize_title(title)

        if normalized in existing_titles:
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

        todoist_response = requests.post(
            "https://api.todoist.com/api/v1/tasks",
            headers={
                "Authorization": f"Bearer {TODOIST_TOKEN}",
                "Content-Type": "application/json"
            },
            json=payload
        )

        if todoist_response.status_code == 200:
            created += 1

    return jsonify({
        "status": "success",
        "created": created,
        "skipped_duplicates": skipped
    })

```python
# ------------------------
# Daily Summary Only
# ------------------------

@app.route("/daily-summary")
def daily_summary():
    if not require_auth(request):
        return jsonify({"error": "Unauthorized"}), 401

    eastern = ZoneInfo("America/New_York")
    today = datetime.now(eastern).date()

    response = requests.get(
        "https://api.todoist.com/api/v1/tasks",
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}"}
    )

    if response.status_code != 200:
        return jsonify({
            "error": "Failed to retrieve Todoist tasks",
            "status_code": response.status_code
        }), 500

    tasks = response.json().get("results", [])

    today_tasks = []

    for t in tasks:
        try:
            created_utc = datetime.fromisoformat(
                t["created_at"].replace("Z", "+00:00")
            )

            created_eastern = created_utc.astimezone(eastern)

            if created_eastern.date() == today:
                today_tasks.append(t["content"])

        except Exception:
            continue

    html_tasks = "".join(
        f"<li>{task}</li>" for task in today_tasks
    )

    if not html_tasks:
        html_tasks = "<li>No tasks created today.</li>"

    html_body = f"""
    <html>
      <body style="font-family: Arial, sans-serif;">
        <h2>Daily Summary - {today}</h2>
        <ul>{html_tasks}</ul>
      </body>
    </html>
    """

    text_body = (
        "Daily Summary:\n\n" +
        "\n".join(today_tasks)
        if today_tasks
        else "Daily Summary:\n\nNo tasks created today."
    )

    send_email(
        subject=f"Daily Task Summary - {today}",
        body_text=text_body,
        body_html=html_body
    )

    return jsonify({
        "sent": True,
        "count": len(today_tasks)
    })

# ------------------------
# Main
# ------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```
