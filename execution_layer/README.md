# execution_layer/ - 执行层

本目录实现 PathDelta 的执行层，负责将验证通过的配置补丁应用到目标设备。

---

## 文件说明

| 文件 | 用途 | 主要函数/类 |
|------|------|-------------|
| `executor.py` | 配置执行器 | `ConfigExecutor`, `execute_patch()` |
| `__init__.py` | 模块导出 | `ConfigExecutor` |

---

## 核心组件

### ConfigExecutor (executor.py)

配置执行器，支持多种执行模式：

```python
from execution_layer import ConfigExecutor

executor = ConfigExecutor()

# 执行配置补丁
result = executor.execute(
    patches={"new_york": "route-map RM_TEST...", "chicago": "..."},
    mode="kathara",  # 或 "ssh", "dry-run"
    lab_dir="experiments/results/lab"
)
```

---

## 执行模式

| 模式 | 描述 | 用途 |
|------|------|------|
| `kathara` | Kathara 仿真环境 | 测试/验证 |
| `ssh` | SSH 远程执行 | 生产环境 |
| `dry-run` | 只打印命令 | 调试 |

---

## 使用示例

### Kathara 模式

```python
from execution_layer import ConfigExecutor

executor = ConfigExecutor()

# 在 Kathara 实验室中执行
result = executor.execute(
    patches={
        "new_york": """
        configure terminal
        route-map RM_PATHDELTA permit 10
         match ip address prefix-list PL_TEST
         set local-preference 200
        exit
        """,
        "chicago": "..."
    },
    mode="kathara",
    lab_dir="/path/to/kathara/lab"
)

if result["success"]:
    print("Configuration applied successfully")
else:
    print(f"Failed: {result['errors']}")
```

### Dry-run 模式

```python
# 只打印命令，不实际执行
result = executor.execute(
    patches=patches,
    mode="dry-run"
)
# 输出将显示要执行的命令
```

---

## 执行结果

```python
{
    "success": True,
    "devices": {
        "new_york": {"status": "ok", "output": "..."},
        "chicago": {"status": "ok", "output": "..."}
    },
    "errors": [],
    "duration_sec": 5.2
}
```

---

## 目录结构

```
execution_layer/
├── __init__.py     # 模块导出
└── executor.py     # 配置执行器
```

---

## 注意事项

1. **Kathara 模式**: 需要先启动 Kathara 实验室
2. **SSH 模式**: 需要配置设备凭据（不在代码中硬编码）
3. **回滚**: 执行前会自动备份当前配置
