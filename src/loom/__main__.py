"""Entry point for running the Loom directly."""

import uvicorn


def main():
    """Run the Loom server."""
    uvicorn.run(
        "loom.app:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
    )


if __name__ == "__main__":
    main()
