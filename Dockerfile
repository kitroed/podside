FROM python:3.13-slim
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY static/ static/

RUN useradd --system --no-create-home app
USER app

EXPOSE 8000

# One worker only: rooms live in this process's memory.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
