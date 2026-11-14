// Injection-time tool-result trimming proxy + bearer auth, fronting llama-server.
//
//   公网 0.0.0.0:14802 -> this proxy -> 127.0.0.1:14803 (llama-server, loopback only)
//
// Responsibilities
//   1. auth: every request (except CORS preflight) must carry "Authorization: Bearer <key>"
//      or "x-api-key: <key>"; otherwise 401 with a JSON error body.
//   2. trim: role=tool content over TRIM_MAX chars becomes head + marker + tail. Non-tool
//      messages are forwarded byte-identical (the JSON is re-serialized only when a trim happened).
//   3. stream: SSE responses are piped chunk by chunk with no buffering, so token streaming is
//      preserved end to end.
//
// Env: TRIM_API_KEY (else a random 24-byte hex key is generated and printed once),
//      UPSTREAM (default http://127.0.0.1:14803), TRIM_MAX/TRIM_HEAD/TRIM_TAIL (2048/1000/1000),
//      LISTEN_HOST/LISTEN_PORT (0.0.0.0 / 14802),
//      UPSTREAM_GRAY + GRAYSCALE_PERCENT (0 = off) for a sticky session-based rollout.
import http from 'node:http';
import https from 'node:https';
import crypto from 'node:crypto';
import { URL } from 'node:url';

const UPSTREAM = process.env.UPSTREAM || 'http://127.0.0.1:14803';
const LISTEN_HOST = process.env.LISTEN_HOST || '127.0.0.1';
const LISTEN_PORT = Number(process.env.LISTEN_PORT || 14802);
const MAX = Number(process.env.TRIM_MAX || 2048);
const HEAD = Number(process.env.TRIM_HEAD || 1000);
const TAIL = Number(process.env.TRIM_TAIL || 1000);
const API_KEY = process.env.TRIM_API_KEY;
// Optional second backend for staged rollouts. Disabled while GRAYSCALE_PERCENT is 0.
// Selection is sticky per session when the caller sends a session/ conversation id.
const UPSTREAM_GRAY = process.env.UPSTREAM_GRAY || '';
const GRAYSCALE_PERCENT = Number(process.env.GRAYSCALE_PERCENT || 0);
const TRIM_DEBUG = process.env.TRIM_DEBUG === '1';
const VLLM_UPSTREAM = process.env.VLLM_UPSTREAM || 'http://127.0.0.1:14806';
const ROUTING_MODE = process.env.ROUTING_MODE || 'prism_only'; // prism_only | auto
const VLLM_MAX_LEN = Number(process.env.VLLM_MAX_LEN || 8192);
const MAX_BODY = Number(process.env.TRIM_MAX_BODY || 64 * 1024 * 1024);
const GRAY_SALT = process.env.TRIM_GRAY_SALT || '';
if (!(MAX > HEAD + TAIL)) {
  throw new Error('TRIM_MAX must be greater than TRIM_HEAD + TRIM_TAIL');
}
if (!process.env.TRIM_API_KEY) {
  throw new Error('TRIM_API_KEY is required');
}

function sessionKey(headers, parsed) {
  const h = headers['x-session-id'] || headers['x-conversation-id'] || headers['x-request-session'];
  if (h) return String(h);
  if (parsed && typeof parsed === 'object') {
    const v = parsed.session_id || parsed.conversation_id || parsed.user;
    if (v) return String(v);
  }
  return '';
}

function bucketOf(key) {
  if (!key) return Math.floor(Math.random() * 100);
  const h = crypto.createHash('sha256').update(GRAY_SALT + ':' + String(key)).digest();
  return h.readUInt32BE(0) % 100;
}

export function pickUpstream(headers, parsed) {
  if (!UPSTREAM_GRAY || GRAYSCALE_PERCENT <= 0) return UPSTREAM;
  if (!GRAY_SALT) return UPSTREAM;
  return bucketOf(sessionKey(headers || {}, parsed)) < GRAYSCALE_PERCENT ? UPSTREAM_GRAY : UPSTREAM;
}

function contentTokens(parsed) {
  let chars = 0;
  const walk = (v) => {
    if (typeof v === 'string') chars += v.length;
    else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === 'object') Object.values(v).forEach(walk);
  };
  walk(parsed && parsed.messages);
  walk(parsed && parsed.prompt);
  return Math.ceil(chars / 4);
}

function hasMedia(parsed) {
  let found = false;
  const walk = (v) => {
    if (found) return;
    if (Array.isArray(v)) return v.forEach(walk);
    if (!v || typeof v !== 'object') return;
    if (v.type === 'image_url' || v.type === 'input_image' || v.type === 'audio_url' || v.type === 'video_url' || v.image_url || v.input_image) { found = true; return; }
    Object.values(v).forEach(walk);
  };
  walk(parsed && parsed.messages);
  return found;
}

