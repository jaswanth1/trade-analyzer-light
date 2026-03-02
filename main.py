from fastapi import FastAPI

app = FastAPI()


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}


# ── Upstox OAuth & Status ───────────────────────────────────────────────

@app.get("/api/v1/upstox/auth-url")
async def upstox_auth_url():
    """Returns the Upstox OAuth login URL."""
    from common.upstox import get_auth_url
    return {"url": get_auth_url()}


@app.get("/api/v1/upstox/callback")
async def upstox_callback(code: str, state: str = ""):
    """OAuth callback — exchanges code for access token, saves it."""
    from common.upstox import exchange_auth_code
    token = exchange_auth_code(code)
    if token:
        return {"status": "success", "message": "Token saved. You can close this tab."}
    return {"status": "error", "message": "Failed to exchange code"}


@app.get("/api/v1/upstox/status")
async def upstox_status():
    """Check if Upstox token is valid."""
    from common.upstox import is_upstox_available, get_access_token
    available = is_upstox_available()
    return {"available": available, "has_token": get_access_token() is not None}
