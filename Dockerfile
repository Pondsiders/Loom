# The Loom
# Where Claude becomes Alpha

FROM python:3.12-slim

WORKDIR /app

# Install uv and git (needed to fetch SDK from GitHub)
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
RUN pip install uv

# Copy project files
COPY pyproject.toml .
COPY src/ src/

# Install dependencies (including pondside SDK from GitHub)
RUN uv pip install --system -e .

EXPOSE 8080

CMD ["uvicorn", "loom.app:app", "--host", "0.0.0.0", "--port", "8080"]
