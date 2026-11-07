> HISTORICAL SNAPSHOT (2026-11-06). Merge counts and numbers may be stale.
> Current behavior, flags and limitations: ../../README.md.

# KVMem -> Prism 合并报告（2026-11-06）

## 结论（先说结果）

**合并未完成，未产出可运行的融合二进制；128K/256K 压测因此未执行。**
生产 14802/14803 全程未被触碰（proxy=200，llama 仍在 127.0.0.1:14803）。

root cause 不是"补丁太大"，而是**两套同源代码在同一个子系统上正面冲突**：

| 子系统 | KVMem 侧 | Prism 侧 | 冲突性质 |
|---|---|---|---|
| GDN 状态回滚 | `llama_memory_recurrent::replay_kind` + replay ring | ARC-LAB `GGML_OP_GATED_DELTA_NET_REPLAY` + `LLAMA_GDN_REPLAY` | 语义重复，不是简单叠加 |
| 投机解码 MTP | 给 MTP 注入 `logical_pos` / `synced_rows` / `verify_start` 状态 | Prism 自己的 EAGLE3/MTP 状态机（同类变量在不同类里） | 补丁 hunk 落到了错误的类 |
| KV 驱逐 | host-swap + `--kvmem-budget` | `--cache-reuse` 的 KV-shift | 可共存，已合上 |

## 已完成的工作

1. **G0**：从 `kvmem-root/patches/llama-kvmem-current.patch` 提取补丁（1,918 行 / 41 文件），
   在 `$REPO` 建独立副本（prism 源码 + 新 git 仓）。
2. **自动应用**：`git apply --reject` → **56 个文件干净应用**，17 个文件产生 35 个 reject。
3. **手工合并（已完成）**：
   - 新增 ggml op `GGML_OP_GATED_DELTA_NET_RECORD`：enum、name/symbol 表、`GGML_OP_COUNT` 102→103、CUDA supports_op case；
   - `llama_kv_cell_ext::logical_pos` 字段 + `apply_ubatch` 写入（含 2D 分支）；
   - `llama_kv_cache` 头文件补 `get_v_storage` / `seq_rm_logical` 声明；
   - `llama-model.cpp` 引入 `llama-kvmem-factory.h`；
   - `llama_memory_recurrent` 头补 `replay_kind` enum + replay 成员 + ctor 的 `replay` 形参；
   - `gated_delta_net.cu` 的 `K > 0` 保护 + `ggml-cuda.h` include；
   - `ops.cpp` 的 `K >= 0` / `K != 1` 守卫。
4. **编译进度**：CMake 配置通过（CUDA 12.8，sm_70），`llama-server` 目标编译到
   **仅剩 4 个错误**：`common/speculative.cpp` 1305–1308 与 2780 的 `logical_pos` / `synced_rows` /
   `verify_start` / `sync_batch` 未声明——对应补丁 6 个 hunk 落到了错误的 MTP 实现类。

## 未完成部分（可续作）

- `common/speculative.cpp`：6 hunk，需要把 KVMem 的状态字段落到 Prism 的 MTP 实现类上（不是机械插入）；
- `src/models/delta-net-base.cpp` / `qwen35.cpp`：各 2 hunk（record op 的调用点）；
- `tools/server/server-common.cpp`：2 hunk（`--kvmem-*` 参数注册）；
- `src/llama-memory-recurrent.cpp`：6 hunk 的 replay 主体（头文件已补，实现体需对齐 Prism 的 ARC-LAB replay）。

预计还需 2–4 小时（含 2–3 轮 20 分钟级编译）。

## 可立即使用的替代（若现在就要 128K）

`kvmem-root` 的 `llama-kvmem-server` 是**已验证可用**的 128K 路径（历史实测 117K prefill 551.7 tok/s）。
可以把它起在 14805 供人工测试：

```bash
cd /workspace/baseline_llama
CTX=131072 B=16384 R=2048 HOST_MIB=8192 CPU_GB=8 PORT=14805 NOKV=0 \
  ./scripts/start_kvmem.sh
```

注意：它没有 `--cache-reuse` / `--slot-save-path` / `--spec-type draft-dflash`，
所以是"128K 可用、Agent 优化与 DFlash2 不可用"的取舍。

## 红线核对

- 生产 14802/14803：未改配置、未重启、未杀；代理验证 200。
- 未触碰 `ninfer_attack/`、`strata_qwen38/`；未改 CPU/电源/MSR。
- 合并工作全部在 `$REPO/`。
