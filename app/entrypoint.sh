#!/usr/bin/env bash
set -euo pipefail

MAX_WAIT=60
SLEEP_INTERVAL=2
ELAPSED=0

if [ -z "${POSTGRES_URI:-}" ]; then
  echo "ERROR: POSTGRES_URI is not set. Set POSTGRES_URI in environment."
  exit 2
fi

DB_HOST="db"
DB_PORT=5432

echo "[entrypoint] esperando conexão com o banco em $DB_HOST:$DB_PORT..."

while [ $ELAPSED -lt $MAX_WAIT ]; do
  if pg_isready -h "$DB_HOST" -p "$DB_PORT" > /dev/null 2>&1; then
    echo "[entrypoint] conexão com Postgres OK"
    break
  fi
  echo "[entrypoint] banco não disponível ainda, tentando novamente..."
  sleep $SLEEP_INTERVAL
  ELAPSED=$((ELAPSED + SLEEP_INTERVAL))
done

if [ $ELAPSED -ge $MAX_WAIT ]; then
  echo "[entrypoint] tempo de espera estourou ($MAX_WAIT s). Abortando."
  exit 1
fi

echo "[entrypoint] iniciando Streamlit..."
exec streamlit run app.py --server.port=8501 --server.address=0.0.0.0
