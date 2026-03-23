"""
tools/drop_old_collections.py
==============================
Drops conversation_memory and chat_history Milvus collections so the
server can recreate them at 1024-dim (BAAI/bge-m3) on next restart.

Run BEFORE restarting the server, AFTER running the reingest scripts.

  source venv/bin/activate
  python tools/drop_old_collections.py
"""

import sys
import os

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SERVER_DIR)

from pymilvus import connections, utility

MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
TO_DROP = ["conversation_memory", "chat_history"]


def main():
    print("Connecting to Milvus...")
    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)

    for name in TO_DROP:
        if utility.has_collection(name):
            utility.drop_collection(name)
            print(f"  Dropped: {name}")
        else:
            print(f"  Not found (already gone): {name}")

    print("\nDone. Restart the server — conversation_memory will be recreated at 1024-dim.")


if __name__ == "__main__":
    main()
