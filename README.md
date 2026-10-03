# backtest — 无前视偏差的多标的回放回测库

纯 Python、零依赖（仅标准库），状态与结果全部保存在内存，由上层直接调用。

## 模型

- 输入为 1 分钟 OHLCV 记录（`Bar`），`ts` 为开盘时刻（UTC 秒），收盘时刻为 `ts + 60`。
  记录须按时间戳非递减到达（可跨任意批次），同刻标的顺序任意；`(symbol, ts)` 不重复，允许缺分钟。
- 每个事件时刻依次：发布此刻收盘的 1m/5m 行情与 SMA3 指标 → 记录权益 → 调用一次策略 →
  处理此刻开盘的成交（先全部卖出、再买入，各阶段按标的字典序）。
- 目标股数在决策后的首次开盘阶段尝试执行一次（可以是同一时间戳，但用的是开盘那根 K 线的
  开盘价，而非刚收盘那根）；买入受现金限制，未满足部分取消；无后续开盘则目标继续等待。
- 5 分钟窗按 UTC 整点起每 5 分钟对齐，集齐 5 根 1m K 线才在窗尾发布，缺一根整窗作废。
- 价格与现金均为整数分，不计手续费滑点，不允许借款与做空。

## 用法

```python
from backtest import ReplayEngine, Bar

def strategy(snapshot):           # snapshot: ts / symbols / cash / positions
    targets = {}
    for sym, frame in snapshot.symbols.items():
        if frame.sma3_1m is not None and frame.bar_1m.close > frame.sma3_1m:
            targets[sym] = 100    # 非负整数目标股数
        else:
            targets[sym] = 0
    return targets

eng = ReplayEngine(initial_cash=1_000_000, strategy=strategy)
eng.add_bars(batch1)              # 可任意分批追加
eng.add_bars(batch2)
eng.finish()                      # 处理最后一根 K 线的收盘；不强制平仓

eng.fills                         # 逐笔成交 List[Fill]
eng.equity_curve                  # 权益轨迹 List[EquityPoint]
eng.cash, eng.positions           # 最终现金与持仓
```

`snapshot.symbols[sym]` 为 `SymbolFrame(bar_1m, sma3_1m, bar_5m, sma3_5m)`；
指标不足 3 根已发布 K 线时为 `None`；尚无已发布 1m 行情的标的不出现在快照中。

## 运行测试

```bash
python3 -m pytest tests -q
```

- `tests/test_bars_and_indicators.py` — 5m 边界、缺失分钟、SMA 预热（手算小样本）
- `tests/test_trading.py` — 现金不足、同刻卖出释放资金、等待目标被覆盖（精确现金/持仓）
- `tests/test_determinism.py` — 跨批次与同刻重排不变性、开盘价之外字段不影响成交、未来数据不影响已完成结果
- `tests/test_scale.py` — 50 标的 × 20 万根 K 线，持有策略全程 < 20 秒
