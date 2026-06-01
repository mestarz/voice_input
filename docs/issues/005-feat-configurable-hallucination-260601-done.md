# 005 - feat - 幻觉短语支持 config.toml 自定义

- 状态：done
- 日期：2026-06-01

## 背景

幻觉短语此前硬编码在 `transcriber.py`，遇到新的空音频幻觉需改代码。
用户希望可在 config.toml 中自行追加，无需改代码。

## 实现逻辑

- `config.py`：新增 `hallucination_substrings: list[str]`（默认空），
  并在配置模板中加入示例说明。
- `transcriber.py`：`_looks_like_hallucination(text, extra_substrings)`
  将用户短语归一化后与内置 `_HALLUCINATION_SUBSTRINGS` 合并做子串匹配。
  `transcribe()` 从 `config.hallucination_substrings` 取额外列表传入。

## 说明

- 用户短语与内置默认**合并**生效，不会覆盖内置规则。
- 比较前会归一化（去空格/标点），故配置里写不写空格、标点均可。
- 已重启服务验证：自定义短语（如“关注我的频道”“下期再见”）可被过滤，
  正常语句不受影响。
