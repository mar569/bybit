# Отрисовка PNG/TV по `docs/.cursor_pdf_pages`

`ED_PDF_CHART_STYLE=1` (default) — один сетап на график:

| Приоритет | Режим | На PNG |
|-----------|--------|--------|
| 1 | **pattern** | Наклонные границы фигуры + **Вход / SL / цель** (`chart_pattern_draw`) |
| 2 | **smc** | BOS/MSS, свип, OB, FVG (`chart_education_visual`) |
| 3 | **range** | Боковик: поддержка/сопротивление, без RBR fade_top |
| 4 | **structure** | До 2 swing H/L + ключевые уровни |

**Выключено:** synthetic RBR, «отказ? → вниз», ghost-retest, R↑/R↓ + swings + RBR одновременно.

Отключить PDF-режим (legacy): `ED_PDF_CHART_STYLE=0`.
