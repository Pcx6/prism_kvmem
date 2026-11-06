import argparse, base64, json, time, urllib.request
ap = argparse.ArgumentParser()
ap.add_argument('--port', default='14803'); ap.add_argument('--key', required=True)
ap.add_argument('--image', required=True); ap.add_argument('--prompt', default='请提取图片中的所有文字，逐行输出，不要解释。')
ap.add_argument('--max-tokens', type=int, default=512)
a = ap.parse_args()
ext = a.image.rsplit('.',1)[-1].lower()
mime = 'image/png' if ext == 'png' else 'image/jpeg' if ext in ('jpg','jpeg') else 'image/webp'
b64 = base64.b64encode(open(a.image,'rb').read()).decode()
body = {'messages':[{'role':'user','content':[
    {'type':'text','text':a.prompt},
    {'type':'image_url','image_url':{'url':'data:%s;base64,%s' % (mime, b64)}}]}],
    'max_tokens': a.max_tokens, 'temperature': 0.0}
req = urllib.request.Request('http://127.0.0.1:%s/v1/chat/completions' % a.port,
    data=json.dumps(body).encode(),
    headers={'Content-Type':'application/json','Authorization':'Bearer '+a.key})
t0 = time.time()
with urllib.request.urlopen(req, timeout=900) as r: resp = json.load(r)
tm = resp.get('timings', {})
msg = resp['choices'][0]['message']
print(json.dumps({'image': a.image.split('/')[-1], 'wall_s': round(time.time()-t0,2),
    'prompt_n': tm.get('prompt_n'), 'ttft_s': round((tm.get('prompt_ms') or 0)/1000.0,2),
    'gen_tps': round(tm.get('predicted_per_second') or 0,1),
    'text': (msg.get('content') or '').strip()[:1200]}, ensure_ascii=False, indent=1))
