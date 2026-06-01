@app.route("/braindump", methods=["POST"])
def braindump():
    data = request.json
    print("Incoming request:", data)

    text = data.get("text", "")

    if not text:
        print("No text provided")
        return jsonify({"error": "No text provided"}), 400

    print("Calling OpenAI...")

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

    print("OpenAI response status:", response.status_code)
    print("OpenAI response body:", response.text)

    content = response.json()["choices"][0]["message"]["content"]
    tasks = [t.strip() for t in content.split("\n") if t.strip()]

    print("Extracted tasks:", tasks)

    for task in tasks:
        todoist_response = requests.post(
            "https://api.todoist.com/api/v1/tasks",
            headers={
                "Authorization": f"Bearer {TODOIST_TOKEN}",
                "Content-Type": "application/json"
            },
            json={"content": task}
        )
        print("Todoist status:", todoist_response.status_code)
        print("Todoist response:", todoist_response.text)

    return jsonify({"status": "success", "tasks_created": len(tasks)})
