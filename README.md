# task26100415-b — 无前视偏差的多标的回测库

纯 Python（仅标准库），由上层直接调用；行情分批传入，状态与结果保存在内存。

## 时间语义

- 输入为 1 分钟 OHLCV（`Bar`，时间戳为 UTC 开盘时刻，收盘 = +60s），价格/现金均为整数分。
- 每个时刻：先公布此时收盘的全部 1m 行情与指标 → 记录权益 → 调用一次策略 → 处理此时开盘的成交。
- 5 分钟 K 线按 UTC 整 5 分钟分窗，集齐 5 根 1m 才在窗口结束时发布；缺一根整窗作废，不跨窗补齐，不暴露临时值。
- 两个周期各自提供 SMA3（最近 3 根已发布收盘价的均值），不足 3 根为 `None`，缺口不重置计数。
- 目标股数在决策后的首次开盘阶段执行一次（可以是同一时间戳，但用的是下一根 K 线的开盘价）；未成交部分取消；没有后续开盘的标的，目标继续等待，新目标覆盖旧目标。
- 同刻先卖后买，两阶段内部均按标的名称字典序；买入受剩余现金限制，成交尽可能多整股；不允许借款/做空；不计手续费滑点，成交不受成交量限制。

## 用法

```python
from backtest import Bar, ReplayEngine

def strategy(snapshot):
    # snapshot: ts / cash / positions / symbols（每个标的含 m1、m5 的
    # PeriodView(bar, sma3)）；尚无已公布 1m 行情的标的不出现。
    # 必须为快照内每个标的返回非负整数目标股数。
    return {v.symbol: 0 for v in snapshot.symbols}

engine = ReplayEngine(strategy, initial_cash=1_000_000)
engine.add_bars([Bar("AAA", 0, 100, 110, 90, 105, 10), ...])  # 可多次分批追加
engine.finish()              # 处理最后已输入 K 线的收盘；不强制平仓
engine.fills                 # 逐笔成交 [Fill(ts, symbol, side, qty, price)]
engine.equity_curve          # 权益轨迹 [EquityPoint(ts, cash, positions, equity)]
engine.cash, engine.positions
```

输入约束（违反抛 `ValueError`）：时间戳分钟对齐、全局非递减、同标的+同刻不重复。`finish()` 之后不可再追加（抛 `RuntimeError`）。

## 运行测试

```bash
python3 -m pytest          # 全部 14 个测试（含规模测试）
python3 -m pytest tests/test_scale.py -q   # 仅规模测试：50 标的 x 20 万根 < 20s
```

- `tests/test_publication.py` — 5 分钟边界、缺失分钟、指标预热（手算小样本）
- `tests/test_trading.py` — 现金不足部分成交并取消、同刻卖出释放资金、等待开盘的目标被覆盖、权益记录时点
- `tests/test_determinism.py` — 跨批次/同刻重排不变性、开盘成交只依赖开盘价、未来数据不影响已完成结果、输入与策略输出校验
- `tests/test_scale.py` — 规模与性能
