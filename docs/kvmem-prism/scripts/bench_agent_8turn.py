import json, time, urllib.request, sys, argparse
ap = argparse.ArgumentParser()
ap.add_argument('out')
ap.add_argument('--port', type=int, default=14802)
ap.add_argument('--key', default='')
ap.add_argument('--truncate-chars', type=int, default=2048)
ARGS = ap.parse_args()
PORT = ARGS.port
KEY = ARGS.key
SYSTEM = ('You are an autonomous agent operating a Linux host. Follow the operator policy strictly. '
          'Available tools: read_file, write_file, run_shell, http_get, web_search. ') * 10
SYSTEM = SYSTEM[:8000]
def user_text(i):
    return (f'Turn {i}: inspect the nginx access log and report the top error. Be concise. ') * 30
def tool_call(i):
    return json.dumps({'tool': 'run_shell', 'args': {'cmd': f'tail -n 200 /var/log/nginx/error.log | sort | uniq -c | sort -rn | head -{i}'}})
def truncate(text, limit):
    if limit <= 0 or len(text) <= limit: return text
    return text[:limit] + '\n...[truncated %d chars]' % (len(text) - limit)
def tool_result(i):
    return ('2025/11/01 10:%02d:33 [error] upstream timed out (110: Connection timed out) while reading response header. '
            'client: 10.0.0.%d, server: api.internal, request: "POST /v1/agent HTTP/1.1", upstream: "http://127.0.0.1:9000/v1/agent"\n') % (i, i) * 40
headers = {'Content-Type': 'application/json'}
if KEY: headers['Authorization'] = 'Bearer ' + KEY
def call(messages, max_tokens):
    body = {'messages': messages, 'max_tokens': max_tokens, 'temperature': 0.0}
    req = urllib.request.Request(f'http://127.0.0.1:{PORT}/v1/chat/completions', data=json.dumps(body).encode(), headers=headers)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        resp = json.load(r)
    return resp, time.time() - t0
messages = [{'role': 'system', 'content': SYSTEM}]
rows = []
t_start = time.time()
for turn in range(1, 9):
    messages.append({'role': 'user', 'content': user_text(turn)})
    resp, wall = call(messages, 48)
    tm = resp.get('timings', {})
    row = {'turn': turn, 'prompt_n': tm.get('prompt_n'), 'cache_n': tm.get('cache_n'),
           'prompt_ms': round(tm.get('prompt_ms') or 0,1), 'prompt_tps': round(tm.get('prompt_per_second') or 0,1),
           'gen_tps': round(tm.get('predicted_per_second') or 0,1),'wall_s': round(wall,2),
           'ttft_s': round((tm.get('prompt_ms') or 0)/1000.0,3), 'content': (resp['choices'][0]['message'].get('content') or '')[:60].replace(chr(10),' ')}
    rows.append(row); print(json.dumps(row, ensure_ascii=False), flush=True)
    messages.append({'role': 'assistant', 'content': tool_call(turn)})
    messages.append({'role': 'tool', 'content': truncate(tool_result(turn), ARGS.truncate_chars)})
    time.sleep(0.2)
total = time.time() - t_start
json.dump({'rows': rows, 'total_s': round(total,1), 'total_ttft_s': round(sum(r['ttft_s'] for r in rows),1)}, open(ARGS.out,'w'), indent=2)
print(json.dumps({'total_s': round(total,1), 'total_ttft_s': round(sum(r['ttft_s'] for r in rows),1)}))
