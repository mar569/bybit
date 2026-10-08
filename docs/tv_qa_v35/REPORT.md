# EdBot TV QA (tradingview-mcp CLI, 15m)

Дата прогона: после v3.5 compile fix.

## Активы

| Symbol | EdBot | Studies на графике | Screenshot |
|--------|-------|-------------------|------------|
| BYBIT:BTCUSDT.P | 3.5 | 15 | ok |
| BYBIT:ETHUSDT.P | 3.5 | 15 | ok |
| BYBIT:SOLUSDT.P | 3.5 | 15 | ok |
| BYBIT:STRKUSDT.P | 3.5 | 15 | ok |
| BYBIT:DOGEUSDT.P | 3.5 | 15 | ok |
| BYBIT:WIFUSDT.P | 3.5 | 15 | ok |
| BYBIT:AVAXUSDT.P | 3.5 | 15 | ok |
| BYBIT:LINKUSDT.P | 3.5 | 15 | ok |
| VANTAGE:UKOUSD | 3.5 | 15 | ok |

PNG: `D:\tradingview-mcp-main\tradingview-mcp-main\screenshots\qa_*.png`  
State JSON: `docs/tv_qa_v35/*.state.json`

## Наблюдения (прогон)

1. **15 studies** — на графике Beluga/LuxAlgo/ICT; EdBot теряется визually. Для QA: оставить Volume + EdBot.
2. **Таблица «Главная идея» = `???`** — символы ①–⑥ не рендерятся в `table.cell` → **v3.6: `1)`–`6)`**.
3. **Edge «нет edge R:R 0.97»** на STRK после пампа — корректно (min 1.15), боксов SL/TP нет.
4. **Клин** часто «клин ↑» без белых линий, если мало касаний → **v3.6: пунктир + подпись**.
5. **Pine `data labels/tables`** через MCP = 0 (ограничение API), ориентир: screenshot + таблица на графике.
6. Редактор **Ed Story RBR v1** ≠ индикатор на chart **Ed Bot Analysis** — не смешивать.

## Повтор прогона

```powershell
# CDP: scripts\Launch-TradingView-Debug.bat
powershell -File scripts\tv_edbot_qa_run.ps1
```

## Следующие шаги (код)

- [ ] Паттерны 1-2-3 / ГиП на истории (как 2×)
- [ ] Пресет «Ed only» (input профиль)
- [ ] Снизить min confidence для альтов (input group)
