"""Adversarial stdio fixture, never used by the application host."""
import json
import os
import queue
import sys
import threading
import time

mode = sys.argv[1]
lines = queue.Queue()


def read():
    for line in sys.stdin.buffer:
        lines.put(line)
    lines.put(None)


threading.Thread(target=read, daemon=True).start()
count = 0
while (line := lines.get()) is not None:
    if mode == "exit":
        sys.exit(7)
    if mode == "hang":
        time.sleep(60)
        continue
    request = json.loads(line)
    assert line == (json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    count += 1
    if mode == "serialized":
        time.sleep(0.05)
        assert lines.empty(), "more than one in-flight request"
    if mode == "malformed":
        print("not JSON", flush=True)
        continue
    if mode == "oversized":
        sys.stdout.write("x" * (1024 * 1024 + 1) + "\n")
        sys.stdout.flush()
        continue
    if mode == "stderr":
        sys.stderr.write("trusted private diagnostic\n" * 10000)
        sys.stderr.flush()
    response = {
        "protocol_version": request["protocol_version"],
        "request_id": "wrong" if mode == "mismatch" else request["request_id"],
        "ok": True,
        "result": {"pid": os.getpid(), "count": count, "params": request["params"]},
    }
    print(json.dumps(response, separators=(",", ":")), flush=True)
