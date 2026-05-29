"""
Persistent storage for user feedback on job postings.
Feedback is stored in feedback/feedback_store.json as a flat list of items.
"""
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone

STORE_PATH = Path(__file__).parent / "feedback_store.json"


def _load() -> dict:
    if STORE_PATH.exists():
        with open(STORE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"items": []}


def _save(data: dict) -> None:
    STORE_PATH.parent.mkdir(exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def job_hash(url: str) -> str:
    """Short hash used as stable job ID in feedback links."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def add_feedback(
    job_id: str,
    url: str,
    title: str,
    organization: str,
    score: int,
    action: str,        # "like" or "pass"
    comment: str = "",
) -> None:
    """Upsert feedback for a job (replaces any existing entry for the same job_id)."""
    data = _load()
    data["items"] = [x for x in data["items"] if x.get("job_id") != job_id]
    data["items"].append({
        "job_id": job_id,
        "url": url,
        "title": title,
        "organization": organization,
        "score_given": score,
        "action": action,
        "comment": comment,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    _save(data)


def get_all() -> list:
    return _load()["items"]


def get_feedback_summary() -> dict:
    items = get_all()
    liked  = [x for x in items if x["action"] == "like"]
    passed = [x for x in items if x["action"] == "pass"]
    return {"liked": liked, "passed": passed, "total": len(items)}
