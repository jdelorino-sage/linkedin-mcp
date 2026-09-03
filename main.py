from mcp.server.fastmcp import FastMCP
import requests
import json
import os
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables
load_dotenv() 

# 1. Initialize the MCP server with the SSE-compatible setup
mcp = FastMCP("LinkedIn Profile Analyzer")
DATA_FILE = "linkedin_posts.json"
rapidapi_key = os.getenv("RAPIDAPI_KEY")

@mcp.tool()
def fetch_and_save_linkedin_posts(username: str) -> str:
    """Fetch LinkedIn posts for a given username and save them in a JSON file."""
    url = "https://linkedin-data-api.p.rapidapi.com/get-profile-posts"
    headers = {
        "x-rapidapi-key": rapidapi_key, 
        "x-rapidapi-host": "linkedin-data-api.p.rapidapi.com"
    }
    querystring = {"username": username}
    response = requests.get(url, headers=headers, params=querystring)
    if response.status_code != 200:
        raise Exception(f"Error fetching posts: {response.status_code} - {response.text}")
    
    data = response.json()
    posts = []
    for post in data.get('data', []):
        posts.append({
            "Post URL": post.get('postUrl', ''), 
            "Text": post.get('text', ''), 
            "Like Count": post.get('likeCount', 0), 
            "Total Reactions": post.get('totalReactionCount', 0), 
            "Posted Date": post.get('postedDate', ''), 
            "Posted Timestamp": post.get('postedDateTimestamp', ''), 
            "Share URL": post.get('shareUrl', ''), 
            "Author Name": f"{post.get('author', {}).get('firstName', '')} {post.get('author', {}).get('lastName', '')}", 
            "Author Profile": post.get('author', {}).get('url', ''), 
            "Author Headline": post.get('author', {}).get('headline', ''), 
            "Author Profile Picture": post.get('author', {}).get('profilePictures', [{}])[0].get('url', ''), 
            "Main Image": post.get('image', [{}])[0].get('url', '') if post.get('image') else '', 
            "All Images": ", ".join([img.get('url', '') for img in post.get('image', [])]),
        })
        
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(posts, f, indent=4)
    return f"Data saved in {DATA_FILE}"

@mcp.tool()
def get_saved_posts(start: int = 0, limit: int = 5) -> dict:
    """Retrieve saved LinkedIn posts with pagination."""
    if not os.path.exists(DATA_FILE):
        return {"message": "No data found. Fetch posts first using fetch_and_save_linkedin_posts().", "posts": []}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            posts = json.load(f)
        total_posts = len(posts)
        limit = min(limit, 5)
        paginated_posts = posts[start:start + limit]
        return {"posts": paginated_posts, "total_posts": total_posts, "has_more": start + limit < total_posts}
    except json.JSONDecodeError:
        return {"message": "Error reading data file. JSON might be corrupted.", "posts": []}

@mcp.tool()
def search_posts(keyword: str) -> dict:
    """Search saved LinkedIn posts for a specific keyword."""
    if not os.path.exists(DATA_FILE):
        return {"message": "No data found. Fetch posts first.", "posts": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        posts = json.load(f)
    filtered_posts = [post for post in posts if keyword.lower() in post.get("Text", "").lower()]
    return {"keyword": keyword, "total_results": len(filtered_posts), "posts": filtered_posts[:5], "has_more": len(filtered_posts) > 5}

@mcp.tool()
def get_top_posts(metric: str = "Like Count", top_n: int = 5) -> dict:
    """Get the top LinkedIn posts based on a specific engagement metric."""
    if not os.path.exists(DATA_FILE):
        return {"message": "No data found. Fetch posts first.", "posts": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        posts = json.load(f)
    if metric not in ["Like Count", "Total Reactions"]:
        return {"message": "Invalid metric. Use 'Like Count' or 'Total Reactions'."}
    sorted_posts = sorted(posts, key=lambda x: x.get(metric, 0), reverse=True)
    return {"metric": metric, "posts": sorted_posts[:top_n]}

@mcp.tool()
def get_posts_by_date(start_date: str, end_date: str) -> dict:
    """Retrieve posts within a specified date range (YYYY-MM-DD)."""
    if not os.path.exists(DATA_FILE):
        return {"message": "No data found. Fetch posts first.", "posts": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        posts = json.load(f)
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return {"message": "Invalid date format. Use 'YYYY-MM-DD'."}
    filtered_posts = [post for post in posts if start_dt <= datetime.strptime(post["Posted Date"], "%Y-%m-%d") <= end_dt]
    return {"start_date": start_date, "end_date": end_date, "total_results": len(filtered_posts), "posts": filtered_posts[:5], "has_more": len(filtered_posts) > 5}

if __name__ == "__main__":
    import uvicorn
    from auth import RequireKey

    try:
        app = mcp.http_app(path="/mcp")      # standalone fastmcp 2.x
    except AttributeError:
        mcp.settings.streamable_http_path = "/mcp"
        app = mcp.streamable_http_app()      # official mcp SDK v1

    uvicorn.run(RequireKey(app), host="127.0.0.1", port=8000)
