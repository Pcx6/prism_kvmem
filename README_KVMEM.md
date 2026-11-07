# Prism x KVMem 生产融合说明

本仓库是 Prism（qwen35 架构 27B 三值量化模型）的 llama.cpp 分支，把 KVMem 主机侧 KV 换页适配器编译进 Prism，并在此基础上完成：
请求级检索 span 注入、短上下文短路、前缀复用、多模态视觉、以及生产部署 14803 的运维脚本。

生产入口：`14803`（llama-server，KVMem + Vision，API 名 `Bonsai-2-27B`）→ `14802`（trim_proxy.mjs，裁剪 tool 结果并转发）。


## 1. 生产现状速览

| 项目 | 值 |
|---|---|
| 模型 | `/workspace/baseline_llama/models/Ternary-Bonsai-2-27B-PTQ1_0.gguf`（5.6 GB，qwen35 hybrid GDN，262144 ctx） |
| 视觉塔 | `/srv/models/Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf`（601 MB，`qwen3vl_merger`，GPU offload） |
| API 名称 | `Bonsai-2-27B`（启动参数 `-a Bonsai-2-27B`） |
| KVMem | `--kvmem --kvmem-budget 12288 --kvmem-gen-reserve 22528`（GPU 池 34816 token） |
| 短路阈值 | `0.95 x pool = 33075` token，低于此值走原生注意力快速路径 |
| 显存 | 启动 9395 MiB（含视觉），峰值约 9.7-9.9 GB / 16 GB |
| 长文 | 128K prefill 约 564 t/s，250K 约 546 t/s（换页开启） |
| 短请求 | 32K 冷启动约 690 t/s；8 轮 Agent 23-24 s（总 TTFT 13.6-14.1 s） |
| 视觉 | OCR 与图片描述正常，TTFT 1.0-3.6 s，单图 8-29 s |
| KV 空闲回收 | `--kvmem-idle-ttl 86400`（1 天）；到期释放 host KV，日志 `KVMEM_IDLE_EVICT` |
| 回滚 | `/srv/llama_agent/start-llama-14803-prism.sh`（原生 prism，无 KVMem/视觉） |


## 2. 架构

### 2.1 内存分层

- `llama_memory_kvmem_hybrid`：attention 层用 KVMem 的 slot 池，recurrent（GDN）层保持原生。
- GPU 只保留 `budget + gen_reserve` 个 token 的 KV（槽池），冷块换到主机内存；检索时按 query 打分把需要的块取回。
- 池大小 = `budget + gen_reserve`；高水位 0.95（超过触发换页），低水位收缩回 `budget`。
- 换页/检索打分需要请求级 query span（见 2.2）。

### 2.2 请求级 span 注入与检索

1. `/v1/chat/completions` 取最后一条 user 消息，在渲染后的 prompt 里定位它的 token 区间。
2. 区间截到最近 512 token（与参考驱动 `--kvmem-query-max-tokens` 默认一致）；模板改写导致定位失败时回退到 prompt 末尾 512 token。
3. 在 `launch_slot_with_task` 里调 `llama_kvmem_set_request_span(qbegin, qend, force)`，日志 `KVMEM_REQUEST_SPAN begin=... end=...`。
4. prefill 结束后调 `end_prefill_capture / apply_retrieval / pin_working_set / attention_begin`，把冷块拉回 GPU 再进入生成。
5. 打分用 CUDA 版 page softmax（`KVMEM_PAGE_SOFTMAX_CUDA`）；CPU 版在 256K 上会跑 >10 分钟。

### 2.3 短上下文短路

- 当请求 token 数 < 池高水位（33075）时，请求完全驻留 GPU，不需要主机镜像：
  - 不注入 span；`begin_cached_turn / truncate_cached` 后立即 `end_prefill_capture`，prefill 不再 capture/harvest K/Q；
  - prefill 结束跳过 `apply_retrieval / pin_working_set / attention_begin`；
  - 日志 `KVMEM_SHORT_BYPASS tokens=...`。
- 跨过阈值（例如 8K 轮次之后接 40K）时日志出现 `KVMEM_BYPASS_EXIT`，强制整体重算一次，保证主机镜像完整。
- 该短路把 8 轮 Agent 从 34.0 s 降到 23-24 s。

### 2.4 前缀复用

