# 官方分词基准（baselines）

当被测网关不转发 `/v1/tokenizers/estimate-token-count` 时，探针会回退到本目录下的
`<model>.json`（或 `default.json`）作为官方计数对照。

生成方式（需要一个官方平台的 Key，只调用分词接口，不消耗推理额度）：

```bash
set MOONSHOT_API_KEY=sk-...
python tools/build_baseline.py --model kimi-k3
python tools/build_baseline.py --model kimi-k2.6
```

文件结构：

```json
{
  "meta": {"model": "kimi-k3", "date": "2026-09-26", "source": "official /v1/tokenizers/estimate-token-count"},
  "template_overhead": 43,
  "samples": {"tk_zh": {"total_tokens": 262, "chars": 212}, "...": {}}
}
```

`samples` 中的样本 ID 与 `server/samples.py` 一一对应；修改样本文本后必须重新生成基准。
