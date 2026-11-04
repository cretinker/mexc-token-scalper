import asyncio
import logging
import time
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live

from app.config import settings
from app.database import db
from app.paper_engine import paper_engine
from app.position_guard import position_guard
from app.bot import bot

# Suppress debug logs in CLI live mode
logging.basicConfig(level=logging.WARNING)
console = Console()


def generate_dashboard() -> Panel:
    pf = paper_engine.get_portfolio_summary()
    open_positions = db.get_open_positions()
    recent_signals = db.get_recent_signals(limit=8)

    # 1. Top KPI Summary
    pnl_color = "green" if pf["total_pnl"] >= 0 else "red"
    kpi_text = (
        f"[bold white]Equity:[/bold white] [bold green]${pf['total_equity']:,.2f}[/bold green]  |  "
        f"[bold white]Cash:[/bold white] ${pf['cash']:,.2f}  |  "
        f"[bold white]Net PnL:[/bold white] [{pnl_color}]${pf['total_pnl']:+,.2f} ({pf['roi_pct']:+.1f}%)[/{pnl_color}]  |  "
        f"[bold white]Win Rate:[/bold white] {pf['win_rate']:.1f}% ({pf['win_count']}W / {pf['loss_count']}L)  |  "
        f"[bold white]Active Positions:[/bold white] {pf['open_positions_count']}/{pf['max_positions']}"
    )

    # 2. Open Positions Table
    pos_table = Table(title="[bold cyan]ACTIVE POSITIONS & STEPPED EXITS[/bold cyan]", expand=True)
    pos_table.add_column("Symbol", style="bold white")
    pos_table.add_column("Playbook", style="cyan")
    pos_table.add_column("Entry Price", justify="right")
    pos_table.add_column("Current Price", justify="right")
    pos_table.add_column("PnL %", justify="right")
    pos_table.add_column("Value ($)", justify="right")
    pos_table.add_column("Stop Loss", justify="right")
    pos_table.add_column("Progress", style="magenta")

    if not open_positions:
        pos_table.add_row("[dim]No active positions[/dim]", "--", "--", "--", "--", "--", "--", "Waiting for signals")
    else:
        for p in open_positions:
            cur = p["current_price"]
            entry = p["entry_price"]
            pnl_pct = ((cur - entry) / entry) * 100.0 if entry > 0 else 0.0
            pnl_style = "bold green" if pnl_pct >= 0 else "bold red"
            fmt_p = f"${cur:.7f}" if cur < 0.01 else f"${cur:.4f}"
            fmt_e = f"${entry:.7f}" if entry < 0.01 else f"${entry:.4f}"
            fmt_s = f"${p['stop_loss']:.7f}" if p['stop_loss'] < 0.01 else f"${p['stop_loss']:.4f}"
            val = p["remaining_units"] * cur

            step_status = "ENTRY"
            if p.get("tp2_hit"):
                step_status = "[bold yellow]TP2 + MOONBAG[/bold yellow]"
            elif p.get("tp1_hit"):
                step_status = "[bold green]TP1 (BE LOCKED)[/bold green]"

            pos_table.add_row(
                p["symbol"],
                p["play_type"],
                fmt_e,
                fmt_p,
                f"[{pnl_style}]{pnl_pct:+.2f}%[/{pnl_style}]",
                f"${val:.2f}",
                fmt_s,
                step_status
            )

    # 3. Signals Table
    sig_table = Table(title="[bold yellow]LATEST MEXC REVIVAL & PENNY SIGNALS[/bold yellow]", expand=True)
    sig_table.add_column("Symbol", style="bold white")
    sig_table.add_column("Setup", style="yellow")
    sig_table.add_column("Score", justify="center")
    sig_table.add_column("Price", justify="right")
    sig_table.add_column("24h Chg", justify="right")
    sig_table.add_column("24h Vol", justify="right")
    sig_table.add_column("Vol Spike", justify="center")
    sig_table.add_column("Spread", justify="right")

    if not recent_signals:
        sig_table.add_row("[dim]Scanning market...[/dim]", "--", "--", "--", "--", "--", "--", "--")
    else:
        for s in recent_signals:
            fmt_price = f"${s['price']:.7f}" if s['price'] < 0.01 else f"${s['price']:.4f}"
            chg_style = "green" if s['change_24h'] >= 0 else "red"
            sig_table.add_row(
                s["symbol"],
                s["play_type"],
                f"[bold]{s['score']}[/bold]",
                fmt_price,
                f"[{chg_style}]{s['change_24h']:+.1f}%[/{chg_style}]",
                f"${s['volume_24h']:,.0f}",
                f"{s['spike_multiple']}x",
                f"{s['spread_pct']:.2f}%"
            )

    content = Table.grid(expand=True)
    content.add_row(Panel(kpi_text, style="blue"))
    content.add_row(pos_table)
    content.add_row(sig_table)

    return Panel(content, title="[bold green]MEXC PENNY SCALPER & REVIVAL RADAR[/bold green]", border_style="cyan")


async def main():
    console.print("[bold cyan]Initializing MEXC Penny Scalper Terminal...[/bold cyan]")
    # Run initial scan
    await bot.run_scan_cycle()

    with Live(generate_dashboard(), refresh_per_second=1, console=console) as live:
        last_scan = time.time()
        while True:
            try:
                # 1. Update open positions
                position_guard.update_open_positions()

                # 2. Trigger periodic scan
                if time.time() - last_scan >= settings.SCAN_INTERVAL_SECONDS:
                    last_scan = time.time()
                    await bot.run_scan_cycle()

                live.update(generate_dashboard())
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

            await asyncio.sleep(settings.UPDATE_POSITIONS_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot stopped by user.[/yellow]")