- server 用 token 公共前缀算 `n_past`，随后 `llama_kvmem_truncate_cached(n_past)` 对齐 KVMem store，再 `seq_rm` 清原生 KV。
- KVMem 的 `seq_pos_min` 返回的是原生行位置而非逻辑 0，会触发 Prism 的 “无 checkpoint 就整体重算” 分支；已对 KVMem 加守卫，保留前缀复用。
- `--cache-reuse` 对 KVMem 自动禁用（`cache_reuse is not supported by this context`），属设计取舍；多轮收益来自 prompt 前缀复用。

### 2.5 多模态行号（关键修复）

- qwen3vl 的 M-RoPE 下，图像 chunk 的 token 数与位置数不一致：实测 `n_tokens=330, n_pos=22`。
- KVMem 的缓存行按 token 追加，位置只用于 RoPE/掩码；两者必须分开：
  - server 每个 batch 显式携带 `logical_pos`（prompt 行号 = `prompt.n_tokens()`，生成行号连续）；
  - 图像 embedding batch 通过 `mtmd_helper_decode_image_chunk_rows(..., row_0, ...)` 指定起始行；
  - 文本行紧接图像行，RoPE 位置仍按 M-RoPE 推进。
- 该修复消除 “completion leaves a hole or exceeds reservation”（详见第 8 节）。


## 3. 构建

环境：V100 16 GB，CUDA 12.8，sm_70，24 线程。

```bash
cd /workspace/prism_kvmem
cmake -B build-sm70 \
  -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=70 \
  -DLLAMA_KVMEM=ON \
  -DLLAMA_KVMEM_ROOT=/workspace/baseline_llama/src/kvmem-root \
  -DLLAMA_BUILD_SERVER=ON -DGGML_NATIVE=ON
cmake --build build-sm70 -j 12 --target llama-server
```

产物：`build-sm70/bin/llama-server` 与同目录动态库（`libllama`、`libggml`、`libmtmd`、`libkvmem`）。运行需设置 `LD_LIBRARY_PATH=build-sm70/bin`。


## 4. 运行

### 4.0 快速上手（5 步）

```bash
# 1. 构建（首次或改代码后）
cd /workspace/prism_kvmem
cmake -B build-sm70 -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=70 \
  -DLLAMA_KVMEM=ON -DLLAMA_KVMEM_ROOT=/workspace/baseline_llama/src/kvmem-root \
  -DLLAMA_BUILD_SERVER=ON -DGGML_NATIVE=ON
cmake --build build-sm70 -j 12 --target llama-server

# 2. 启动推理服务（14803，KVMem + 视觉 + TTL 1 天）
nohup setsid /srv/llama_agent/start-llama-14803.sh \
  > /srv/llama_agent/fused_prod.log 2>&1 &

# 3. 启动代理（14802，公网入口）
cd /srv/harness_agent
TRIM_API_KEY=$(cat /srv/llama_agent/api_key.txt) UPSTREAM=http://127.0.0.1:14803 \
  LISTEN_HOST=0.0.0.0 LISTEN_PORT=14802 nohup setsid node trim_proxy.mjs \
  > /srv/harness_agent/trim_14802.log 2>&1 &

# 4. 健康检查
KEY=$(cat /srv/llama_agent/api_key.txt)
curl -s -H "Authorization: Bearer $KEY" http://127.0.0.1:14803/health   # 200
curl -s -H "Authorization: Bearer $KEY" http://127.0.0.1:14802/health   # 200

# 5. 发请求
curl -s http://127.0.0.1:14802/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"Bonsai-2-27B","messages":[{"role":"user","content":"你好"}],"max_tokens":64}'
```

停止服务：`pgrep -a -x llama-server` 找到 pid 后 `kill -9`；代理同理按 LISTEN_PORT=14802 精确杀。
空闲 KV 清理：默认 1 天（`--kvmem-idle-ttl 86400`），会释放 host 内存；不等 TTL 时可用 `POST /slots/0?action=erase` 立即清空（见 4.6）。

### 4.1 生产启动

`/srv/llama_agent/start-llama-14803.sh`：

