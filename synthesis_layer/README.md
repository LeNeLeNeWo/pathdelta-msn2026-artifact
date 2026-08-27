# synthesis_layer/ - 配置合成层

本目录实现 PathDelta 的配置合成层，负责将 PolicyEntry 转换为符合风格的 FRR 配置补丁，并通过 ConstraintGuard 防护机制确保配置正确性。

---

## 文件说明

| 文件 | 用途 | 主要函数/类 |
|------|------|-------------|
| `renderer.py` | **LLM 配置渲染器** | `ConfigRenderer`, `render_config()` |
| `guard.py` | **ConstraintGuard 防护** | `ConstraintGuard`, `SecurityViolationError` |
| `planner.py` | **PatchPlanner 补丁规划** | `PatchPlanner`, `PatchPlan` |
| `context_analyzer.py` | **上下文分析器（StyleGrid）** | `ContextAnalyzer`, `analyze_affected_devices()` |
| `models.py` | 合成层数据模型 | `PatchPlan`, `RenderResult`, `ReuseContext` |
| `template_library.py` | 模板库 | `TemplateLibrary` |
| `template_retriever.py` | **模板检索（Dual-RAG）** | `TemplateRetriever` |
| `template_schema.py` | 模板 Schema | `TemplateSchema` |
| `template_reporter.py` | 模板报告 | `TemplateReporter` |
| `reporter.py` | 合成报告 | `SynthesisReporter` |

---

## 核心组件

### ConfigRenderer (renderer.py) - **LLM 驱动的配置生成**

LLM 驱动的配置渲染器，支持 Dual-RAG 和风格一致性：

```python
from synthesis_layer.renderer import ConfigRenderer
from synthesis_layer.context_analyzer import ContextAnalyzer

analyzer = ContextAnalyzer()
renderer = ConfigRenderer(
    context_analyzer=analyzer,
    use_neural=True,  # 使用 LLM
    # LLM Provider 由环境配置决定 (DeepSeek or Qwen)
)

# 渲染补丁计划
patches = renderer.render_plan(patch_plan)
# 返回: {"new_york": "route-map RM_...\n...", "chicago": "..."}
```

**关键特性**：
1. **Dual-RAG 检索**：
   - Style-RAG：检索相似的配置片段作为风格参考
   - Semantic-RAG：检索语义相关的配置示例
   
2. **LLM 提示工程**：
   - 结构化提示模板
   - 参数约束注入
   - Few-shot 示例

3. **风格一致性**：
   - 命名规范（prefix-list, route-map）
   - 序列号分配
   - 参数值对齐

### ConstraintGuard (guard.py) - **Fail-Closed 防护机制**

配置安全防护，检测 LLM drift 攻击和参数偏离：

```python
from synthesis_layer.guard import ConstraintGuard, SecurityViolationError

guard = ConstraintGuard()

try:
    guard.verify(
        patch_text="route-map RM_TEST permit 10\n set local-preference 200",
        params={"local_pref": 200},
        mechanism="local_pref",
        intent_id="test_001",
        device="new_york"
    )
    print("✓ Guard passed")
except SecurityViolationError as e:
    print(f"✗ Security violation: {e}")
    # 配置被拒绝，不会应用
```

**检测规则**：

| 规则类别 | 检测内容 | 示例 |
|----------|----------|------|
| **Hard Rules** | 禁止的危险命令 | `no router bgp`, `shutdown` |
| **Drift Detection** | 参数值偏离 | local-pref 应为 200，实际为 150 |
| **Scope Violation** | 超出意图范围的修改 | 修改了不相关的 prefix-list |
| **Syntax Check** | FRR 语法错误 | 缺少 `!` 分隔符 |
| **Mechanism Validation** | 机制特定检查 | BGP 必须有 `set local-preference` |

**Drift 攻击防护**：
- LLM 可能生成错误的参数值（如 200 变成 150）
- ConstraintGuard 检测参数偏离并拒绝配置
- 确保 Fail-Closed：有疑问时拒绝，而不是应用错误配置

### ContextAnalyzer (context_analyzer.py) - **StyleGrid 风格分析**

分析现有配置，提取风格模式（StyleGrid）：

```python
from synthesis_layer.context_analyzer import ContextAnalyzer

analyzer = ContextAnalyzer()

# 分析单个配置
context = analyzer.slice_config(frr_config_text)
# 返回: {
#   "prefix_lists": [...],
#   "route_maps": [...],
#   "naming_style": {"prefix_list_prefix": "PL-", "route_map_prefix": "RM-"},
#   "seq_pattern": {"prefix_list_step": 5, "route_map_step": 10},
#   "lp_step": 50,
#   "cost_step": 10
# }

# 分析受影响设备
from synthesis_layer.context_analyzer import analyze_affected_devices

reuse_contexts = analyze_affected_devices(
    configs=baseline_configs,
    affected_devices=["new_york", "chicago"],
    neighbors_by_device={"new_york": ["chicago"]},
    prefixes=["10.0.0.0/24"]
)
```

**StyleGrid 提取的风格元素**：

1. **命名规范**：
   - Prefix-list 前缀（如 `PL-`, `PREFIX_`）
   - Route-map 前缀（如 `RM-`, `RMAP_`）
   - 分隔符风格（连字符 vs 下划线）

2. **序列号模式**：
   - Prefix-list 序列号步长（通常 5）
   - Route-map 序列号步长（通常 10）
   - 起始序列号

3. **参数步长**：
   - BGP local-preference 步长（如 50, 100）
   - OSPF cost 步长（如 10, 5）

