# policy_layer/ - 策略映射层

本目录实现 PathDelta 的策略映射层，负责将 IntentCard 转换为具体的路由策略（PolicyEntry），并计算符合风格的参数值。

---

## 文件说明

| 文件 | 用途 | 主要函数/类 |
|------|------|-------------|
| `runner.py` | 策略层运行入口 | `run_policy()` |
| `models.py` | 策略数据模型 | `PolicyEntry`, `RolePolicy` |
| `mechanism_selector.py` | 机制选择器 | `select_mechanism()` |
| `param_solver.py` | 参数求解器（基础版） | `solve_bgp_local_pref()`, `solve_ospf_cost()` |
| `adaptive_param_solver.py` | **自适应参数求解器（新版）** | `AdaptiveParamSolver` |
| `normalizer.py` | 意图规范化 | `normalize_intent()` |
| `topology_view.py` | 拓扑视图 | `TopologyView` |
| `ospf_steering_resolver.py` | OSPF 路径计算 | `resolve_ospf_steering()` |
| `preference_tiers.py` | 偏好层级处理 | `PreferenceTiers`, `build_preference_tiers()` |
| `aggregator.py` | 策略聚合 | `aggregate_policies()` |
| `emitters.py` | 策略输出 | `emit_policy()` |
| `registry.py` | 机制注册表 | `MechanismRegistry` |

### 子目录

| 目录 | 用途 |
|------|------|
| `handlers/` | 各意图类型的策略构建器 (builders) |

---

## 核心组件

### PolicyEntry (models.py)

策略条目数据结构：

```python
from policy_layer.models import PolicyEntry

policy = PolicyEntry(
    intent_id="intent_001",
    type="prefer_with_backup",
    proto="bgp",
    mechanism="local_pref",
    scope="prefix",
    prefix="10.0.0.0/24",
    primary_exit="new_york",
    backup_exit="chicago",
    affected_devices=["new_york", "chicago"],
    params={"local_pref": 200, "backup_local_pref": 100}
)
```

### 策略运行 (runner.py)

主入口函数：

```python
from policy_layer.runner import run_policy

result = run_policy(
    intent_card=intent_dict,
    topology=topology_dict,
    sketch=sketch_dict
)
# 返回: {
#   "intent_id": "...",
#   "proto": "bgp",
#   "mechanism": "local_pref",
#   "affected_devices": [...],
#   "params": {...}
# }
```

### 机制选择 (mechanism_selector.py)

根据意图类型和拓扑选择最佳机制：

```python
from policy_layer.mechanism_selector import select_mechanism

mechanism = select_mechanism(
    intent_type="prefer_with_backup",
    topology=topology_dict,
    sketch=sketch_dict
)
# 返回: "local_pref" 或 "ospf_cost" 等
```

**选择逻辑**：
- 优先使用 BGP 机制（如果 BGP 已启用）
- 如果只有 OSPF，使用 OSPF cost 机制
- 根据现有配置的"工程痕迹"（existing_lprefs, existing_costs）判断

---

## 参数求解器

### AdaptiveParamSolver (adaptive_param_solver.py) - **核心创新**

自适应参数求解器，解决 brownfield 网络的参数分配问题：

```python
from policy_layer.adaptive_param_solver import AdaptiveParamSolver

solver = AdaptiveParamSolver()

# BGP Local-Preference 求解
tiers = {"exit_A": 2, "exit_B": 1, "exit_C": 0, "exit_D": -1}
existing_lprefs = [100, 150, 200]

lp_map = solver.solve_bgp_local_pref(
    tiers=tiers,
    existing_lprefs=existing_lprefs,
    lp_step=50
)
# 返回: {"exit_A": 250, "exit_B": 200, "exit_C": 150, "exit_D": 50}

# OSPF Cost 求解
cost_map = solver.solve_ospf_cost(
    tiers=tiers,
    existing_costs=[10, 20, 30],
    cost_step=10
)
# 返回: {"exit_A": 5, "exit_B": 15, "exit_C": 25, "exit_D": 55}
```

**关键特性**：

1. **鲁棒步长推断（Robust Step Inference）**
   - 使用众数法（Mode）代替 GCD
   - 对噪点和异常值更鲁棒
   - 置信度评估（confidence >= 0.3）

2. **三级冲突回避（Three-Level Collision Avoidance）**
   - Level 1: Grid Alignment（网格对齐）
   - Level 2: Micro-step（半步长）
   - Level 3: Bisection（二分法）
   - 成功处理窄缝（narrow gaps）

