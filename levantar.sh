#!/usr/bin/env bash
# Inicia Laravel (:8000), Angular (:4200) y FastAPI (:8001).
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")" && pwd)"
BACK="$RAIZ/laravel_nventas-main"
FRONT="$RAIZ/app ang ventas"
PYTHON="$RAIZ/ml/.venv/bin/python"
PIDS=()

if ! systemctl is-active --quiet mysql; then
  echo 'MySQL está apagado. Ejecuta sudo systemctl start mysql y vuelve a intentar.'
  exit 1
fi
if [[ ! -x "$PYTHON" ]] || ! "$PYTHON" -c 'import pandas, numpy, sklearn, fastapi, uvicorn' >/dev/null 2>&1; then
  echo 'Faltan las dependencias de Python. Desde la raíz del proyecto ejecuta:'
  echo 'python3 -m venv ml/.venv'
  echo 'ml/.venv/bin/python -m pip install -r ml/requirements.txt'
  exit 1
fi
apagar() {
  trap - EXIT INT TERM
  if ((${#PIDS[@]})); then
    kill "${PIDS[@]}" 2>/dev/null || true
    wait "${PIDS[@]}" 2>/dev/null || true
  fi
}
trap apagar EXIT
trap 'exit 0' INT TERM
ocupado() {
  local escuchas
  if ! escuchas=$(ss -ltnH "sport = :$1"); then
    echo "No se pudo comprobar el puerto $1. Se detiene el inicio para evitar conflictos." >&2
    exit 1
  fi
  [[ -n "$escuchas" ]]
}

if ocupado 8001; then
  if ! curl --fail --silent --max-time 3 http://127.0.0.1:8001/health >/dev/null; then
    echo 'El puerto 8001 está ocupado por otro servicio. Libéralo antes de iniciar el análisis.'
    exit 1
  fi
  echo 'Python ya está iniciado.'
else
  (cd "$RAIZ/ml" && exec "$PYTHON" -m uvicorn app:app --host 127.0.0.1 --port 8001) &
  PIDS+=("$!")
fi
if ocupado 8000; then
  echo 'El puerto 8000 ya está en uso; se conserva el backend existente.'
else
  (cd "$BACK" && exec php artisan serve --host 127.0.0.1 --port 8000) &
  PIDS+=("$!")
fi
if ocupado 4200; then
  echo 'El puerto 4200 ya está en uso; se conserva el frontend existente.'
else
  (cd "$FRONT" && PATH="$PWD/node_modules/.bin:$PATH" NG_CLI_ANALYTICS=false exec ng serve --host 127.0.0.1 --port 4200) &
  PIDS+=("$!")
fi

echo 'Aplicación: http://127.0.0.1:4200/'
echo 'Entrenamiento: http://127.0.0.1:4200/dashboard/analisis/entrenamiento'
echo 'API Python / documentación: http://127.0.0.1:8001/docs'
echo 'Ctrl+C apaga únicamente los procesos iniciados por este script.'
if ((${#PIDS[@]})); then
  wait -n "${PIDS[@]}"
else
  echo 'Los tres servicios ya estaban iniciados.'
fi
