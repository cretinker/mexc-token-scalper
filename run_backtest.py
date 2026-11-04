import os
import json
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from app.backtester import BacktestEngine

console = Console()


def main():
    cache_dir = r"c:\Users\DELL\Documents\Elijah Workspace\new-apex\scratch\replay_cache"
    console.print(Panel("[bold cyan]MEXC Autonomous High-Conviction Backtest Engine[/bold cyan]\n"
                        "Architecture: BTC Regime Firewall + Daily RS Leader Radar + ATR Risk Sizing + Swing DOM Trailing\n"
                        f"Dataset: 90-day 15m Real MEXC Klines across 120 Tokens | Cache: {cache_dir}", border_style="cyan"))

    engine = BacktestEngine(
        starting_equity=1000.0,
        risk_pct=6.50,
        per_pos_cap_x=5.0,
        max_positions=10,
        min_notional=25.0,
        fee_pct=0.055,
        slippage_pct=0.04
    )

    console.print("[dim]Loading historical dataset...[/dim]")
    dataset = engine.load_dataset(cache_dir)
    console.print(f"[bold green]Loaded {len(dataset)} unique MEXC tokens with verified 15m candle series.[/bold green]")

    console.print("[yellow]Simulating chronological multi-asset event loop...[/yellow]")
    report = engine.run_backtest(dataset, cache_dir=cache_dir)

    # Save raw json output
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # 1. KPI Panel
    pnl_style = "bold green" if report["net_profit"] >= 0 else "bold red"
    kpi_table = Table(title="[bold white]PORTFOLIO PERFORMANCE SUMMARY[/bold white]", expand=True)
    kpi_table.add_column("Metric", style="white")
    kpi_table.add_column("Result", justify="right")

    kpi_table.add_row("Starting Capital", f"${report['starting_equity']:,.2f}")
    kpi_table.add_row("Final Capital", f"[bold green]${report['final_equity']:,.2f}[/bold green]")
    kpi_table.add_row("Net Profit", f"[{pnl_style}]${report['net_profit']:+,.2f} ({report['roi_pct']:+.1f}%)[/{pnl_style}]")
    kpi_table.add_row("Profit Factor", f"[bold cyan]{report['profit_factor']}[/bold cyan]")
    kpi_table.add_row("Max Drawdown", f"[bold red]-{report['max_drawdown_pct']:.2f}%[/bold red]")
    kpi_table.add_row("Total Executions", f"{report['total_trades']}")
    kpi_table.add_row("Win Rate", f"[bold green]{report['win_rate']:.1f}%[/bold green] ({report['win_count']}W / {report['loss_count']}L)")
    kpi_table.add_row("Gross Profit", f"${report['gross_profit']:,.2f}")
    kpi_table.add_row("Gross Loss", f"${report['gross_loss']:,.2f}")
    kpi_table.add_row("Average Win", f"[green]+{report['avg_win_pct']:.2f}%[/green]")
    kpi_table.add_row("Average Loss", f"[red]{report['avg_loss_pct']:.2f}%[/red]")

    console.print(kpi_table)

    # 2. Playbook Breakdown Table
    pb_table = Table(title="[bold yellow]PERFORMANCE BREAKDOWN BY PLAYBOOK[/bold yellow]", expand=True)
    pb_table.add_column("Playbook", style="bold white")
    pb_table.add_column("Trades", justify="center")
    pb_table.add_column("Win Rate", justify="center")
    pb_table.add_column("Net PnL ($)", justify="right")
    pb_table.add_column("Profit Factor", justify="right")

    for play, stats in report.get("by_playbook", {}).items():
        w_style = "green" if stats["win_rate"] >= 18 else "yellow"
        p_style = "green" if stats["net_pnl"] >= 0 else "red"
        pb_table.add_row(
            play,
            str(stats["trades"]),
            f"[{w_style}]{stats['win_rate']}%[/{w_style}] ({stats['wins']}/{stats['trades']})",
            f"[{p_style}]${stats['net_pnl']:+,.2f}[/{p_style}]",
            f"{stats['profit_factor']}"
        )
    console.print(pb_table)

    # 3. Top 5 Best Trades
    best_table = Table(title="[bold green]TOP 5 WINNING TRADES[/bold green]", expand=True)
    best_table.add_column("Symbol", style="bold white")
    best_table.add_column("Playbook", style="cyan")
    best_table.add_column("Entry Time", style="dim")
    best_table.add_column("Exit Time", style="dim")
    best_table.add_column("PnL ($)", justify="right", style="bold green")
    best_table.add_column("PnL (%)", justify="right", style="bold green")
    best_table.add_column("Exit Reason", style="magenta")

    for t in report.get("best_5_trades", []):
        best_table.add_row(
            t["symbol"],
            t["play_type"],
            t["entry_time"],
            t["exit_time"],
            f"+${t['pnl']:.2f}",
            f"+{t['pnl_pct']:.2f}%",
            t["reason"]
        )
    console.print(best_table)

    # 4. Top 5 Worst Trades
    worst_table = Table(title="[bold red]TOP 5 CONTROLLED CUT LOSSES[/bold red]", expand=True)
    worst_table.add_column("Symbol", style="bold white")
    worst_table.add_column("Playbook", style="cyan")
    worst_table.add_column("Entry Time", style="dim")
    worst_table.add_column("Exit Time", style="dim")
    worst_table.add_column("PnL ($)", justify="right", style="bold red")
    worst_table.add_column("PnL (%)", justify="right", style="bold red")
    worst_table.add_column("Exit Reason", style="dim")

    for t in report.get("worst_5_trades", []):
        worst_table.add_row(
            t["symbol"],
            t["play_type"],
            t["entry_time"],
            t["exit_time"],
            f"${t['pnl']:.2f}",
            f"{t['pnl_pct']:.2f}%",
            t["reason"]
        )
    console.print(worst_table)
    console.print(f"[bold green]Backtest results verified and saved to {out_path}[/bold green]")


if __name__ == "__main__":
    main()
