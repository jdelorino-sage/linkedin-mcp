"""LinkedIn Profile Analyzer MCP server.

Exposed over streamable HTTP so Copilot Studio (and any other MCP client) can
call it. Post data comes from an Apify actor rather than a RapidAPI reseller.
"""

import json
import os
import re
from datetime import datetime

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

load_dotenv()

# The SDK's DNS rebinding protection allowlists localhost only when given no
# settings, which 421s every request once the server sits behind a real
# hostname. Allowlist the public host via MCP_ALLOWED_HOSTS. Comma separated,
# no scheme, e.g. MCP_ALLOWED_HOSTS=myapp.up.railway.app
_LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_extra_hosts = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]

_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=_LOCAL_HOSTS + _extra_hosts,
    allowed_origins=_LOCAL_HOSTS + _extra_hosts,
)

mcp = FastMCP("LinkedIn Profile Analyzer", transport_security=_security)

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
APIFY_ACTOR = os.getenv("APIFY_ACTOR", "apimaestro~linkedin-profile-posts")
DATA_DIR = os.getenv("DATA_DIR", "data")

# Remembers the last username fetched, so the read tools have a sensible
# default without sharing one global file between concurrent callers.
_last_username: str | None = None


def _slug(username: str) -> str:
    """Reduce a username or profile URL to a safe cache filename."""
    username = username.strip().rstrip("/")
    if "linkedin.com" in username:
        username = username.rsplit("/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9_.-]", "_", username) or "unknown"


def _path_for(username: str) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, f"posts_{_slug(username)}.json")


def _resolve(username: str | None) -> str:
    name = (username or _last_username or "").strip()
    if not name:
        raise Exception(
            "No username given and nothing has been fetched yet. "
            "Call fetch_and_save_linkedin_posts first."
        )
    return name


def _load(username: str | None) -> tuple[str, list[dict]]:
    name = _resolve(username)
    path = _path_for(name)
    if not os.path.exists(path):
        raise Exception(
            f"No cached posts for '{name}'. "
            "Call fetch_and_save_linkedin_posts for this username first."
        )
    with open(path, "r", encoding="utf-8") as f:
        return name, json.load(f)


def _pick(source: dict, *keys, default=""):
    """First non empty value among keys. Field names vary between actors."""
    for key in keys:
        value = source.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def _normalise(post: dict) -> dict:
    stats = post.get("stats") or {}
    author = post.get("author") or {}
    posted = post.get("posted_at")
    if not isinstance(posted, dict):
        posted = {"date": posted or ""}

    name = _pick(author, "full_name", "name")
    if not name:
        name = f"{author.get('first_name', '')} {author.get('last_name', '')}".strip()

    media = post.get("media") or {}
    images = []
    if isinstance(media, dict):
        images = [i.get("url", "") for i in (media.get("images") or []) if isinstance(i, dict)]
    elif isinstance(media, list):
        images = [i.get("url", "") for i in media if isinstance(i, dict)]

    return {
        "Post URL": _pick(post, "url", "post_url", "postUrl"),
        "Text": _pick(post, "text", "content", "commentary"),
        "Post Type": _pick(post, "post_type", "type"),
        "Like Count": _pick(stats, "like", "likes", "total_reactions", default=0),
        "Total Reactions": _pick(stats, "total_reactions", "totalReactionCount", "reactions", default=0),
        "Comment Count": _pick(stats, "comments", "comment_count", default=0),
        "Repost Count": _pick(stats, "reposts", "shares", default=0),
        "Posted Date": str(_pick(posted, "date", "timestamp"))[:10],
        "Posted Raw": _pick(posted, "date", "relative", "timestamp"),
        "Author Name": name,
        "Author Profile": _pick(author, "profile_url", "url", "link"),
        "Author Headline": _pick(author, "headline", "occupation"),
        "Main Image": images[0] if images else "",
        "All Images": ", ".join(i for i in images if i),
    }


