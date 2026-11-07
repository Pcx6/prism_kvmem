# KVMem x Prism 文档

完整说明见仓库根目录 [README.md](../../README.md)。

本目录只放可复现脚本：

- bench_128k.py / bench_256k.py：长文 prefill/decode（`--port/--key`，默认 14802）
- bench_agent_8turn.py：8 轮 Agent 轨迹与逐轮 TTFT
- bench_quality_100k.py：100K 文档检索质量（ORANGE-FALCON-42）
- bench_bypass_cold.py：指定 token 数的冷启动短路测试
- test_vision.py：多模态 OCR / 描述
- repro_prefix_reuse.py / repro_multimodal_reuse.py：前缀复用与多模态 hole 回归用例

运行前确保目标端口有服务，并准备长文本文件（`PROMPT_FILE`）与图片（`IMAGE_FILE`）。
