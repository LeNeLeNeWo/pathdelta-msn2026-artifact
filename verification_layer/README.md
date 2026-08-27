# verification_layer/ - 验证层

本目录实现 PathDelta 的验证层，负责验证生成的配置补丁的正确性（静态语法检查 + 动态网络仿真）。

---

## 文件说明

| 文件 | 用途 | 主要函数/类 |
|------|------|-------------|
| `static_verifier.py` | **静态语法验证** | `StaticVerifier`, `verify_syntax()` |
| `dynamic_verifier.py` | **动态仿真验证（ShadowSafe）** | `DynamicVerifier`, `verify_routing()` |
| `verifier.py` | 主验证器（统一接口） | `Verifier`, `verify_patch()` |
| `assertions.py` | 验证断言 | `AssertionChecker`, `Assertion` |
| `lab_generator.py` | Kathara 实验室生成 | `LabGenerator` |
| `__init__.py` | 模块导出 | `Verifier` |

---

## 核心组件

### StaticVerifier (static_verifier.py) - **语法检查**

使用 Docker + FRR 进行静态语法验证：

```python
from verification_layer.static_verifier import StaticVerifier

verifier = StaticVerifier()

# 验证单个补丁
result = verifier.verify_syntax(
    patch_text="""
    route-map RM_PATHDELTA permit 10
     match ip address prefix-list PL_TEST
     set local-preference 200
    !
    """,
    device="router1"
)

if result["valid"]:
    print("✓ Syntax OK")
else:
    print(f"✗ Syntax errors: {result['errors']}")
```

**验证方法**：
1. 启动 FRR Docker 容器
2. 将补丁注入容器
3. 运行 `vtysh -c "show running-config"`
4. 检查是否有语法错误

### DynamicVerifier (dynamic_verifier.py) - **ShadowSafe 仿真验证**

使用 Kathara 进行动态网络仿真验证：

```python
from verification_layer.dynamic_verifier import DynamicVerifier

verifier = DynamicVerifier()

# 验证完整配置
result = verifier.verify_routing(
    patches={
        "new_york": "route-map RM_TEST...",
        "chicago": "route-map RM_TEST..."
    },
    topology=topology_dict,
    intent={
        "type": "prefer_with_backup",
        "prefix": "10.0.0.0/24",
        "primary_exit": "new_york"
    },
    timeout_sec=180
)

if result["passed"]:
    print("✓ Dynamic verification PASSED")
    print(f"  Route: {result['actual_route']}")
else:
    print(f"✗ Failed: {result['reason']}")
```

**验证流程**：
1. 生成 Kathara 实验室
2. 应用配置补丁
3. 启动虚拟网络
4. 等待 BGP/OSPF 收敛
5. 检查路由表
6. 验证流量路径是否符合意图

**ShadowSafe 特性**：
- 在虚拟环境中测试配置
- 不影响生产网络
- 检测配置错误和路由异常
- 支持 BGP 收敛检测

### Verifier (verifier.py) - **统一验证接口**

主验证器，支持静态和动态验证：

```python
from verification_layer import Verifier

verifier = Verifier()

# 静态验证（快速）
static_result = verifier.verify_static(patch_text, device)

# 动态验证（完整）
dynamic_result = verifier.verify_dynamic(
    patches=all_patches,
    topology=topology,
    intent=intent
)

# 组合验证
result = verifier.verify(
    patches=all_patches,
    topology=topology,
    intent=intent,
    static=True,   # 先做静态验证
    dynamic=True   # 再做动态验证
)
```

### 验证类型

| 类型 | 方法 | 依赖 | 描述 | 速度 |
|------|------|------|------|------|
| 静态语法 | `verify_static()` | Docker + FRR | 检查 FRR 配置语法 | 快（秒级） |
| 动态仿真 | `verify_dynamic()` | Kathara | 在仿真环境中验证路由行为 | 慢（分钟级） |
| 语义验证 | `verify_semantic()` | - | 检查配置是否符合意图语义 | 快（秒级） |

---

