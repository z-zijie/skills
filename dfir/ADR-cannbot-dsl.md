# 决策记录：IR 改用 cannbot-dsl

> 指令：*"IR 就用 cannbot-dsl"*（2026-11-04）

## 结论

**cannbot-dsl 已确认可用（0.8.0，pip 可装），但它是一个"算子 JIT 编写框架"，
不是一个可导出的性能 IR。** 它可以作为 IR 的**来源**（DataFlow 用 DSL 写、我们从中抽特征），
但不能直接当"已有的 IR 格式"读进来。

因此我的做法是：**保留 dfir 的估算器，把 cannbot-dsl 作为 flow 的来源**，
从 DSL kernel 源码/结构里抽成本特征，喂给估算器。

## 我实际验证到的事实

```
$ python3.12 -c "import cannbotdsl"
version: 0.8.0          # 从 pip 装（cannbot_dsl-0.8.0-cp312...whl）
```

公开 API 有 141 个符号，与"图/IR"相关的：
- `compile(func, *args, **kwargs)` —— JIT 编译入口
- `Tiler` / `make_tiler(tiler, *, alignment=None)` / `Tile` / `PartitionTiler` /
  `tile_slice` / `tile_slice_multi` —— 分块抽象
- `Dim` / `TensorSpec` / `Coord` / `Layout` / `MemLoc` / `PIPE` / `dtype`

**没有**：`ir` / `graph` / `trace` 这类可序列化的中间表示，也没有 dump 成文本的入口。

DSL 的 kernel 是**用 Python 写的**（`@kernel` 装饰的类，`__call__` 里是
`for t in cannbotdsl.range(...)` + `mem_copy` / `vadd` 这类调用）。
所以"DataFlow 描述"在 cannbot-dsl 里**就是 kernel 的 Python 源码本身**。

## 这对性能建模意味着什么

**好消息**：Python 源码是可分析的对象。比我的 JSON IR 信息量大得多——
循环结构、索引表达式、每次迭代的搬运/计算调用，全都在源码里，
正是我 M11 缺的那部分（scalar 成本对应的循环/索引开销）。

**坏消息**：它不是"读进来就能算"的数据结构，需要一层**从 DSL kernel 抽取特征**的分析器。

## 迁移方案

```
cannbot-dsl kernel (Python 源码)
        │
        ├─ AST 静态分析：循环层数、迭代次数表达式、每迭代的 mem_copy/vector 调用
        │                （→ 这正好补上 scalar 特征的缺口）
        │
        ▼
   dfir Flow（保留现有 node/pipe/bytes 模型）
        │
        ▼
   estimate() → 周期/微秒
```

具体步骤：
1. **写一个 DSL→dfir 的抽取器**：解析 `@kernel` 类的 `__call__`，识别
   `mem_copy`（→ MTE2/MTE3 节点）、`vadd` 等向量调用（→ VEC 节点）、
   `cannbotdsl.range`（→ 循环次数，喂给 scalar 模型）
2. **补 scalar 特征**：`loop_depth`、`loop_trip_count`、`per_iter_index_ops`
   从 AST 直接可得，这是我阶段 0 要的东西
3. **估算器不变**：`ir.py` 的 Flow/Node 结构保留，只是 flow 由 DSL 生成而非手写 JSON
4. **验证**：用 cannbot-dsl 写两个算子（对应 basic / advanced 的结构差异），
   看 scalar 预测能否区分 0.97µs 和 34µs

## 为什么不直接改写成 DSL 的编译器内部 IR

`cannbotdsl.compile()` 内部肯定有 IR，但：
- 没有公开 dump 接口，依赖内部结构会在版本升级时碎掉（0.7.0 → 0.8.0 已经变过）
- 我的目标是**性能预测**，不是编译；从源码 AST 抽取足够且稳定
- 保留 dfir 的 Node 模型让我能继续用已有的 7 项标定（MTE2/MTE3/VEC/AIC/AIC_LOAD/SCALAR/MIX）
  和 M10 的多核模型

## 待确认

- cannbot-dsl 的 `@kernel` 类是否有稳定的 AST 形态（不同算子写法差异大）
- `PartitionTiler` / `make_tiler` 是高层 API，用它写的 kernel 循环结构被封装了，
  AST 里看不到迭代次数——这类 kernel 需要另外的抽取方式
