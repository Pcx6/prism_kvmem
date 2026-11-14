#!/usr/bin/env python3
"""100K document retrieval check: the answer must contain ORANGE-FALCON-42."""
import argparse, json, os, time, urllib.request, urllib.error

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "14802")))
ap.add_argument("--key", default=os.environ.get("API_KEY", ""))
ap.add_argument("--prompt-file", default=os.environ.get("PROMPT_FILE", "prompt_quality_100k.txt"))
ap.add_argument("--max-tokens", type=int, default=512)
a = ap.parse_args()

text = open(a.prompt_file, encoding="utf-8").read()
body = {"messages": [{"role": "user", "content": text}],
        "max_tokens": a.max_tokens, "temperature": 0.0, "cache_prompt": False}
headers = {"Content-Type": "application/json"}
if a.key:
    headers["Authorization"] = "Bearer " + a.key

req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % a.port,
                             data=json.dumps(body).encode(), headers=headers)
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=1800) as r:
        resp = json.load(r)
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode("utf-8", "replace")[:500])
    raise SystemExit(1)
m = resp["choices"][0]["message"]
content = m.get("content") or ""
reason = m.get("reasoning_content") or ""
tm = resp.get("timings", {})
print(json.dumps({"port": a.port, "wall_s": round(time.time() - t0, 1),
    "prompt_n": tm.get("prompt_n"), "cache_n": tm.get("cache_n"),
    "prefill_tps": round(tm.get("prompt_per_second") or 0, 1),
    "gen_tps": round(tm.get("predicted_per_second") or 0, 1),
    "has_orange": "ORANGE-FALCON-42" in content,
    "has_silver": "SILVER-HERON-19" in content,
    "content": content[:200], "reasoning": reason[:120]}, ensure_ascii=False))
