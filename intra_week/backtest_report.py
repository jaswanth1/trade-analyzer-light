"""
Backtest report generation for IntraWeek module.

Generates markdown report with equity curve, per-strategy breakdowns,
top/bottom trades, and monthly PnL table.
"""

from pathlib import Path


def generate_report(trades, metrics, output_path):
    """Generate markdown backtest report."""
    lines = []

    lines.append("# IntraWeek Backtest Report\n")

    if not trades:
        lines.append("No trades generated during backtest period.\n")
        Path(output_path).write_text("\n".join(lines))
        return

    # ── Summary ──
    lines.append("## Summary Statistics\n")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total Trades | {metrics['n_trades']} |")
    lines.append(f"| Win Rate | {metrics['win_rate']}% |")
    lines.append(f"| Avg PnL | {metrics['avg_pnl']:.2f}% |")
    lines.append(f"| Total PnL | {metrics['total_pnl']:.2f}% |")
    lines.append(f"| % Reaching 10%+ | {metrics['pct_10plus']}% |")
    lines.append(f"| % Reaching 20%+ | {metrics['pct_20plus']}% |")
    lines.append(f"| Avg Holding Days | {metrics['avg_holding_days']} |")
    lines.append(f"| Max Drawdown | {metrics['max_drawdown']:.2f}% |")
    lines.append(f"| Profit Factor | {metrics['profit_factor']} |")
    lines.append(f"| Sharpe Ratio | {metrics['sharpe']} |")
    lines.append(f"| Avg MFE | {metrics['avg_mfe']:.2f}% |")
    lines.append(f"| Avg MAE | {metrics['avg_mae']:.2f}% |")
    lines.append("")

    # ── Strategy Breakdown ──
    by_strategy = metrics.get("by_strategy", {})
    if by_strategy:
        lines.append("## By Strategy\n")
        lines.append("| Strategy | Trades | Win Rate | Avg PnL | Total PnL |")
        lines.append("|----------|--------|----------|---------|-----------|")
        for s, m in by_strategy.items():
            lines.append(
                f"| {s} | {m['n_trades']} | {m['win_rate']}% | "
                f"{m['avg_pnl']:.2f}% | {m['total_pnl']:.2f}% |"
            )
        lines.append("")

    # ── Exit Reason Breakdown ──
    by_exit = metrics.get("by_exit_reason", {})
    if by_exit:
        lines.append("## By Exit Reason\n")
        lines.append("| Reason | Trades | Avg PnL |")
        lines.append("|--------|--------|---------|")
        for r, m in by_exit.items():
            lines.append(f"| {r} | {m['n_trades']} | {m['avg_pnl']:.2f}% |")
        lines.append("")

    # ── Monthly PnL ──
    monthly = {}
    for t in trades:
        if t.entry_date:
            key = t.entry_date.strftime("%Y-%m")
            if key not in monthly:
                monthly[key] = {"trades": 0, "pnl": 0, "wins": 0}
            monthly[key]["trades"] += 1
            monthly[key]["pnl"] += t.pnl_pct
            if t.pnl_pct > 0:
                monthly[key]["wins"] += 1

    if monthly:
        lines.append("## Monthly Breakdown\n")
        lines.append("| Month | Trades | Wins | PnL % |")
        lines.append("|-------|--------|------|-------|")
        for month in sorted(monthly.keys()):
            m = monthly[month]
            wr = round(m["wins"] / m["trades"] * 100, 0) if m["trades"] > 0 else 0
            lines.append(f"| {month} | {m['trades']} | {m['wins']} ({wr:.0f}%) | {m['pnl']:.2f}% |")
        lines.append("")

    # ── Top Trades ──
    sorted_by_pnl = sorted(trades, key=lambda t: t.pnl_pct, reverse=True)

    top_n = min(10, len(sorted_by_pnl))
    if top_n > 0:
        lines.append("## Top Trades\n")
        lines.append("| Symbol | Strategy | Entry Date | PnL % | Exit | MFE % | Hold Days |")
        lines.append("|--------|----------|------------|-------|------|-------|-----------|")
        for t in sorted_by_pnl[:top_n]:
            sym = t.symbol.replace(".NS", "")
            lines.append(
                f"| {sym} | {t.strategy} | {t.entry_date} | "
                f"{t.pnl_pct:+.2f}% | {t.exit_reason} | "
                f"{t.mfe_pct:.2f}% | {t.holding_days} |"
            )
        lines.append("")

    # ── Worst Trades ──
    if len(sorted_by_pnl) > 5:
        lines.append("## Worst Trades\n")
        lines.append("| Symbol | Strategy | Entry Date | PnL % | Exit | MAE % | Hold Days |")
        lines.append("|--------|----------|------------|-------|------|-------|-----------|")
        for t in sorted_by_pnl[-min(10, len(sorted_by_pnl)):]:
            sym = t.symbol.replace(".NS", "")
            lines.append(
                f"| {sym} | {t.strategy} | {t.entry_date} | "
                f"{t.pnl_pct:+.2f}% | {t.exit_reason} | "
                f"{t.mae_pct:.2f}% | {t.holding_days} |"
            )
        lines.append("")

    # ── All Trades ──
    lines.append("<details>")
    lines.append("<summary>All Trades</summary>\n")
    lines.append("| # | Symbol | Strategy | Entry | Exit | PnL % | Reason | Score |")
    lines.append("|---|--------|----------|-------|------|-------|--------|-------|")
    for i, t in enumerate(trades, 1):
        sym = t.symbol.replace(".NS", "")
        lines.append(
            f"| {i} | {sym} | {t.strategy} | "
            f"{t.entry_date} | {t.exit_date} | {t.pnl_pct:+.2f}% | "
            f"{t.exit_reason} | {t.score:.0%} |"
        )
    lines.append("\n</details>\n")

    Path(output_path).write_text("\n".join(lines))
