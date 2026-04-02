#!/bin/bash
# Start the Satu AI Brain server (Python 3.8)
cd "$(dirname "$0")"
source venv/bin/activate
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
