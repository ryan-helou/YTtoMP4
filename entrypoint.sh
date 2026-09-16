#!/bin/sh
# Start the PO token provider beside the app, then hand off to uvicorn.
# A single worker keeps the in-memory job table coherent; real concurrency is
# the thread pool inside the app (MAX_CONCURRENT).
if [ -f /opt/pot/build/main.js ]; then
  node /opt/pot/build/main.js --port "${POT_PORT:-4416}" &
  echo "pot-provider: started on 127.0.0.1:${POT_PORT:-4416}"
else
  echo "pot-provider: not present, continuing without it"
fi

exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 --timeout-keep-alive 75
