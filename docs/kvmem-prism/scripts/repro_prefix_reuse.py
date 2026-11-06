import json, time, urllib.request, sys
port=sys.argv[1]; key=sys.argv[2]; ngen=int(sys.argv[3]) if len(sys.argv)>3 else 600
base=open('/tmp/p128k.txt').read()
headers={'Content-Type':'application/json','Authorization':'Bearer '+key}
def tok(text):
    req=urllib.request.Request(f'http://127.0.0.1:{port}/tokenize', data=json.dumps({'content':text}).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r: return len(json.load(r)['tokens'])
def mk(n):
    t=base[: int(len(base)*n/117789.0)]
    for _ in range(3):
        c=tok(t)
        if abs(c-n)< n//100: break
        t=base[: max(1,int(len(t)*n/float(c)))]
    return t
def send(text, tag):
    body={'messages':[{'role':'user','content':text}],'max_tokens':ngen,'temperature':0.0}
    req=urllib.request.Request(f'http://127.0.0.1:{port}/v1/chat/completions', data=json.dumps(body).encode(), headers=headers)
    t0=time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as r: resp=json.load(r)
    except Exception as e:
        print(tag,'ERR',repr(e)[:160], flush=True); return False
    tm=resp.get('timings',{})
    print('%s wall=%.1fs prompt_n=%s cache_n=%s gen=%s' % (tag, time.time()-t0, tm.get('prompt_n'), tm.get('cache_n'), tm.get('predicted_n')), flush=True)
    return True
a=mk(22000)
pre=a[: int(len(a)*8770/22000.0)]
b=pre + base[200000:200000+int(len(base)*8200/117789.0)]
print('A tokens', tok(a), 'B tokens', tok(b), 'gen', ngen, flush=True)
send(a,'A_22k')
send(b,'B_16k_lcp8.7k')
