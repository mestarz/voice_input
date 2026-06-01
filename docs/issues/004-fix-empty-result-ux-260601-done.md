# 004 - fix - 区分识别失败原因 + 减少误伤与真失败

- 状态：done
- 日期：2026-06-01

## 背景

用户反馈“经常识别失败 / 未识别到任何东西”，且无法判断到底是幻觉被过滤还是真没识别到。

根因分析：

1. **无法区分**：`daemon._do_transcribe_and_paste` 中，真·空音频与幻觉被过滤
   都得到 `text == ""`，弹同一条“没有识别到文本”，用户分不清。
2. **过滤误伤**：003 引入的子串 `请订阅`、`字幕由` 过于宽泛，会把
   “请订阅我的频道”等真实语音静默吞掉。
3. **真失败来源**：`transcribe()` 用 `vad_filter=True` 的默认参数，对短句/轻声
   容易整段切掉 → 空结果。这是日常“识别失败”的高频来源，与幻觉无关。

注：Whisper（beam search）对同一音频是确定性输出，“幻觉后重识别”无意义，故不采用。

## 实现逻辑

- `transcriber.py`
  - 新增 `TranscribeResult(text, raw, status)`；`status ∈ {ok, empty, hallucination}`。
  - `transcribe()` 改为返回 `TranscribeResult`，区分空结果与幻觉。
  - 收窄 `_HALLUCINATION_SUBSTRINGS`，仅保留强特征短语，去掉易误伤的通用词。
  - VAD 参数改为从配置读取（`vad_parameters`）。
- `config.py`
  - 新增 `vad_filter`、`vad_min_silence_ms`(300)、`vad_speech_pad_ms`(200)，
    较默认更宽松，减少短句被误切；同步更新配置模板。
- `daemon.py`
  - 幻觉 → 通知“疑似空音频幻觉，已忽略”，并写入 `last_error` 便于排查。
  - 真空/empty → 通知“没听到声音，请靠近麦克风或说久一点再试”。

## 说明

- 修改后已重启 `voice-paste.service`，模型预加载成功（cuda/float16）。
- 后续如仍偶发误切，可在 config.toml 调大 `vad_min_silence_ms`/`vad_speech_pad_ms`
  或设 `vad_filter = false`。
