"""Run from the repository root: python -m backend.app."""

import os

import uvicorn


def main() -> None:
    host = os.environ.get("VOXTRACT_HOST", "127.0.0.1")
    port = int(os.environ.get("VOXTRACT_PORT", "8000"))
    if not 1 <= port <= 65535:
        raise ValueError("VOXTRACT_PORT must be between 1 and 65535")
    uvicorn.run("backend.app.main:app", host=host, port=port, workers=1)


if __name__ == "__main__":
    main()