3. **区间感知分配（Interval-Aware Allocation）**
   - 识别现有值之间的间隔
   - 优先在间隔中插入新值
   - 避免超出有效范围

4. **ECMP 支持**
   - 相同 tier 的出口获得相同参数值
   - 确保等价多路径（Equal-Cost Multi-Path）

**改进对比**：

| 特性 | 旧版 (param_solver.py) | 新版 (adaptive_param_solver.py) |
|------|----------------------|------------------------------|
| 步长检测 | GCD（对噪点敏感） | 众数法（鲁棒） |
| 冲突回避 | 单级（只能向上跳） | 三级降级策略 |
| 窄缝处理 | 失败或跳过 | 成功插入（半步长/二分法） |
| 置信度评估 | 无 | 有（confidence >= 0.3） |

### 参数求解器（基础版）(param_solver.py)

基础参数求解器，提供简单的参数分配：

```python
from policy_layer.param_solver import solve_bgp_local_pref, solve_ospf_cost

# BGP Local-Preference
lp_map = solve_bgp_local_pref(
    tiers={"exit_A": 1, "exit_B": 0},
    sketch={"bgp_style": {"existing_lprefs": [100, 150]}},
    lp_step=50,
    detected_step=50
)

# OSPF Cost
cost_map = solve_ospf_cost(
    tiers={"path_A": 1, "path_B": -1},
    sketch={"ospf_style": {"existing_costs": [10, 20]}},
    cost_step=10,
    detected_step=10
)
```

**参数语义**：
- **tier > 0**：提升优先级
  - BGP: local-pref **增加**
  - OSPF: cost **减少**
- **tier = 0**：保持基线
- **tier < 0**：降低优先级
  - BGP: local-pref **减少**
  - OSPF: cost **增加**

### PreferenceTiers (preference_tiers.py)

偏好层级数据结构：

```python
from policy_layer.preference_tiers import PreferenceTiers

# 定义出口优先级
tiers = PreferenceTiers({
    "exit_primary": 2,    # 最高优先级
    "exit_backup": 1,     # 次优先级
    "exit_baseline": 0,   # 基线（不改）
    "exit_avoid": -1      # 避免（降权）
})

# 验证单调性
tiers.validate_monotonicity()
```

**Tier 语义约定**：
- `tier > 0`: 提升优先级（higher BGP local-pref / lower OSPF cost）
- `tier == 0`: 保持基线（minimize changes）
- `tier < 0`: 显式降权/规避（lower BGP local-pref / higher OSPF cost）

---

## 支持的机制

### BGP 机制

| 机制 | 描述 | 适用意图 | 实现状态 |
|------|------|----------|---------|
| `local_pref` | 本地优先级 | prefer_with_backup, ordered_preference, ecmp | ✅ 完整实现 |
| `local_pref_ladder` | 阶梯式本地优先级 | ordered_preference | ✅ 完整实现 |
| `local_pref_degrade` | 降低本地优先级 | avoid_exit | ✅ 完整实现 |

### OSPF 机制

| 机制 | 描述 | 适用意图 | 实现状态 |
|------|------|----------|---------|
| `ospf_cost` | 接口链路开销 | prefer_with_backup, path_migration | ✅ 完整实现 |
| `ospf_cost_ladder` | 阶梯式链路开销 | ordered_preference | ✅ 完整实现 |

**注意**：PathDelta 专注于 BGP local-preference 和 OSPF cost 两种核心机制，这两种机制覆盖了大部分实际场景。

---

## 使用示例

### 完整流程

```python
from policy_layer.runner import run_policy
from policy_layer.adaptive_param_solver import AdaptiveParamSolver

intent = {
    "intent_id": "test_001",
    "type": "prefer_with_backup",
    "prefix": "10.0.0.0/24",
    "primary_exit": "new_york",
    "backup_exit": "chicago"
}

topology = {...}  # 从 topology.yaml 加载
sketch = {...}    # 从 CurrentPolicySketch.yaml 加载

policy_result = run_policy(
    intent_card=intent,
    topology=topology,
    sketch=sketch
)

print(f"Protocol: {policy_result['proto']}")
print(f"Mechanism: {policy_result['mechanism']}")
print(f"Affected: {policy_result['affected_devices']}")
print(f"Params: {policy_result['params']}")
```

### 使用自适应参数求解器

