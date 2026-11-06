import json, time, urllib.request, sys
port=sys.argv[1]; key=sys.argv[2]; target=int(sys.argv[3])
base=open('/tmp/p128k.txt').read()
text='COLD START. ' + base[: int(len(base)*target/117789.0)]
headers={'Content-Type':'application/json','Authorization':'Bearer '+key}
for _ in range(2):
    req=urllib.request.Request(f'http://127.0.0.1:{port}/tokenize', data=json.dumps({'content':text}).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r: n=len(json.load(r)['tokens'])
    if abs(n-target) <= max(64, target//200): break
    text='COLD START. ' + base[: max(1, int((len(text)-12)*target/float(n)))]
body={'messages':[{'role':'user','content':text}],'max_tokens':16,'temperature':0.0,'cache_prompt':False}
req=urllib.request.Request(f'http://127.0.0.1:{port}/v1/chat/completions', data=json.dumps(body).encode(), headers=headers)
t0=time.time()
try:
    with urllib.request.urlopen(req, timeout=900) as r: resp=json.load(r)
except Exception as e:
    print('ERR', repr(e)[:200]); raise SystemExit(1)
tm=resp.get('timings',{})
print('target=%d tokenized=%d wall=%.1fs prompt_n=%s cache_n=%s prefill_tps=%s gen_tps=%s' % (target,n,time.time()-t0,tm.get('prompt_n'),tm.get('cache_n'),round(tm.get('prompt_per_second') or 0,1),round(tm.get('predicted_per_second') or 0,1)))
