"""Conservative X post validation and briefing ranking. No network requests."""
from datetime import datetime, timezone
from urllib.parse import urlparse
import re

POST_PATH = re.compile(r"^/([A-Za-z0-9_]{1,15})/status/([0-9]{5,25})/?$")
METRICS = ("views", "likes", "reposts", "replies")
HANDLES = {"gat_only_you": "유경", "seyyxz": "세인", "GGzy55": "GG"}


def verify_post(post):
    """Return a normalized record; never substitute unknown metrics with zero."""
    url = str(post.get("url") or "")
    parsed = urlparse(url)
    match = POST_PATH.fullmatch(parsed.path) if parsed.scheme == "https" and parsed.hostname in ("x.com", "twitter.com", "www.x.com", "www.twitter.com") and not parsed.query and not parsed.fragment else None
    reasons = []
    if not match:
        reasons.append("개별 X 원문 URL 확인 불가")
    elif post.get("id") is not None and str(post["id"]) != match.group(2):
        reasons.append("게시글 ID 불일치")
    published = post.get("created_at")
    if published:
        try:
            dt = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                raise ValueError("timezone missing")
            published = dt.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError):
            published = None
    if not published:
        reasons.append("작성일 확인 불가")
    metrics = {}
    for name in METRICS:
        value = post.get(name)
        metrics[name] = value if type(value) is int and value >= 0 else None
    if any(value is None for value in metrics.values()):
        reasons.append("일부 반응 수치 확인 불가")
    return {
        "id": match.group(2) if match else None,
        "url": "https://x.com/" + match.group(1) + "/status/" + match.group(2) if match else None,
        "created_at": published,
        "metrics": metrics,
        "verified_url": bool(match) and "게시글 ID 불일치" not in reasons,
        "verified_100k": bool(match) and "게시글 ID 불일치" not in reasons and metrics["views"] is not None and metrics["views"] >= 100000,
        "issues": reasons,
    }


def rank_post(post, followers=None):
    """Ranking is a heuristic, not a claim of actual virality."""
    checked = verify_post(post)
    if not checked["verified_url"]:
        return None
    m = checked["metrics"]
    score = 0.0
    if m["replies"] is not None:
        score += min(m["replies"], 300) / 10
        if m["replies"] >= 30:
            score += 15
        if m["replies"] >= 100:
            score += 20
    if m["views"] is not None:
        score += min(m["views"], 200000) / 10000
        if type(followers) is int and followers > 0:
            score += min(m["views"] / followers, 30) * 2
    if m["likes"] is not None:
        score += min(m["likes"], 3000) / 200
    if m["reposts"] is not None:
        score += min(m["reposts"], 1000) / 100
    return round(score, 2)


def select_candidates(posts, limit=10):
    """Deduplicate by canonical post ID and rank verified originals only."""
    unique = {}
    for post in posts:
        checked = verify_post(post)
        if not checked["verified_url"]:
            continue
        key = checked["id"]
        if key not in unique or (rank_post(post, post.get("followers")) or 0) > (rank_post(unique[key], unique[key].get("followers")) or 0):
            unique[key] = post
    return sorted(unique.values(), key=lambda p: rank_post(p, p.get("followers")) or 0, reverse=True)[:limit]
