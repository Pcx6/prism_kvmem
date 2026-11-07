#!/usr/bin/env python3
"""Long-context benchmark (about 256K tokens)."""
import argparse, json, os, time, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "14802")))
ap.add_argument("--key", default=os.environ.get("API_KEY", ""))
ap.add_argument("--prompt-file", default=os.environ.get("PROMPT_FILE", "prompt_250k.txt"))
ap.add_argument("--max-tokens", type=int, default=32)
a = ap.parse_args()

text = open(a.prompt_file, encoding="utf-8").read()
body = {"messages": [{"role": "user", "content": text}],
        "max_tokens": a.max_tokens, "temperature": 0.0}
headers = {"Content-Type": "application/json"}
if a.key:
    headers["Authorization"] = "Bearer " + a.key

req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % a.port,
                             data=json.dumps(body).encode(), headers=headers)
t0 = time.time()
with urllib.request.urlopen(req, timeout=1800) as r:
    resp = json.load(r)
tm = resp.get("timings", {})
print("wall=%.1fs prompt_n=%s prefill_tps=%s gen_n=%s gen_tps=%s" % (
    time.time() - t0, tm.get("prompt_n"),
    round(tm.get("prompt_per_second") or 0, 1),
    tm.get("predicted_n"), round(tm.get("predicted_per_second") or 0, 1)))
print("timings:", json.dumps(tm)[:400])
