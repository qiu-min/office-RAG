# 配置学习笔记

## DeepSeek 文本 LLM 凭据

### 源码事实

- `config/settings.yaml` 当前将 `llm.provider` 配置为 `deepseek`，模型为 `deepseek-v4-flash`。
- `src/libs/llm/deepseek_llm.py::DeepSeekLLM.__init__()` 当前按以下方式读取凭据：
  - `api_key` 构造参数
  - 环境变量 `DEEPSEEK_API_KEY`
  - 两者都没有时抛出异常
- 当前 `DeepSeekLLM` 没有读取 `settings.llm.api_key`，也没有读取 `settings.llm.base_url`。
- `DeepSeekLLM.DEFAULT_BASE_URL` 是 `https://api.deepseek.com`；当前 `base_url` 只有通过构造参数才能覆盖。
- `src/core/settings.py` 的 `LLMSettings` 虽然声明了 `api_key` 和 `base_url` 字段，但字段存在不等于每个 Provider 都实际消费它们。

### 当前使用方式

在启动项目的同一个 PowerShell 会话中设置：

```powershell
$env:DEEPSEEK_API_KEY = "sk-你的密钥"
```

然后再启动服务或执行摄取/查询命令。

该 `$env:` 写法只对当前终端进程及其子进程有效。Windows 用户级永久配置可以使用
`[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "...", "User")`，设置后重新打开终端或 IDE。

### 待改进

如果希望完全通过 `settings.yaml` 配置 DeepSeek，应修改 `DeepSeekLLM`，使其读取 `settings.llm.api_key` 和 `settings.llm.base_url`，并补充对应单元测试。

## 切换 LLM 的判断

- 同一个 Provider 只换模型，通常只需要修改 `llm.model`。
- 切换到本地 Ollama 文本模型，需要把 `llm.provider` 改为 `ollama`，并修改模型名；当前 Ollama 默认连接 `http://localhost:11434`，不需要 API Key。
- Qwen 云端接口若使用 OpenAI-compatible 协议，可以复用 `OpenAILLM` 的请求格式，但当前 `OpenAILLM` 还没有读取 `settings.llm.base_url`，因此不能只改 YAML 就切换到自定义兼容端点，需要先补这个配置读取逻辑和测试。
- 如果 Provider 不兼容 OpenAI/Ollama 协议，则需要新增 Provider、工厂注册、配置和测试。

## 当前配置自检结果

- YAML 可以正常加载。
- `LLMFactory.create(settings)` 可以创建 `DeepSeekLLM`。
- `LLMFactory.create_vision_llm(settings)` 可以创建 `OllamaVisionLLM`。
- 实际运行仍依赖外部条件：`DEEPSEEK_API_KEY`、Ollama 服务，以及配置中的 `nomic-embed-text` 和 `qwen2.5vl:3b` 模型。

### 无 API Key 时为什么仍能摄取

- `ChunkRefiner` 和 `MetadataEnricher` 对文本 LLM 使用懒加载。
- DeepSeek 初始化失败时，它们会捕获异常并切换到规则处理，摄取流程继续执行。
- 因此“摄取成功”不等于 DeepSeek 已被调用；应查看 trace 中的 `use_llm`、`llm_enhanced_count` 和 `enriched_by_llm`。
