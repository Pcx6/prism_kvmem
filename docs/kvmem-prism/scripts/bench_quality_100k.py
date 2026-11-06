import json, time, urllib.request, sys, urllib.error
port = sys.argv[1]
key = sys.argv[2] if len(sys.argv) > 2 else ''
text = open('/tmp/kvmem_q100k.txt').read()
body = {'messages': [{'role': 'user', 'content': text}], 'max_tokens': 512, 'temperature': 0.0}
headers = {'Content-Type': 'application/json'}
if key: headers['Authorization'] = 'Bearer ' + key
req = urllib.request.Request(f'http://127.0.0.1:{port}/v1/chat/completions', data=json.dumps(body).encode(), headers=headers)
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=1800) as r:
        resp = json.load(r)
except urllib.error.HTTPError as e:
    print('HTTP', e.code, e.read().decode('utf-8','replace')[:500]); raise SystemExit
wall = time.time() - t0
m = resp['choices'][0]['message']
content = m.get('content') or ''
reason = m.get('reasoning_content') or ''
tm = resp.get('timings', {})
print(json.dumps({'port': port, 'wall_s': round(wall,1), 'prompt_n': tm.get('prompt_n'), 'cache_n': tm.get('cache_n'), 'prefill_tps': round(tm.get('prompt_per_second') or 0,1), 'gen_tps': round(tm.get('predicted_per_second') or 0,1), 'has_orange': 'ORANGE-FALCON-42' in content, 'has_silver': 'SILVER-HERON-19' in content, 'content': content[:200], 'reasoning': reason[:120]}, ensure_ascii=False))
