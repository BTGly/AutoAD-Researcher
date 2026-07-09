import os

HOST = os.environ.get("AUTOAD_HOST", "0.0.0.0")
PORT = int(os.environ.get("AUTOAD_PORT", "8000"))
RUNS_ROOT = os.environ.get("AUTOAD_RUNS_ROOT", "runs")