function decideBackend(headers, parsed) {
  const forced = String(headers['x-backend'] || '').toLowerCase();
  if (forced === '1cat' || forced === 'vllm') return 'vllm';
  if (forced === 'prism') return 'prism';
  if (ROUTING_MODE !== 'auto') return 'prism';
  const model = parsed && String(parsed.model || '').toLowerCase();
  if (model === '1cat-vllm' || model === 'qwen3.8-27b') return 'vllm';
  if (String(headers['x-mtp'] || '') === '1' || (parsed && parsed.mtp === true)) return 'vllm';
  if (hasMedia(parsed)) return 'prism';
  if (contentTokens(parsed) > VLLM_MAX_LEN) return 'prism';
  if (parsed && parsed.stream === true) return 'vllm';
  return 'prism';
}

export function trimText(text) {
  if (typeof text !== 'string' || text.length <= MAX) return text;
  const dropped = text.length - HEAD - TAIL;
  return text.slice(0, HEAD) + '\n...[truncated ' + dropped + ' chars]...\n' + text.slice(-TAIL);
}

function trimContent(content) {
  if (typeof content === 'string') return trimText(content);
  if (Array.isArray(content)) {
    for (const block of content) {
      if (block && typeof block === 'object' && typeof block.text === 'string') {
        block.text = trimText(block.text);
      }
    }
    return content;
  }
  if (content && typeof content === 'object') {
    if (typeof content.text === 'string') content.text = trimText(content.text);
    else if (Array.isArray(content.content)) trimContent(content.content);
  }
  return content;
}

export function trimBody(obj) {
  if (!obj || !Array.isArray(obj.messages)) return obj;
  for (const msg of obj.messages) {
    if (!msg || typeof msg !== 'object') continue;
    if (msg.role === 'tool' || msg.role === 'tool_result' || msg.type === 'tool_result') {
      msg.content = trimContent(msg.content);
    }
  }
  return obj;
}

export function describeMessages(obj) {
  if (!obj || !Array.isArray(obj.messages)) return null;
  let chars = 0;
  let images = 0;
  const roles = {};
  const perMsg = [];
  for (const m of obj.messages) {
    const role = (m && m.role) || '?';
    roles[role] = (roles[role] || 0) + 1;
    let len = 0;
    let hasImage = false;
    const types = [];
    const c = m && m.content;
    if (typeof c === 'string') {
      len = c.length;
    } else if (Array.isArray(c)) {
      for (const b of c) {
        if (!b || typeof b !== 'object') continue;
        if (b.type) types.push(b.type);
        if (b.type === 'image_url') hasImage = true;
        if (typeof b.text === 'string') len += b.text.length;
        if (b.image_url && typeof b.image_url.url === 'string') len += b.image_url.url.length;
      }
    } else if (c && typeof c === 'object') {
      len = JSON.stringify(c).length;
    }
    chars += len;
    if (hasImage) images += 1;
    perMsg.push({ role, len, image: hasImage, types: types.join(',') });
  }
  return { chars, images, roles, perMsg };
}

function logRequest(idx, req, parsed, rawLen) {
  if (!TRIM_DEBUG) return;
  const d = describeMessages(parsed);
  const total = parsed && Array.isArray(parsed.messages) ? JSON.stringify(parsed.messages).length : 0;
  process.stderr.write('[debug] #' + idx + ' ' + req.method + ' ' + req.url +
    ' raw=' + rawLen + ' msgs=' + (parsed && parsed.messages ? parsed.messages.length : -1) +
    ' sumChars=' + (d ? d.chars : -1) + ' msgsJson=' + total +
    ' images=' + (d ? d.images : -1) + ' roles=' + JSON.stringify(d ? d.roles : {}) + '\n');
  if (d) process.stderr.write('[debug] #' + idx + ' per-msg ' + JSON.stringify(d.perMsg).slice(0, 3000) + '\n');
  if (total > 10000) {
    process.stderr.write('[debug] #' + idx + ' messages-prefix ' + JSON.stringify(parsed.messages).slice(0, 500) + '\n');
  }
}

function authorized(headers) {
  const auth = headers['authorization'] || '';
  const bearer = auth.startsWith('Bearer ') ? auth.slice(7) : '';
  return bearer === API_KEY || headers['x-api-key'] === API_KEY;
}

const agent = new URL(UPSTREAM).protocol === 'https:' ? https : http;
const stats = { requests: 0, denied: 0, trimmed: 0, bytesIn: 0, bytesOut: 0 };

