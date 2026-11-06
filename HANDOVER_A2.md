# HANDOVER: KVMem -> Prism 融合（A2 续作）

更新时间 2026-11-06。上一轮已交付 128K/256K 压测（用 kvmem 独立二进制），融合版仍差 3 处结构移植。

## 0. 环境事实

| 项 | 值 |
|---|---|
| GPU | Tesla V100-SXM2-16GB (sm_70)，驱动 580.65.06 |
| CUDA | **/usr/local/cuda-12.8**（默认 /usr/local/cuda 是 13.0，不支持 sm_70，必须显式指定） |
| 模型 | `/workspace/baseline_llama/models/Ternary-Bonsai-2-27B-PTQ1_0.gguf`（5.6 GB） |
| 融合工作区 | `/workspace/prism_kvmem`（分支 `feat/kvmem-prism-merge`，HEAD 7eb31bc） |
| 参考实现（KVMem） | `/workspace/baseline_llama/src/kvmem-root/backends/llamacpp`（llama.cpp 树） |
| adapter 源码 | `/workspace/baseline_llama/src/kvmem-root/src/adapter/*.cpp` |
| 生产 | 14802 代理 -> 127.0.0.1:14803 prism llama-server（**不要动**） |
| 测试端口 | 14805（与 14803 不能并存：16 GB 装不下两个实例） |

## 1. 当前编译状态

```bash
cd /workspace/prism_kvmem
CUDACXX=/usr/local/cuda-12.8/bin/nvcc cmake -B build-sm70 \
  -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=70 \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-12.8/bin/nvcc \
  -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_KVMEM=ON -DLLAMA_KVMEM_ROOT=/workspace/baseline_llama/src/kvmem-root
cmake --build build-sm70 -j 12 --target llama-server
```

- `LLAMA_KVMEM=OFF`：**通过**（有二进制，但无 host-swap）。
- `LLAMA_KVMEM=ON`：失败，错误如下（三组）：

```
llama-memory-kvmem.cpp: no matching function for call to llama_kv_cache::llama_kv_cache(..., const char*)
llama-memory-kvmem.cpp:1357: llama_memory_recurrent has no member named 'p_l'
llama-memory-kvmem.cpp:328: too many arguments to ggml_backend_cuda_gdn_fold(...)
```

## 2. 三处移植清单（按此顺序做）

### 2.1 `llama_kv_cache` 构造：补 `name_tag`

- 参考：`kvmem-root/backends/llamacpp/src/llama-kv-cache.h:99-117`
- 目标：`prism_kvmem/src/llama-kv-cache.h:99-115`
- 差异：kvmem 末尾多一个 **`const char * name_tag = ""`**（默认参数，用于多 cache 的 tensor 命名）。
- 动作：目标头文件加该参数；`.cpp` 定义同步；把 tensor 命名处用 `name_tag` 拼接（参考 kvmem 的 `.cpp` 搜 `name_tag`）。

### 2.2 `llama_memory_recurrent::p_l` 平面

- 参考：`kvmem-root/backends/llamacpp/src/llama-memory-recurrent.h:130`（成员）与 `:199`（`get_p_l`），
  实现见 `llama-memory-recurrent.cpp`：`83`、`133`、`808`、`985-991`、`1198-1201`。
- 目标：`prism_kvmem/src/llama-memory-recurrent.{h,cpp}`，对照它已有的 `r_l`/`s_l`（`112`、`144-146`、`800`）成对添加。
- `p_l` 是 PLE conv-state 平面；创建、io 序列化、rollback 三处都要加，否则 adapter 读空指针。
- 同时补 `ggml_tensor * get_p_l(int32_t il) const;` 与实现。

### 2.3 `ggml_backend_cuda_gdn_fold` 签名

- 参考：`kvmem-root/backends/llamacpp/ggml/src/ggml-cuda/gated_delta_net.cu:442`
  ```cpp
  bool ggml_backend_cuda_gdn_fold(const ggml_cuda_gdn_replay_layer * layers,
      int n_layers, int n_keep, int capacity, int key_heads, int value_heads, void * stream_ptr)
  ```