```bash
#!/bin/bash
set -u
ulimit -c 0
export LD_LIBRARY_PATH=/workspace/prism_kvmem/build-sm70/bin
KEY=$(cat /srv/llama_agent/api_key.txt)
exec /workspace/prism_kvmem/build-sm70/bin/llama-server \
  -m /workspace/baseline_llama/models/Ternary-Bonsai-2-27B-PTQ1_0.gguf \
  --mmproj /srv/models/Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf \
  -a Bonsai-2-27B \
  -c 262144 -ngl 99 -t 24 -np 1 --reasoning on \
  -ub 1024 -b 4096 -fa on --cache-reuse 256 --slot-save-path /srv/llama_agent/slots \
  --cache-ram 0 -ctxcp 0 \
  --kvmem --kvmem-budget 12288 --kvmem-gen-reserve 22528 --kvmem-idle-ttl 86400 \
  --host 127.0.0.1 --port 14803 --api-key "$KEY"
```

### 4.2 代理

14802 由 `/srv/harness_agent/trim_proxy.mjs` 提供，只裁剪 `role=tool` 的超长内容，不处理图片：

```bash
cd /srv/harness_agent
TRIM_API_KEY=$(cat /srv/llama_agent/api_key.txt) \
UPSTREAM=http://127.0.0.1:14803 \
LISTEN_HOST=0.0.0.0 LISTEN_PORT=14802 \
node trim_proxy.mjs
```

### 4.3 调用示例

```bash
KEY=$(cat /srv/llama_agent/api_key.txt)

# 文本
curl http://127.0.0.1:14802/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"Bonsai-2-27B","messages":[{"role":"user","content":"你好"}],"max_tokens":64}'

# 视觉（base64 data URL）
python3 docs/kvmem-prism/scripts/test_vision.py --port 14802 --key "$KEY" \
  --image /workspace/phototest/微信图片_20261003195102_3_8.png \
  --prompt '逐字提取图片中的全部文字。' --max-tokens 800
```

### 4.4 启停与健康检查

```bash
pgrep -a -x llama-server                 # 生产进程
kill -9 <pid>                            # 服务会忽略 SIGTERM，需 -9
nohup setsid /srv/llama_agent/start-llama-14803.sh \
  > /srv/llama_agent/fused_prod.log 2>&1 &

curl -H "Authorization: Bearer $(cat /srv/llama_agent/api_key.txt)" \
  http://127.0.0.1:14803/health      # 期望 200
curl -H "Authorization: Bearer $(cat /srv/llama_agent/api_key.txt)" \
  http://127.0.0.1:14802/health      # 期望 200
nvidia-smi --query-gpu=memory.used --format=csv,noheader   # 期望 9-10 GB
```

### 4.5 回滚

| 目标 | 脚本 |
|---|---|
| 原生 prism（无 KVMem、无视觉，131072 ctx） | `/srv/llama_agent/start-llama-14803-prism.sh` |
| 有 KVMem、无视觉 | `/srv/llama_agent/start-llama-14803.sh.bak-mmproj` |
| 14805 测试实例（融合版） | `/srv/llama_agent/start-llama-fused.sh` |

### 4.6 KV 空闲 TTL 与手动清理

```bash
# 默认 1 天；测试可用 10 秒；0 关闭
--kvmem-idle-ttl 86400     # 生产
--kvmem-idle-ttl 10        # 验证
--kvmem-idle-ttl 0         # 禁用

# 立即清空 slot 0 的 KV（行/块）；host RSS 由 TTL 路径归还
curl -X POST -H "Authorization: Bearer $KEY" \
  "http://127.0.0.1:14803/slots/0?action=erase"
```

- 扫描周期约 1 秒（队列空闲 tick），只处理 `state == IDLE` 的 slot。
- 超时后 `prompt_clear()`（KVMem full reset）+ `malloc_trim(0)`，日志 `KVMEM_IDLE_EVICT reason=idle_ttl idle_ms=... n_tokens=...`。
- 实测释放：64K 会话 RSS 5.63 GB -> 1.27 GB（TTL=5 s）；250K 会话 17.96 GB -> 1.33 GB（TTL=10 s）。
- OOM 兜底：`MemAvailable < 1.5 GB` 或 GPU free < 1 GB 时强制清理 IDLE slot（`reason=oom_guard`）；decode 分配失败时清理并重试一次（`reason=oom_retry`）。
- 说明：GPU KV 池是启动时固定分配（约 2.1-2.2 GB），TTL 释放的是池内行和主机内存，不会把池显存归还驱动。


## 5. 参数选型

### 5.1 budget 扫描（128K 请求，16 GB 卡）

