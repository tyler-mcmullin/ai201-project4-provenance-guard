import uuid
from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import json
from datetime import datetime, timezone
from signals import llm_signal, stylometric_signal, confidence_score, generate_label

LOG_PATH = "audit_log.jsonl"


app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# Helper Functions
def log_event(entry):
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")

def read_log(limit=20):
    try:
        with open(LOG_PATH) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in lines[-limit:]]

def get_original_decision(content_id: str) -> dict | None:
    entries = read_log(limit=10)
    for entry in entries:
        if entry.get("content_id") == content_id and entry.get("event_type") == "attribution":
            return entry
    return None


# Routes
@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json()
    text = data.get("text")
    content_id = data.get("content_id", str(uuid.uuid4()))
    creator_id = data.get("creator_id")

    if not text:
        return jsonify({"error": "text is required"}), 400

    llm_score = llm_signal(text)
    stylo_score = stylometric_signal(text)
    result = confidence_score(llm_score, stylo_score)

    label = generate_label(result["attribution"], result["score"])

    log_event({
        "event_type": "attribution",
        "content_id": content_id,
        "creator_id": creator_id,
        "llm_score": llm_score,
        "stylometric_score": stylo_score,
        "confidence_score": result["score"],
        "attribution": result["attribution"],
        "label_text": label,
    })

    return jsonify({
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": result["attribution"],
        "confidence_score": result["score"],
        "label_text": label,
        "signals": {
            "llm_score": llm_score,
            "stylometric_score": stylo_score,
            },
        "status": "complete",
    })

@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json()

    if not data:
        return jsonify({"error": "request body must be JSON"}), 400

    content_id = data.get("content_id")
    creator_id = data.get("creator_id")
    reasoning = data.get("creator_reasoning")

    if not content_id:
        return jsonify({"error": "content_id is required"}), 400
    if not reasoning:
        return jsonify({"error": "creator_reasoning is required"}), 400

    appeal_id = str(uuid.uuid4()) 

    original = get_original_decision(content_id)
    if not original:
        return jsonify({"error": "content_id not found"}), 404

    log_event({
        "event_type": "appeal",
        "appeal_id": appeal_id,
        "content_id": content_id,
        "creator_id": creator_id,
        "creator_reasoning": reasoning,
        "status": "under_review",
        "original_attribution": original.get("attribution"),
        "original_confidence": original.get("confidence_score"),
    })

    return jsonify({
        "content_id": content_id,
        "creator_id": creator_id,
        "appeal_id": appeal_id,
        "status": "under_review",
        "message": "Your appeal was received and is under review.",
    })

@app.route("/log", methods=["GET"])
def view_log():
    return jsonify({"entries": read_log()})

@app.route("/")
def home():
    return "Provenance Guard is running."

if __name__ == "__main__":
    app.run(port=8080, debug=True)