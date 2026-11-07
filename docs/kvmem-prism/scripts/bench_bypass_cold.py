#!/usr/bin/env python3
"""Cold short-context probe: measures prefill speed at a target token count."""
import argparse, json, os, time, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "14802")))
ap.add_argument("--key", default=os.environ.get("API_KEY", ""))
ap.add_argument("--prompt-file", default=os.environ.get("PROMPT_FILE", "prompt_128k.txt"))
ap.add_argument("--tokens", type=int, default=30720)
ap.add_argument("--base-tokens", type=int, default=117789)
a = ap.parse_args()

base = open(a.prompt_file, encoding="utf-8").read()
text = "COLD START. " + base[: int(len(base) * a.tokens / float(a.base_tokens))]
headers = {"Content-Type": "application/json"}
if a.key:
    headers["Authorization"] = "Bearer " + a.key
for _ in range(2):
    req = urllib.request.Request("http://127.0.0.1:%d/tokenize" % a.port,
                                 data=json.dumps({"content": text}).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        n = len(json.load(r)["tokens"])
    if abs(n - a.tokens) <= max(64, a.tokens // 200):
        break
    text = "COLD START. " + base[: max(1, int((len(text) - 12) * a.tokens / float(n)))]
body = {"messages": [{"role": "user", "content": text}], "max_tokens": 16,
        "temperature": 0.0, "cache_prompt": False}
req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % a.port,
                             data=json.dumps(body).encode(), headers=headers)
t0 = time.time()
with urllib.request.urlopen(req, timeout=900) as r:
    resp = json.load(r)
tm = resp.get("timings", {})
print("target=%d tokenized=%d wall=%.1fs prompt_n=%s cache_n=%s prefill_tps=%s gen_tps=%s" % (
    a.tokens, n, time.time() - t0, tm.get("prompt_n"), tm.get("cache_n"),
    round(tm.get("prompt_per_second") or 0, 1),
    round(tm.get("predicted_per_second") or 0, 1)))