| budget | 启动显存 | 128K 峰值 | 128K prefill / decode | 短路实测 |
|---|---|---|---|---|
| 12288 | 7647 MiB | ~7.9 GB | 741.8 / 53.9 | 819.8 t/s @14.3K |
| 16384 | 7487 | 7901 | 681.5 / 51.3 | 819.8 @14.3K |
| 24576 | 8015 | 8527 | 598.9 / 48.0 | 754.8 @22.5K |
| 32768 | 8543 | 8959 | 541.5 / 45.2 | 30.7K 走短路 |
| 40960 | 9071 | 9503 | 497.7 / 27.3 | 648.9 @38.9K |
| 49152 | 9599 | 10033 | 463.0 / 25.8 | 604.9 @47.1K |

结论：显存在 16 GB 内不是瓶颈，**池越大长文 prefill 越慢**（换页收缩后的注意力窗口 = 池大小）。

### 5.2 14 GB 池实验（按每 GB 收益 5% 规则否决）

| 池大小 | 启动 | 峰值 | 128K prefill/decode | 256K prefill/decode |
|---|---|---|---|---|
| 34816 | 8543 | 8929 | 573.2 / 52.5 | 542.8 / 47.5 |
| 106496 | 13163 | 13605 | 390.8 / 52.6 | - |
| 131072（适配器上限） | 14623 | 15019 | 387.6 / 23.6 | - |
| 114688（上限 - 1 GB） | 13691 | 14379 | ~390 | 315.9 / 48.6 |

9.3 GB -> 14.4 GB 显存增加 5.1 GB，长文 prefill 反而下降 32-42%，负相关，按规则放弃。

### 5.3 最终配置

`--kvmem-budget 12288 --kvmem-gen-reserve 22528`（池 34816，短路阈值 33075）：
32K 以内全走原生快速路径，128K/256K 保留 KVMem 换页，显存 9.3-9.9 GB，留 6 GB 余量。

跨阈值场景（8K -> 40K -> 8K）：8.9 s -> 63.1 s（`KVMEM_BYPASS_EXIT` 整体重算）-> 9.3 s，行为正常。


## 6. 极限上下文实测（收口）

测试时间 2026-11-06，生产 14803（KVMem 池 34816 + 视觉 + API 名 Bonsai-2-27B），全部经 14802 代理，V100 16 GB。

### 6.1 测试方法

- 每个尺寸独立冷请求：`cache_prompt: false`、`max_tokens: 32`、`temperature: 0`，避免前缀缓存互相影响。
- 指标：prompt_n（实际 prefill token 数）、prefill t/s、TTFT（服务端 prompt_ms）、decode t/s、wall、显存峰值（1.5 s 采样）。
- 热缓存测试：先冷跑一次长 prompt，再在同一 prompt 尾部追加一句短问题（`cache_prompt: true`），只计算新增 token。

### 6.2 冷上下文渐进（8K 到 250K）

| 目标 | prompt_n | prefill t/s | TTFT | decode t/s | wall | 显存峰值 |
|---|---|---|---|---|---|---|
| 8K | 8,244 | 825.2 | 9.99 s | 57.8 | 10.6 s | 10733 MiB |
| 16K | 16,436 | 795.4 | 20.66 s | 53.9 | 21.4 s | 10693 MiB |
| 24K | 24,628 | 742.8 | 33.16 s | 50.3 | 34.0 s | 10733 MiB |
| 32K | 32,820 | 679.9 | 48.28 s | 47.0 | 49.3 s | 10693 MiB |
| 40K | 41,012 | 648.8 | 63.22 s | 54.0 | 64.2 s | 10733 MiB |
| 48K | 49,204 | 621.7 | 79.14 s | 54.0 | 80.2 s | 10733 MiB |
| 64K | 65,588 | 602.0 | 108.95 s | 53.9 | 110.2 s | 10741 MiB |
| 96K | 98,356 | 578.9 | 169.91 s | 53.8 | 171.5 s | 10781 MiB |
| 128K | 131,124 | 564.1 | 232.44 s | 54.3 | 234.4 s | 10703 MiB |
| 192K | 196,660 | 553.1 | 355.57 s | 53.4 | 358.3 s | 10781 MiB |
| **250K** | **250,053** | **546.0** | **457.93 s** | 52.7 | 461.2 s | 10781 MiB |

曲线特征：

