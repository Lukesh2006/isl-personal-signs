"""Sample 1: basic server endpoints with FastAPI.

Run:  pip install fastapi uvicorn
      uvicorn server_endpoints:app --port 8000
Then: http://127.0.0.1:8000/health
"""

from fastapi import FastAPI

app = FastAPI(title="Personal ISL sign recognition (sample)")


@app.get("/health")
async def health():
    """Lets a browser or a monitor check that the server is up."""
    return {"status": "ok"}


@app.get("/config")
async def config():
    """Tells the web page how users sign in."""
    return {"auth": "pocketbase", "pocketbase_url": "https://db.example.com"}