4. **复用策略**：
   - 现有对象是否可复用
   - 需要创建的新对象
   - 需要修改的对象

### PatchPlanner (planner.py) - **最小化补丁规划**

补丁规划器，生成最小化的配置修改：

```python
from synthesis_layer.planner import PatchPlanner

planner = PatchPlanner(context_analyzer=analyzer)

patch_plans = planner.plan(
    role_policy=role_policy,
    reuse_contexts=reuse_contexts,
    baseline_configs=baseline_configs
)

# 返回: [PatchPlan(...), PatchPlan(...)]
# 每个 PatchPlan 包含：
# - device: 设备名
# - strategy: CREATE, MODIFY, REBIND
# - objects: 需要操作的对象列表
# - reuse_context: 复用上下文
```

**规划策略**：

| 策略 | 描述 | 示例 |
|------|------|------|
| `CREATE` | 创建新对象 | 创建新的 prefix-list 和 route-map |
| `MODIFY` | 修改现有对象 | 修改现有 route-map 的 local-pref 值 |
| `REBIND` | 重新绑定 | 将 route-map 绑定到不同的 BGP 邻居 |

**最小化原则**：
- 优先复用现有对象
- 只修改必要的部分
- 避免不必要的配置变更

---

## Dual-RAG 模板系统

### TemplateRetriever (template_retriever.py) - **双重检索**

Dual-RAG 模板检索器，结合风格和语义检索：

```python
from synthesis_layer.template_retriever import TemplateRetriever

retriever = TemplateRetriever()

# Style-RAG: 检索相似的配置片段
style_templates = retriever.retrieve_by_style(
    mechanism="local_pref",
    naming_pattern="PL-*",
    top_k=3
)

# Semantic-RAG: 检索语义相关的示例
semantic_templates = retriever.retrieve_by_semantic(
    intent_type="prefer_with_backup",
    mechanism="local_pref",
    top_k=3
)

# 组合检索
templates = retriever.retrieve(
    mechanism="local_pref",
    intent_type="prefer_with_backup",
    style_weight=0.6,  # Style-RAG 权重
    semantic_weight=0.4,  # Semantic-RAG 权重
    top_k=5
)
```

**Dual-RAG 的优势**：
1. **Style-RAG**：确保生成的配置符合现有风格
2. **Semantic-RAG**：确保生成的配置语义正确
3. **组合检索**：平衡风格一致性和语义正确性

### TemplateLibrary (template_library.py)

模板库，存储和管理配置模板：

```python
from synthesis_layer.template_library import TemplateLibrary

library = TemplateLibrary()

# 获取模板
template = library.get_template("bgp_local_pref")

# 添加自定义模板
library.add_template(
    name="custom_template",
    mechanism="local_pref",
    content="route-map RM_CUSTOM permit 10\n..."
)
```

---

## 目录结构

```
synthesis_layer/
├── __init__.py
├── renderer.py             # LLM 配置渲染
├── guard.py                # ConstraintGuard 防护
├── planner.py              # PatchPlanner 规划
├── context_analyzer.py     # 上下文分析
├── models.py               # 数据模型
├── template_library.py     # 模板库
├── template_retriever.py   # 模板检索 (Dual-RAG)
├── template_schema.py      # 模板 Schema
├── template_reporter.py    # 模板报告
└── reporter.py             # 合成报告
```


---

## 使用示例

### 完整合成流程

```python
from synthesis_layer.context_analyzer import ContextAnalyzer, analyze_affected_devices
from synthesis_layer.planner import PatchPlanner
from synthesis_layer.renderer import ConfigRenderer
from synthesis_layer.guard import ConstraintGuard

# 1. 分析上下文（StyleGrid）
analyzer = ContextAnalyzer()
reuse_contexts = analyze_affected_devices(
    configs=baseline_configs,
    affected_devices=policy_result["affected_devices"],
    neighbors_by_device=topology["neighbors"],
    prefixes=[policy_result["prefix"]]
)

# 2. 规划补丁（最小化修改）
planner = PatchPlanner(context_analyzer=analyzer)
patch_plans = planner.plan(
    role_policy=policy_result,
    reuse_contexts=reuse_contexts,
    baseline_configs=baseline_configs
)

# 3. 渲染配置（LLM + Dual-RAG）
renderer = ConfigRenderer(
    context_analyzer=analyzer,
    use_neural=True,
    llm_provider="deepseek"
)

all_patches = {}
for plan in patch_plans:
    patches = renderer.render_plan(plan)
    all_patches.update(patches)

# 4. ConstraintGuard 验证（Fail-Closed）
guard = ConstraintGuard()

for device, patch in all_patches.items():
    try:
        guard.verify(
            patch_text=patch,
            params=policy_result["params"],
            mechanism=policy_result["mechanism"],
            intent_id=policy_result["intent_id"],
            device=device
        )
        print(f"✓ {device}: Guard passed")
    except SecurityViolationError as e:
        print(f"✗ {device}: Security violation - {e}")
        del all_patches[device]
```

---

## 关键设计原则

### 1. Patch-First 流水线
先生成配置补丁，再验证，支持 LLM 生成的灵活性

### 2. Fail-Closed 防护
ConstraintGuard 确保有疑问时拒绝配置，宁可拒绝也不应用错误配置

### 3. StyleGrid 风格一致性
自动学习和保持现有配置风格（命名、序列号、参数步长）

### 4. Dual-RAG 检索
结合 Style-RAG 和 Semantic-RAG，平衡风格一致性和语义正确性

### 5. 最小化修改
优先复用现有对象，只修改必要的部分，降低出错风险
