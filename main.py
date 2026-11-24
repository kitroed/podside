"""Entry point kept so `uvicorn main:app` still works. The code lives in app.py."""

from app import app

__all__ = ["app"]

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