function is_ui_request(req) {
  if (req.method !== 'GET' && req.method !== 'HEAD') return false;
  const p = String(req.url || '/').split('?')[0];
  if (p === '/' || p === '/index.html' || p === '/manifest.webmanifest' || p === '/sw.js' || p === '/props') return true;
  if (p.startsWith('/_app/')) return true;
  if (/^\/(favicon|apple-)[^/]*$/.test(p)) return true;
  return /^\/[^/]+\.(js|css|png|jpe?g|svg|ico|json|webmanifest|woff2?|ttf|map)$/.test(p);
}

const server = http.createServer((req, res) => {
  req.setTimeout(120000, () => req.destroy());
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }
  if (!authorized(req.headers) && !is_ui_request(req)) {
    stats.denied += 1;
    res.writeHead(401, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: { message: 'invalid or missing API key', type: 'auth_error' } }));
    return;
  }
  const chunks = [];
  let upstreamReq = null;
  let aborted = false;
  const abortUpstream = (why) => {
    if (aborted) return;
    aborted = true;
    stats.aborted = (stats.aborted || 0) + 1;
    process.stderr.write('[abort] #' + stats.requests + ' ' + why + '\n');
    if (upstreamReq) upstreamReq.destroy();
  };
  req.on('aborted', () => abortUpstream('client aborted request'));
  res.on('close', () => {
    if (!res.writableEnded && !res.writableFinished) abortUpstream('client closed connection');
  });
  let bytes = 0;
  req.on('data', (c) => {
    bytes += c.length;
    if (bytes > MAX_BODY) {
      if (!res.headersSent) {
        res.writeHead(413, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ error: { message: 'request body too large', type: 'invalid_request' } }));
      }
      req.destroy();
      return;
    }
    chunks.push(c);
  });
  req.on('end', () => {
    let body = Buffer.concat(chunks);
    stats.requests += 1;
    const type = String(req.headers['content-type'] || '');
    let parsed = null;
    if (body.length && type.includes('json')) {
      try {
        const obj = JSON.parse(body.toString('utf8'));
        parsed = obj;
        logRequest(stats.requests, req, obj, body.length);
        const before = body.length;
        trimBody(obj);
        const after = Buffer.from(JSON.stringify(obj));
        if (after.length !== before) {
          stats.trimmed += 1; stats.bytesIn += before; stats.bytesOut += after.length;
          process.stderr.write('[trim] #' + stats.requests + ' ' + before + ' -> ' + after.length + ' bytes\n');
          body = after;
        }
      } catch { /* unparsable body: forward untouched */ }
    }
    const backend = decideBackend(req.headers, parsed);
    const upstreamBase = backend === 'vllm' ? VLLM_UPSTREAM : pickUpstream(req.headers, parsed);
    const up = new URL(upstreamBase + req.url);
    const headers = { ...req.headers, host: up.host };
    if (is_ui_request(req) && String(req.url || "").split("?")[0] === "/props" && !headers["authorization"] && !headers["x-api-key"]) {
      headers["authorization"] = "Bearer " + API_KEY;
    }
    for (const h of ['connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
                     'te', 'trailer', 'transfer-encoding', 'upgrade']) {
      delete headers[h];
    }
    delete headers['content-length'];
    headers['content-length'] = String(body.length);
    if (backend === 'vllm') {
      stats.vllm = (stats.vllm || 0) + 1;
      process.stderr.write('[route] #' + stats.requests + ' -> vllm ' + upstreamBase + '\n');
    } else if (upstreamBase !== UPSTREAM) {
      stats.gray = (stats.gray || 0) + 1;
      process.stderr.write('[gray] #' + stats.requests + ' -> ' + upstreamBase + '\n');
    }
    if (aborted) return;
    upstreamReq = agent.request(
      { protocol: up.protocol, hostname: up.hostname, port: up.port || undefined,
        path: up.pathname + up.search, method: req.method, headers },
      (upstreamRes) => {
        // Streaming pass-through: headers first, then raw chunks. No buffering, no re-chunking.
        res.writeHead(upstreamRes.statusCode || 502, { ...upstreamRes.headers, 'x-backend': backend });
        res.flushHeaders?.();
        upstreamRes.pipe(res, { end: true });
      });
    upstreamReq.on('error', (e) => {
      if (!res.headersSent) res.writeHead(502, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ error: { message: 'upstream error: ' + String(e) } }));
    });
    upstreamReq.end(body);
  });
});

if (process.env.PROXY_LIB_ONLY !== '1') {
  server.listen(LISTEN_PORT, LISTEN_HOST, () => {
    process.stdout.write('[trim] listening on ' + LISTEN_HOST + ':' + LISTEN_PORT + ' -> ' + UPSTREAM +
      ' (max=' + MAX + ' head=' + HEAD + ' tail=' + TAIL + ' routing=' + ROUTING_MODE + ')\n');
  });
}
export { server, stats, API_KEY };
