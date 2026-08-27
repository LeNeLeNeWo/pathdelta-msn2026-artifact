# common/ - 通用工具模块

本目录包含 PathDelta 的通用工具和基础设施代码。

---

## 文件说明

| 文件 | 用途 | 主要函数/类 |
|------|------|-------------|
| `llm_driver.py` | LLM API 封装 | `call_llm()`, `LLMProvider` |
| `intents.py` | 意图类型定义 | `INTENT_TYPES`, `IntentType` |

---

## llm_driver.py

LLM API 的统一封装，支持多个 LLM 提供商。

### 支持的提供商

| 提供商 | 环境变量 | 默认模型 |
|--------|----------|----------|
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-chat` |
| Qwen (千问) | `DASHSCOPE_API_KEY` | `qwen-plus` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4` |

### 使用示例

```python
from common.llm_driver import call_llm

# 基本调用
response = call_llm(
    prompt="Generate FRR config for BGP local-pref",
    system_prompt="You are a network configuration expert.",
    temperature=0.1
)

# 指定提供商
response = call_llm(
    prompt="...",
    provider="deepseek"  # 或 "qwen", "openai"
)
```

### 配置 API Key

在文件中直接配置（不推荐）：
```python
DEEPSEEK_API_KEY = "your-api-key"
```

或通过环境变量：
```bash
export DEEPSEEK_API_KEY="your-api-key"
```

---

## intents.py

定义 PathDelta 支持的意图类型。

### 意图类型

```python
INTENT_TYPES = [
    "prefer_with_backup",   # 主备出口偏好
    "ecmp",                 # 等价多路径
    "ordered_preference",   # 有序出口偏好
    "pin_to_exit",          # 固定到特定出口
    "avoid_exit",           # 避免特定出口
    "path_migration",       # 前缀迁移
]
```

### 使用示例

```python
from common.intents import INTENT_TYPES, is_valid_intent_type

if is_valid_intent_type("prefer_with_backup"):
    print("Valid intent type")
```
