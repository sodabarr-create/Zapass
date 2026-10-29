FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ZAPAS_DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home zapas && mkdir /data && chown zapas:zapas /data
COPY app.py accounts.py billing.py bot_content.py catalog.py deployment.py economics.py planner.py studio.py telegram_app.py vault.py ./
COPY static ./static
COPY scripts ./scripts
USER zapas
EXPOSE 8766
CMD ["python", "app.py", "--public-host", "0.0.0.0"]