- TTFT 近似线性：8K 为 1.21 ms/token，250K 为 1.83 ms/token；250K 冷启动约 7.6 分钟。
- prefill 从 825 t/s 降到 546 t/s（-34%）；32K 及以下走短路（680-825 t/s），33K 以上进入 KVMem 换页（546-649 t/s），**没有断崖**。
- decode 全程 47-58 t/s，与上下文长度基本无关。
- 显存峰值 10.7-10.8 GB，不随长度增长；250K（接近 262144 上限）无 OOM、无 hole。
- 250K 冷启动复测 548.6 t/s / TTFT 455.8 s，与上表一致。

### 6.3 热缓存长上下文 TTFT（同一长对话第二轮）

| 上下文 | cache_n | 新增 token | TTFT | wall |
|---|---|---|---|---|
| 128K | 131,117 | 13 | **1.79 s** | 3.89 s |
| 192K | 196,653 | 13 | **2.56 s** | 5.36 s |
| 250K | 250,046 | 13 | **3.19 s** | 6.58 s |

第二轮只计算 13 个新 token，TTFT 1.8-3.2 s（含 KVMem 截断/检索固定开销），相比冷启动的 231-456 s，这是长对话可用的关键指标。

### 6.4 Agent 8 轮（短请求 + 上下文增长）

| 场景 | 结果 |
|---|---|
| 原生产 14803（原生 prism） | 24.6 s / TTFT 16.3 s |
| 融合 + 短路（当前生产） | 23.1-24.4 s / TTFT 13.6-14.1 s |
| 单轮 prompt 约 1.5K | prefill 750-900 t/s，TTFT 1.1-2.0 s |

### 6.5 视觉

| 图片 | prompt | TTFT | wall | 结果 |
|---|---|---|---|---|
| 简谱 PNG | 1,986 | 3.65 s | 28.6 s | 歌词/标题/调号基本逐字正确 |
| 简谱 JPG | 344 | 0.69 s | 12.1 s | 歌词整段正确 |
| 风景照片 | 2,059 | 3.55 s | 30.9 s | 描述详实，无幻觉 |

未观察到 V100 典型的整行乱码；视觉默认走 GPU（`--no-mmproj-offload` 可切 CPU，慢约 2 倍，作降级方案）。

### 6.6 中断取消

生成 2 秒后断开：服务端在 `n_tokens=185-251` 处停止（max_tokens=5000），GPU 利用率 98% 在 0.4 秒内回落到 0%，下一次请求正常；连续 3 次中断无 hole。

### 6.7 KV 留存、TTL 与内存回收（2026-11-07 新增）

- 参数：`--kvmem-idle-ttl SECONDS`（默认 86400，0 = 永不清理；env `LLAMA_ARG_KVMEM_IDLE_TTL`），生产脚本已加 `--kvmem-idle-ttl 86400`。
- 实现：队列空闲 tick 每约 1 秒触发一次扫描（不依赖 prompt cache）；只清理 `state == IDLE` 的 slot；执行 `prompt_clear()`（KVMem full reset，释放 host raw/块）+ `malloc_trim(0)` 归还内存。
- 日志：`KVMEM_IDLE_EVICT reason=idle_ttl idle_ms=... n_tokens=...`
- 实测（14805 独立实例）：

| 场景 | 请求后 RSS | TTL 清理后 RSS | 释放 | 触发 |
|---|---|---|---|---|
| 64K，TTL=5 s | 5.63 GB | 1.27 GB | 4.36 GB | idle_ms=5001 |
| 250K，TTL=10 s | 17.96 GB | 1.33 GB | 16.6 GB | idle_ms=10002 |
| 小请求（84 token） | - | - | - | idle_ms=5001 |
| TTL=0（禁用） | 不变 | 不变 | 0 | 8 s 内 0 次 evict |
| TTL=86400 + 8 轮 Agent | - | - | 0 | 23.2 s，前缀复用正常 |

说明：GPU KV 池是启动时固定分配（约 2.1-2.2 GB），清理释放的是池内行和主机内存，不会把这块显存归还给驱动；显存占用仍稳定在 9.4-10.8 GB。

OOM 兜底（同一提交）：
- 空闲 tick 检查 `MemAvailable < 1.5 GB` 或 GPU free < 1 GB 时，强制清理所有 IDLE slot 的 KV（`reason=oom_guard`）。
- decode 抛分配异常时，清理 IDLE KV 并重试同一 batch 一次（`reason=oom_retry`），仍失败才报错。

### 6.8 长文检索质量

