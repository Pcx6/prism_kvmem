import base64, json, time, urllib.request, sys
port=sys.argv[1]; key=sys.argv[2]
img='/workspace/phototest/微信图片_20261010203406_5_8.jpg'
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
def send(parts, tag):
    body={'messages':[{'role':'user','content':parts}],'max_tokens':120,'temperature':0.0}
    req=urllib.request.Request(f'http://127.0.0.1:{port}/v1/chat/completions', data=json.dumps(body).encode(), headers=headers)
    t0=time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as r: resp=json.load(r)
    except Exception as e:
        print(tag,'ERR',repr(e)[:200], flush=True); return False
    tm=resp.get('timings',{})
    print('%s wall=%.1fs prompt_n=%s cache_n=%s' % (tag, time.time()-t0, tm.get('prompt_n'), tm.get('cache_n')), flush=True)
    return True
pre=mk(8000)
b64=base64.b64encode(open(img,'rb').read()).decode()
parts_a=[{'type':'text','text':pre + '\n\n请描述这张图片。'}, {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+b64}}]
parts_b=[{'type':'text','text':pre + base[300000:300000+int(len(base)*8000/117789.0)]}]
send(parts_a,'A_img')
send(parts_b,'B_text_reuse8k')
