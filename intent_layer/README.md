# intent_layer/ - 意图解析层

本目录实现 PathDelta 的意图解析层，负责将自然语言或结构化意图转换为标准化的 IntentCard。

---

## 文件说明

| 文件 | 用途 | 主要函数/类 |
|------|------|-------------|
| `parser.py` | 意图解析器 | `parse_intent_text()`, `IntentParser` |
| `schema.py` | IntentCard 数据模型 | `IntentCard` |
| `runner.py` | 意图层运行入口 | `run_intent_layer()` |
| `generator.py` | 合成意图生成 | `generate_synthetic_intents()` |
| `llm_client.py` | LLM 客户端封装 | `IntentLLMClient` |
| `registry.py` | 意图类型注册表 | `IntentRegistry` |
| `validators.py` | 意图验证器 | `validate_intent()` |

### 子目录

| 目录 | 用途 |
|------|------|
| `handlers/` | 各意图类型的处理器 |
| `examples/` | 示例意图文件 |

---

## 核心组件

### IntentCard (schema.py)

标准化的意图数据结构：

```python
from intent_layer.schema import IntentCard

intent = IntentCard(
    intent_id="intent_001",
    type="prefer_with_backup",
    scope="prefix",
    prefix="10.0.0.0/24",
    primary_exit="new_york",
    backup_exit="chicago",
    ordered_exits=["new_york", "chicago"],
    constraints={}
)
```

### 意图解析 (parser.py)

将自然语言转换为 IntentCard：

```python
from intent_layer.parser import parse_intent_text

text = "Prefer new_york as primary exit, chicago as backup for 10.0.0.0/24"
intent_card = parse_intent_text(text, topology)
```

### 运行入口 (runner.py)

完整的意图层处理流程：

```python
from intent_layer.runner import run_intent_layer

result = run_intent_layer(
    intent_text="...",
    topology=topology_dict,
    use_llm=True
)
```

---

## 支持的意图类型

| 类型 | 描述 | 必需字段 |
|------|------|----------|
| `prefer_with_backup` | 主备出口 | `prefix`, `primary_exit`, `backup_exit` |
| `ecmp` | 等价多路径 | `prefix`, `exits` |
| `ordered_preference` | 有序偏好 | `prefix`, `ordered_exits` |
| `pin_to_exit` | 固定出口 | `prefix`, `pinned_exit` |
| `avoid_exit` | 避免出口 | `prefix`, `avoid_exits` |
| `avoid_exit` | 避免出口 | `prefix`, `avoid_exits` |
| `path_migration` | 路径迁移 | `prefix`, `from_exit`, `to_exit` |
| `ospf_steering` | OSPF 路径引导 | `prefix`, `primary_exit`, `backup_exit` (或 `ordered_exits`) |

---

## 使用示例

### 从 JSON 文件加载意图

```python
import json
from intent_layer.schema import IntentCard

with open("intent.json") as f:
    data = json.load(f)
intent = IntentCard(**data)
```

### 生成合成意图

```python
from intent_layer.generator import generate_synthetic_intents

intents = generate_synthetic_intents(
    topology=topology_dict,
    count=70,
    seed=42
)
```

---

## 目录结构

```
intent_layer/
├── __init__.py
├── parser.py           # 意图解析器
├── schema.py           # IntentCard 定义
├── runner.py           # 运行入口
├── generator.py        # 合成意图生成
├── llm_client.py       # LLM 客户端
├── registry.py         # 意图类型注册
├── validators.py       # 意图验证
├── handlers/           # 意图处理器
│   ├── __init__.py
│   ├── prefer_handler.py
│   └── ...
└── examples/           # 示例文件
    └── sample_intent.json
```


---

## 意图类型详解

### 1. 主备出口（prefer_with_backup）
**用途**：优先使用主出口，主出口故障时自动切换到备用出口

**示例**：
```json
{
  "intent_id": "intent_001",
  "type": "prefer_with_backup",
  "prefix": "10.100.0.0/24",
  "primary_exit": "brisbane",
  "backup_exit": "alice_springs"
}
```

**效果**：Brisbane 优先级高，Alice Springs 作为备份

### 2. 等价多路径（ecmp）
**用途**：流量在多个出口之间均匀分配，实现负载均衡

**示例**：
```json
{
  "intent_id": "intent_002",
  "type": "ecmp",
  "prefix": "10.100.0.0/24",
  "exits": ["brisbane", "sydney", "melbourne"]
}
```

**效果**：流量平均分配到 3 个出口

### 3. 有序偏好（ordered_preference）
**用途**：定义多个出口的优先级顺序，按顺序依次尝试

**示例**：
```json
{
  "intent_id": "intent_003",
  "type": "ordered_preference",
  "prefix": "10.100.0.0/24",
  "ordered_exits": ["brisbane", "sydney", "melbourne"]
}
```

**效果**：Brisbane > Sydney > Melbourne（优先级递减）

### 4. 固定出口（pin_to_exit）
**用途**：强制流量只能从指定出口离开

**示例**：
```json
{
  "intent_id": "intent_004",
  "type": "pin_to_exit",
  "prefix": "10.100.0.0/24",
  "pinned_exit": "brisbane"
}
```

**效果**：只能走 Brisbane，即使故障也不切换

### 5. 避免出口（avoid_exit）
**用途**：禁止流量从某些出口离开

**示例**：
```json
{
  "intent_id": "intent_005",
  "type": "avoid_exit",
  "prefix": "10.100.0.0/24",
  "avoid_exits": ["alice_springs"]
}
```

**效果**：不走 Alice Springs，可以走其他任何出口

### 6. 路径迁移（path_migration）
**用途**：将流量从旧出口平滑迁移到新出口

**示例**：
```json
{
  "intent_id": "intent_006",
  "type": "path_migration",
  "prefix": "10.100.0.0/24",
  "from_exit": "alice_springs",
  "to_exit": "brisbane"
}
```

**效果**：流量从 Alice Springs 迁移到 Brisbane

### 7. OSPF 路径引导（ospf_steering）
**用途**：在纯 OSPF 环境中通过调整 Cost 引导流量路径（主要用于内部路径控制）

**示例**：
```json
{
  "intent_id": "intent_007",
  "type": "ospf_steering",
  "prefix": "10.100.0.0/24",
  "primary_exit": "brisbane",
  "backup_exit": "alice_springs",
  "ordered_exits": ["brisbane", "alice_springs"]
}
```

**效果**：降低 Brisbane 的 Cost，提高 Alice Springs 的 Cost，使流量优先走 Brisbane。

---

## 合成意图生成

PathDelta 支持自动生成合成意图用于测试：

```python
from intent_layer.generator import generate_synthetic_intents

# 为拓扑生成 70 个意图
intents = generate_synthetic_intents(
    topology=topology_dict,
    count=70,
    seed=42,
    intent_types=["prefer_with_backup", "ecmp", "ordered_preference"]
)

# 保存到文件
for i, intent in enumerate(intents):
    with open(f"intent_{i:02d}.json", "w") as f:
        json.dump(intent, f, indent=2)
```

**生成策略**：
- 随机选择前缀（从拓扑中的网络）
- 随机选择出口（从拓扑中的边界路由器）
- 确保意图的合理性（如主备出口不能相同）
- 均匀分布各种意图类型