```python
from policy_layer.adaptive_param_solver import AdaptiveParamSolver

solver = AdaptiveParamSolver()

# 场景：现有网络有 local-pref 值 [100, 150, 200]
# 需要为 4 个出口分配优先级
tiers = {
    "exit_primary": 2,      # 最高优先级
    "exit_secondary": 1,    # 次优先级
    "exit_baseline": 0,     # 保持基线
    "exit_avoid": -1        # 避免
}

existing_lprefs = [100, 150, 200]

# 求解
lp_map = solver.solve_bgp_local_pref(
    tiers=tiers,
    existing_lprefs=existing_lprefs,
    lp_step=50
)

# 结果示例：
# {
#   "exit_primary": 250,     # 200 + 50
#   "exit_secondary": 200,   # 已存在，复用
#   "exit_baseline": 150,    # 已存在，复用
#   "exit_avoid": 50         # 100 - 50
# }
```

### OSPF Cost 求解示例

```python
from policy_layer.adaptive_param_solver import AdaptiveParamSolver

solver = AdaptiveParamSolver()

# 场景：调整内部路径优先级
tiers = {
    "path_preferred": 1,    # 优先路径（cost 更小）
    "path_avoid": -1        # 避免路径（cost 更大）
}

existing_costs = [10, 20, 30]

cost_map = solver.solve_ospf_cost(
    tiers=tiers,
    existing_costs=existing_costs,
    cost_step=10
)

# 结果示例：
# {
#   "path_preferred": 5,    # 10 - 5（最小 cost，最优先）
#   "path_avoid": 40        # 30 + 10（更大 cost，避免）
# }
```

---

## 测试

### 运行单元测试

```bash
# 测试自适应参数求解器
python -m pytest policy_layer/test_adaptive_param_solver.py -v

# 测试基础参数求解器
python -m pytest policy_layer/test_param_solver_baseline_semantics.py -v
```

### 演示脚本

```bash
# 运行自适应求解器演示
python policy_layer/demo_adaptive_solver.py
```

---

## 目录结构

```
policy_layer/
├── __init__.py
├── runner.py                       # 运行入口
├── models.py                       # 数据模型
├── mechanism_selector.py           # 机制选择
├── param_solver.py                 # 参数求解器（基础版）
├── adaptive_param_solver.py        # 自适应参数求解器（新版）⭐
├── normalizer.py                   # 意图规范化
├── topology_view.py                # 拓扑视图
├── ospf_steering_resolver.py       # OSPF 路径计算
├── preference_tiers.py             # 偏好层级
├── aggregator.py                   # 策略聚合
├── emitters.py                     # 策略输出
├── registry.py                     # 机制注册
├── test_adaptive_param_solver.py   # 自适应求解器测试
├── test_param_solver_baseline_semantics.py  # 基础求解器测试
├── demo_adaptive_solver.py         # 演示脚本
└── handlers/                       # 策略处理器
    ├── __init__.py
    ├── base.py                     # 基础构建器
    ├── prefer_with_backup_builder.py # 主备出口构建器
    ├── ecmp_builder.py             # ECMP 构建器
    ├── ordered_preference_builder.py # 有序偏好构建器
    ├── pin_to_exit_builder.py      # 固定出口构建器
    ├── avoid_exit_builder.py       # 避免出口构建器
    ├── path_migration_builder.py   # 路径迁移构建器
    └── ospf_steering_builder.py    # OSPF 路径引导构建器
```

---

## 关键设计原则

### 1. Tier 语义一致性

所有机制都遵循统一的 tier 语义：
- `tier > 0`: 提升优先级
- `tier == 0`: 保持基线
- `tier < 0`: 降低优先级

### 2. 风格感知量化（Style-Aware Quantization）

参数值必须符合现有网络的"网格模式"：
- 检测现有值的步长（step）
- 新值 = baseline ± (tier × detected_step)
- 确保风格一致性

### 3. 冲突回避（Collision Avoidance）

生成的参数值不能与现有值冲突：
- 检测冲突
- 使用三级降级策略找到非冲突值
- 确保参数有效性

### 4. 单调性约束（Monotonicity Constraint）

参数值必须保持 tier 的单调性：
- BGP: tier(A) > tier(B) ⇒ local-pref(A) > local-pref(B)
- OSPF: tier(A) > tier(B) ⇒ cost(A) < cost(B)

### 5. ECMP 支持

相同 tier 的出口必须获得相同参数值：
- tier(A) == tier(B) ⇒ param(A) == param(B)
- 确保等价多路径负载均衡

---

## 相关文档

- [AdaptiveParamSolver 设计文档](adaptive_param_solver.py) - 自适应参数求解器的详细实现
- [PreferenceTiers 规范](preference_tiers.py) - Tier 语义和约束
- [参数求解测试](test_adaptive_param_solver.py) - 完整的测试用例