- 目标：`prism_kvmem/ggml/src/ggml-cuda/gated_delta_net.cu:638`（缺 `key_heads, value_heads`，且函数体更短）。
- 动作：把参考实现的参数校验 + `channels` 计算 + kernel 启动方式整体移植；头文件声明同步。

## 3. 验证

1. `KVMEM=ON` 编译通过；`build-sm70/bin/llama-server --help | grep kvmem` 至少出现 `--kvmem-budget`。
2. 与生产错峰：先 `for p in $(pgrep -x llama-server); do kill $p; done`，再起 14805：
   ```bash
   build-sm70/bin/llama-server -m /workspace/baseline_llama/models/Ternary-Bonsai-2-27B-PTQ1_0.gguf \
     -c 262144 --host 127.0.0.1 --port 14805 --api-key test-key-14805 \
     --kvmem --kvmem-budget 12288 --kvmem-gen-reserve 2048 \
     -ngl 99 -fa on -ub 1024 -b 4096
   ```
3. 128K/256K 请求（脚本现成：`/srv/llama_agent/bench_128k.py`、`bench_256k.py`，改端口即可）。
   **对比基线（kvmem 独立二进制实测）**：128K prefill 546.6 / decode 31.8 tok/s；256K 538.3 / 28.6 tok/s；显存 8.3 GB。
4. 融合版应同时具备 `--cache-reuse`（prism）与 `--kvmem-budget`（KVMem）。

## 4. 红线

- 不重启/不 kill 生产 14803；测试用 14805，且必须与 14803 错峰（16 GB）。
- 不改 CPU 频率/电源/MSR；测试一律 `ulimit -c 0`。
- 不碰 `/workspace/ninfer_attack/`、`/srv/workspace/strata_qwen38/`。
- 若 20 小时内仍无法让 `KVMEM=ON` 通过或短上下文（4K）崩溃/死锁，放弃融合，保留日志：
  "KVMem 与 prism 的 KV-shift/replay 在数据结构层冲突，无法硬缝"。

## 5. 已知语义风险

- prism 自带 ARC-LAB `GGML_OP_GATED_DELTA_NET_REPLAY` + `LLAMA_GDN_REPLAY`；KVMem 也有自己的 replay ring。
  两者都在改 recurrent state 的 rollback 路径，**必须二选一**：建议保留 prism 的 replay，把 KVMem 的 replay 调用点改成走 prism 的机制；
  否则 `p_l` 的初始化和 fold 会与 replay ring 打架。
- prism 的 `--cache-reuse` 会做 KV-shift；KVMem 的 host-swap 会驱逐页。若同开导致位置错乱，
  以 KVMem 为准并把 `cache-reuse` 默认关（保留开关）。

## 6. 已完成的部分（不要重做）

- ggml：新增 `GGML_OP_GATED_DELTA_NET_RECORD`（enum、name/symbol、`GGML_OP_COUNT`=103、CUDA supports_op、CPU plan 守卫）。
- `llama_kv_cell_ext::logical_pos` 字段 + `apply_ubatch` 写入。
- `llama_kv_cache` 的 `get_v_storage`/`seq_rm_logical` 声明。
- `llama-model.cpp` 引入 `llama-kvmem-factory.h`。
- `llama_memory_recurrent` 的 `replay_kind` enum + replay 成员 + ctor 的 `replay` 形参。
- `common/speculative.cpp`：`logical_pos/synced_rows/verify_start` 迁到 MTP 类，`sync_batch` 插入。
- `tools/server/server-common.cpp`：KVMem 版重复 `oaicompat_chat_params_parse` 已 `#if 0` 隔离。
- `src/llama-context.cpp`：补 `llama-kvmem-hooks.h` include（LLAMA_KVMEM 守卫）。
