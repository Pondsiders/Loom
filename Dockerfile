FROM python:3.11-slim

WORKDIR /app

# Install git (needed for soul.py to read version-controlled prompts)
# The safe.directory config allows reading mounted volumes with different ownership
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/* \
    && git config --global --add safe.directory '*'

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml .
COPY src/ src/

# Install dependencies
RUN uv pip install --system -e .

# Run the app
CMD ["uvicorn", "greatloom.app:app", "--host", "0.0.0.0", "--port", "8080"]