100K 文档（开头埋 `ORANGE-FALCON-42`）：检索版正确命中；`--kvmem-method recency` 或去掉 `apply_retrieval` 都答错，证明 span+检索生效。


## 7. 已知限制

- GPU 只有一个实例：16 GB 放不下 14803 + 14805 同时运行，灰度/对比需要停生产窗口。
- `--cache-reuse` 对 KVMem 自动禁用；多轮收益依赖 prompt 前缀复用。
- 短路阈值以上（>33075）的请求走 KVMem 换页，8 轮 Agent 若单轮上下文超过 33K 会退回非短路路径，每轮多约 1.2 s。
- M-RoPE 图像批次仍会打印 `find_slot: non-consecutive token position ...`（2D 位置本身非连续，属上游行为），不影响正确性。
- 256K 在池 >34816 时会明显变慢；需要长文速度时保持当前配置。
- 进程忽略 SIGTERM，清理用 `kill -9`。
- KV 空闲 TTL 默认 1 天，只清理 IDLE slot；GPU KV 池是固定分配，清理不回退池显存，只释放行与主机内存。
- RAM prompt cache / 原生 checkpoint 在 KVMem 下被禁用（避免 store 与原生 KV 不一致），跨请求只有 slot 前缀复用。


## 8. 故障档案

### 8.1 `decode() failed: completion leaves a hole or exceeds reservation`

- 现象：生产 14803 偶发，文本请求命中前缀缓存后被拒绝（`kv_content.cpp:59`）。
- 根因：M-RoPE 图像 chunk 的 `n_tokens` 与 `n_pos` 不一致（330 vs 22）。早期修复用位置基址填图像行，文本批次又不带行号，导致图像行与文本行重叠/错位；KVMem 完成围栏按行号校验，触发 hole。
- 修复（`96b6d89`）：
  - server 所有 batch 显式携带 `logical_pos`（prompt 行号 + 生成行号连续）；
  - mtmd 新增 `mtmd_helper_decode_image_chunk_rows`，图像行从 `row_0` 起算；
  - 文本行紧接图像行，位置与行号解耦。
- 验证：视觉、图像后文本复用、22K->16.9K 前缀复用、128K、8 轮 Agent 全部无 hole。

### 8.2 KVMem + 多模态直接失败

早期加 `--mmproj` 后图片请求报 `failed to decode mtmd chunk, idx=..., res=1`，日志 `init_batch: failed to prepare KVMem attention ubatches`。
原因：mtmd embedding batch 带 4 维 M-RoPE 位置且首维重复，KVMem 行分配要求一维连续。修复见 8.1（`4232755` + `96b6d89`）。

### 8.3 256K 打分卡死

CPU page softmax 在 25 万 token 上跑 >10 分钟。启用 `KVMEM_PAGE_SOFTMAX_CUDA`（编译 `page_softmax.cu`）后打分 <3 s。

### 8.4 前缀复用失效

KVMem 的 `seq_pos_min` 返回原生行位置，触发 Prism 的 checkpoint 回退分支把 `n_past` 清 0。已对 KVMem 加守卫并补 `begin_cached_turn/truncate_cached`，`cache_n` 恢复正常。

### 8.5 纯文本请求报 Context size exceeded / leaves a hole

- 现象：短文本请求偶发 `Context size exceeded`，随后同一会话连续 `completion leaves a hole`；重启后恢复。
- 根因：server 自带的 RAM prompt cache（`--cache-ram`，默认 8192 MiB）和原生 context checkpoint 会保存/恢复**原生 KV 状态**；KVMem 的 store 不在其中，恢复后 token 数、行号和 store 行数分叉（实测恢复后 prompt 16411 token，store 只有 15879 行），KVMem 准备 batch 失败 → 重试到 1 token → Context exceeded → 完成围栏报 hole。
- 修复（`f1e1646`）：
  - KVMem 启用时强制关闭 RAM prompt cache 与原生 checkpoint（server 内守卫），启动脚本加 `--cache-ram 0 -ctxcp 0`；
  - 前缀截断在位置删除之后追加 `llama_kvmem_remove_logical` 行级清除，避免媒体行残留。
- 复现：8K 文本 + 图片 + 8K 文本，随后多次更短前缀复用，3 轮即触发；修复后 12/12 请求通过。

### 8.6 客户端点"停止"后 GPU 继续计算