@mcp.tool()
def fetch_and_save_linkedin_posts(username: str, limit: int = 20) -> str:
    """Fetch recent posts for a public LinkedIn profile and cache them.

    username: the LinkedIn vanity slug, the part after /in/, e.g. 'williamhgates'.
    limit: how many posts to retrieve, capped at 100.
    """
    global _last_username

    if not APIFY_TOKEN:
        raise Exception("APIFY_TOKEN is not set on the server. Add it to the environment.")

    name = _slug(username)
    limit = max(1, min(int(limit), 100))

    response = requests.post(
        f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items",
        params={"token": APIFY_TOKEN},
        json={"username": name, "page_number": 1, "total_posts": limit},
        timeout=180,
    )
    if response.status_code >= 400:
        raise Exception(
            f"Apify returned {response.status_code}: {response.text[:400]}"
        )

    items = response.json()
    if not isinstance(items, list):
        raise Exception(f"Unexpected Apify response shape: {str(items)[:400]}")

    posts = [_normalise(item) for item in items if isinstance(item, dict)]

    with open(_path_for(name), "w", encoding="utf-8") as f:
        json.dump(posts, f, indent=4)
    _last_username = name

    if not posts:
        return (
            f"Saved 0 posts for '{name}'. Apify ran but returned no items. "
            "Check the username is the slug from the profile URL, and that the "
            "profile is public and has posts."
        )

    # If every post came back blank the actor's field names have changed.
    if all(not p["Text"] and not p["Post URL"] for p in posts):
        sample = list(items[0].keys())
        return (
            f"Saved {len(posts)} posts for '{name}' but every field mapped empty. "
            f"The actor's output keys are now: {sample}. The mapping needs updating."
        )

    return f"Saved {len(posts)} posts for '{name}'."


@mcp.tool()
def get_saved_posts(username: str = "", start: int = 0, limit: int = 5) -> dict:
    """Read cached posts, newest first, with pagination."""
    name, posts = _load(username or None)
    limit = max(1, min(int(limit), 5))
    return {
        "username": name,
        "posts": posts[start:start + limit],
        "total_posts": len(posts),
        "has_more": start + limit < len(posts),
    }


@mcp.tool()
def search_posts(keyword: str, username: str = "") -> dict:
    """Search cached posts for a keyword in the post text."""
    name, posts = _load(username or None)
    hits = [p for p in posts if keyword.lower() in (p.get("Text") or "").lower()]
    return {
        "username": name,
        "keyword": keyword,
        "total_results": len(hits),
        "posts": hits[:5],
        "has_more": len(hits) > 5,
    }


@mcp.tool()
def get_top_posts(metric: str = "Total Reactions", top_n: int = 5, username: str = "") -> dict:
    """Rank cached posts by an engagement metric."""
    allowed = ["Like Count", "Total Reactions", "Comment Count", "Repost Count"]
    if metric not in allowed:
        return {"message": f"Invalid metric. Use one of: {', '.join(allowed)}."}
    name, posts = _load(username or None)
    ranked = sorted(posts, key=lambda p: p.get(metric) or 0, reverse=True)
    return {"username": name, "metric": metric, "posts": ranked[:max(1, min(int(top_n), 20))]}


@mcp.tool()
def get_posts_by_date(start_date: str, end_date: str, username: str = "") -> dict:
    """Filter cached posts to a date range, inclusive. Dates are YYYY-MM-DD."""
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return {"message": "Invalid date format. Use YYYY-MM-DD."}

    name, posts = _load(username or None)
    hits = []
    for post in posts:
        raw = (post.get("Posted Date") or "")[:10]
        try:
            when = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            continue
        if start_dt <= when <= end_dt:
            hits.append(post)

    return {
        "username": name,
        "start_date": start_date,
        "end_date": end_date,
        "total_results": len(hits),
        "posts": hits[:5],
        "has_more": len(hits) > 5,
    }


if __name__ == "__main__":
    import uvicorn
    from auth import RequireKey

    try:
        app = mcp.http_app(path="/mcp")      # standalone fastmcp 2.x
    except AttributeError:
        mcp.settings.streamable_http_path = "/mcp"
        app = mcp.streamable_http_app()      # official mcp SDK v1

    uvicorn.run(
        RequireKey(app),
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
    )