## 静态验证

使用 Docker 容器运行 FRR 语法检查：

```python
from verification_layer.static_verifier import StaticVerifier

verifier = StaticVerifier()

# 验证单个补丁
result = verifier.verify_syntax(
    patch_text="""
    route-map RM_PATHDELTA permit 10
     match ip address prefix-list PL_TEST
     set local-preference 200
    !
    """,
    device="router1"
)

if result["valid"]:
    print("Syntax OK")
else:
    print(f"Errors: {result['errors']}")
```

**检测的错误类型**：
- 语法错误（缺少 `!`、缩进错误）
- 未定义的引用（引用不存在的 prefix-list）
- 参数范围错误（local-pref > 4294967295）
- 命令拼写错误

---

## 动态验证（ShadowSafe）

使用 Kathara 进行网络仿真验证：

```python
from verification_layer.dynamic_verifier import DynamicVerifier

verifier = DynamicVerifier()

# 验证完整配置
result = verifier.verify_routing(
    patches={
        "new_york": "route-map RM_TEST...",
        "chicago": "route-map RM_TEST..."
    },
    topology=topology_dict,
    intent={
        "type": "prefer_with_backup",
        "prefix": "10.0.0.0/24",
        "primary_exit": "new_york"
    },
    timeout_sec=180
)

if result["passed"]:
    print("Dynamic verification PASSED")
    print(f"  Expected route: {intent['primary_exit']}")
    print(f"  Actual route: {result['actual_route']}")
else:
    print(f"Failed: {result['reason']}")
```

**验证步骤**：
1. **生成 Kathara 实验室**：创建虚拟网络拓扑
2. **应用配置补丁**：将补丁注入虚拟路由器
3. **启动网络**：启动所有虚拟路由器
4. **等待收敛**：等待 BGP/OSPF 协议收敛（60-180秒）
5. **检查路由表**：查询路由表，验证路由路径
6. **验证意图**：检查实际路由是否符合用户意图

**收敛检测**：
- BGP: 检查 `show ip bgp summary` 中的 Established 状态
- OSPF: 检查 `show ip ospf neighbor` 中的 Full 状态
- 超时处理：如果超时未收敛，返回失败

**验证断言**：
- 主备出口：流量走主出口
- ECMP：流量在多个出口间负载均衡
- 避免出口：流量不走被避免的出口

---

## 断言系统 (assertions.py)

定义验证断言：

```python
from verification_layer.assertions import AssertionChecker, Assertion

checker = AssertionChecker()

# 添加断言
checker.add_assertion(Assertion(
    name="bgp_session_up",
    condition="show ip bgp summary | grep Established",
    expected=True
))

# 检查断言
results = checker.check_all(lab_runner)
```

---

## 实验室生成 (lab_generator.py)

生成 Kathara 实验室配置：

```python
from verification_layer.lab_generator import LabGenerator

generator = LabGenerator()

lab_dir = generator.generate(
    topology=topology_dict,
    baseline_configs=baseline_configs,
    patches=patches,
    output_dir="experiments/results/lab"
)
```

---

## 使用示例

### 完整验证流程

```python
from verification_layer import Verifier

verifier = Verifier()

# 1. 静态验证
static_result = verifier.verify_static(patch_text, device)
if not static_result["valid"]:
    raise ValueError(f"Syntax error: {static_result['errors']}")

# 2. 动态验证（可选）
if enable_dynamic:
    dynamic_result = verifier.verify_dynamic(
        patches=all_patches,
        topology=topology,
        intent=intent
    )
    if not dynamic_result["passed"]:
        raise ValueError(f"Dynamic verification failed: {dynamic_result['reason']}")
```

---

## 目录结构

```
verification_layer/
├── __init__.py         # 模块导出
├── verifier.py         # 主验证器
├── assertions.py       # 验证断言
└── lab_generator.py    # 实验室生成
```

---

## 依赖

- **静态验证**: Docker + `frrouting/frr:latest` 镜像
- **动态验证**: Kathara 3.8.0+