- 现象：Harness 点停止后前端流停止，但 14803 仍全速解码 5000 token，GPU 占用不回落。
- 根因：
  - `trim_proxy.mjs` 未监听客户端断连，客户端走了它仍保持上游连接；
  - llama-server 生成循环从不轮询连接状态，HTTP 层虽然提供了 `req.should_stop`（`is_connection_closed`），但没有传入任务。
- 修复（`9cfb691`，代理同步修改）：
  - 代理：`req.aborted` / `res.close`（未正常结束）时 `upstreamReq.destroy()`，日志 `[abort] #N client closed connection`；
  - server：`server_task` 携带 `should_stop`，`pre_decode()` 发现连接已断立即 `slot.release()`，日志 `client disconnected, releasing slot (n_tokens = ...)`。
- 验证：生成 2 秒后断开，服务端在 n_tokens=185/251 处停止（不是 5000）；GPU 利用率 98% -> 0% 在 0.4 秒内；连续 3 次中断后普通请求与 SSE 均 200，无 hole、无 Context exceeded。

### 8.7 其他

- 服务器偶发忽略 SIGTERM，`kill` 无效时用 `kill -9`。
- 用 `pkill -f 'trim_proxy.mjs'` 会匹配到执行命令本身，建议按端口/进程号精确杀。


## 9. 代码地图

| 路径 | 作用 |
|---|---|
| `src/CMakeLists.txt` | 编译 KVMem adapter + attention 源 + CUDA page softmax |
| `tools/server/server-context.cpp` | span 注入、短路、前缀复用、行号、KVMem 钩子 |
| `tools/server/server-task.h` | 请求级 span 字段 |
| `tools/server/CMakeLists.txt` | server 侧 KVMEM 头路径与宏 |
| `src/llama-graph.cpp` | 捕获 K/Q、attention 采样入口 |
| `src/llama-batch.{h,cpp}` | `logical_pos` 批量传递 |
| `src/llama-kv-cache.cpp` | 每 cell 的 logical_pos |
| `tools/mtmd/mtmd-helper{,-common.h}` | 图像 batch 的 `logical_pos` 与 row_0 接口 |
| `common/common.cpp` | `--kvmem` 参数与默认值 |
| `common/arg.cpp`, `common/common.h` | `--kvmem-*` 与 `--kvmem-idle-ttl` 参数注册和默认值 |
| `tools/server/server-queue.{h,cpp}` | 队列空闲 tick（驱动 TTL 扫描） |
| `tools/server/server-task.h` | 任务级 `should_stop`（客户端断连） |
| `src/llama-kvmem-hooks.h`（adapter） | `remove_logical` / `truncate_cached` / `store_*` 等钩子 |

主要提交（由旧到新）：

```
725b9b4 llama : add name_tag to llama_kv_cache ctor
e24af54 llama : add the PLE conv plane to llama_memory_recurrent
3519f65 ggml : give ggml_backend_cuda_gdn_fold model-defined head counts
ae03f78 llama : write logical_pos for every applied KV cell
e0616f6 qwen35 : capture pre-RoPE Q and K for KVMem
a7d2446 common : add --kvmem options and set KVMem params before context init
6adec20 build : compile the KVMem attention sources
533ccdb build : shim the ggml precision setter for the KVMem adapter
513eb02 common : find kvmem headers and match the KVMem defaults
f297fd8 graph : capture attention operands for KVMem sampling
77760d0 server : restore prism oaicompat chat parse
5e7843a server : inject the KVMem question span before decoding a request
937def2 server : inject the KVMem question span, apply retrieval after prefill, keep prefix reuse
5e857a2 build : enable the CUDA page-softmax retrieval scorer
80cbe23 server : bypass KVMem capture and staging while the request fits the GPU pool
0048ed8 server : bound the short-context bypass by the pool high watermark
4232755 mtmd : give image embedding batches monotonic cache rows
50b9f3f server : skip KVMem prefill hooks when the memory is disabled
96b6d89 fix: resolve completion hole error caused by KVMem logical_pos discontinuity
f1e1646 fix: disable native prompt cache and checkpoints under KVMem
9cfb691 server : stop generation when the client disconnects
703d34d docs : add the KVMem x prism production README and repro scripts
ba7fde1 docs : document the prompt cache / checkpoint incompatibility
f33e68a docs : document client disconnect cancellation
5bee96e docs : add final context-size, TTFT and warm-cache benchmarks
bdd446f server : add KVMem idle TTL eviction and memory pressure guard
```


