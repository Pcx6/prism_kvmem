import json, time, urllib.request
key = 'test-key-14805'
text = open('/tmp/p128k.txt').read()
body = {"messages": [{"role": "user", "content": text}], "max_tokens": 32, "temperature": 0.0}
req = urllib.request.Request("http://127.0.0.1:14805/v1/chat/completions",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=1800) as r:
        resp = json.load(r)
except Exception as e:
    print("ERR", repr(e)[:300]); raise SystemExit
tm = resp.get("timings", {})
print("wall=%.1fs prompt_n=%s prefill_tps=%s gen_n=%s gen_tps=%s" % (
    time.time()-t0, tm.get('prompt_n'), round(tm.get('prompt_per_second') or 0,1),
    tm.get('predicted_n'), round(tm.get('predicted_per_second') or 0,1)))
print("timings:", json.dumps(tm)[:400])
print("head:", (resp["choices"][0]["message"].get("content") or "")[:100].replace(chr(10)," "))
