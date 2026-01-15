# The Loom
# Where Claude becomes Alpha

FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency management
RUN pip install uv

# Copy project files
COPY pyproject.toml .
COPY loom.py .

# Install dependencies
RUN uv pip install --system -e .

# Install Pondside SDK (mounted at runtime in dev, copied in prod)
# For now, expect it to be mounted at /pondside-sdk

ENV PYTHONPATH=/pondside-sdk

EXPOSE 8080

CMD ["uvicorn", "loom:app", "--host", "0.0.0.0", "--port", "8080"]