## 10. 基准与复现脚本

都在 `docs/kvmem-prism/scripts/`：

| 脚本 | 用途 |
|---|---|
| `bench_128k.py` | 128K prefill/decode（连 14805，test-key） |
| `bench_256k.py` | 256K prefill/decode |
| `bench_agent_8turn.py` | 8 轮 Agent 轨迹，输出逐轮 TTFT 与总耗时 |
| `bench_quality_100k.py` | 100K 文档开头事实检索（`ORANGE-FALCON-42`） |
| `bench_bypass_cold.py` | 指定 token 数的冷启动短路测试 |
| `test_vision.py` | 图片 OCR/描述，输出 TTFT 与文本 |
| `repro_prefix_reuse.py` | 长请求后短前缀复用（hole 回归） |
| `repro_multimodal_reuse.py` | 图像请求后文本前缀复用（hole 回归） |

用法示例：

```bash
python3 docs/kvmem-prism/scripts/bench_agent_8turn.py /tmp/traj.json --port 14802 --key "$KEY"
python3 docs/kvmem-prism/scripts/test_vision.py --port 14802 --key "$KEY" \
  --image /workspace/phototest/R-C.jpg --prompt "描述这张图片。" --max-tokens 600
```


## 11. 关键日志速查

| 日志 | 含义 |
|---|---|
| `KVMEM_REQUEST_SPAN begin=... end=...` | 长请求注入了检索 span |
| `KVMEM_SHORT_BYPASS tokens=...` | 走短路（原生快速路径） |
| `KVMEM_BYPASS_EXIT full prefill` | 跨阈值，整体重算 |
| `KVMEM_MTMD n_tokens=... n_pos=...` | 图像 chunk 的 token/位置数 |
| `cache_reuse is not supported by this context` | KVMem 禁用了 cache-reuse（预期） |
| `failed to prepare KVMem attention ubatches` | 行号/位置不一致（8.1/8.2 已修） |
| `completion leaves a hole or exceeds reservation` | 完成围栏拒绝（8.1 已修） |
| `KVMEM_IDLE_EVICT reason=idle_ttl ...` | TTL 到期清理空闲 slot 的 KV |
| `KVMEM_IDLE_EVICT reason=oom_guard/oom_retry ...` | 内存不足兜底清理 |
| `client disconnected, releasing slot` | 客户端断连，服务端停止生成 |
| `[abort] #N client closed connection` | 代理检测到客户端断开并销毁上游请求 |


## 12. 后续建议

1. 若需要 32K 以上的短路，可评估把换页选择窗口与池大小解耦（当前高水位绑定池，窗口会随池增大）。
2. 多模态长对话（超过 33K）目前会退回 KVMem 换页，可观察图像后持续的文本轮次质量。
3. 16 GB 单卡无法双实例，灰度验证建议准备第二块 GPU 或安排维护窗口做全量切换。
4. 视觉若遇到 V100 精度问题，启动加 `--no-mmproj-offload`，并对比同一图片的 OCR 结果。


## 13. 验收结论（2026-11-07 收口）

| 目标 | 结果 |
|---|---|
| KVMem + Prism 融合 | 生产 14803 单实例运行，启动 9395 MiB（含视觉） |
| 超长 250K | prefill 546-549 t/s，TTFT 456-458 s，无 OOM / 无 hole |
| 128K | prefill 564-568 t/s，TTFT 231-232 s |
| 常用 16K / 32K / 64K | 795 / 680 / 602 t/s，TTFT 20.7 / 48.3 / 109.0 s |
| 热缓存长上下文 | 128K/192K/250K 第二轮 TTFT 1.79 / 2.56 / 3.19 s |
| 8 轮 Agent | 23.1-24.4 s，总 TTFT 13.6-14.1 s |
| 多模态 | OCR 与图片描述正确，TTFT 0.93-3.65 s |
| 中断取消 | 2 s 断开后 0.4 s 内 GPU 98% -> 0%，slot 立即释放 |
| 稳定性 | 修复后 0 `hole`、0 `Context size exceeded`、0 `prepare_reject` |
| 长期运行 | 单卡 16 GB，显存峰值 10.8 GB，余量 5 GB+ |
| KV 空闲回收 | TTL 到期后 250K 会话 RSS 17.96 GB -> 1.33 GB（释放 16.6 GB）；TTL=0 可禁用 |
