"""Interactive CLI for cache management."""

from __future__ import annotations

import sys

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from cachou.providers import CacheInfo, CacheProvider, format_size, get_all_providers

console = Console()

BANNER = r"""
  ██████╗ █████╗  ██████╗██╗  ██╗ ██████╗ ██╗   ██╗
 ██╔════╝██╔══██╗██╔════╝██║  ██║██╔═══██╗██║   ██║
 ██║     ███████║██║     ███████║██║   ██║██║   ██║
 ██║     ██╔══██║██║     ██╔══██║██║   ██║██║   ██║
 ╚██████╗██║  ██║╚██████╗██║  ██║╚██████╔╝╚██████╔╝
  ╚═════╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝ ╚═════╝  ╚═════╝
"""


def show_banner() -> None:
    """Display the application banner."""
    console.print(Text(BANNER, style="bold cyan"))
    console.print(
        Panel("Dev Cache Manager", style="bold magenta", expand=False),
    )
    console.print()


def gather_cache_info(providers: list[CacheProvider]) -> list[tuple[CacheProvider, CacheInfo]]:
    """Collect cache information from all providers."""
    results: list[tuple[CacheProvider, CacheInfo]] = []
    for provider in providers:
        info = provider.get_cache_info()
        results.append((provider, info))
    return results


def show_summary(infos: list[tuple[CacheProvider, CacheInfo]]) -> None:
    """Display a summary table of all caches."""
    table = Table(title="Cache Summary", title_style="bold yellow", show_lines=True)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Provider", style="cyan", min_width=12)
    table.add_column("Path", style="dim")
    table.add_column("Size", style="bold green", justify="right")
    table.add_column("Entries", justify="right")
    table.add_column("Status", justify="center")

    for idx, (_, info) in enumerate(infos, 1):
        status = "[green]✓[/green]" if info.available and info.total_size > 0 else "[dim]—[/dim]"
        path_str = str(info.path) if info.path else "—"
        table.add_row(
            str(idx),
            info.name,
            path_str,
            format_size(info.total_size) if info.available else "N/A",
            str(len(info.entries)) if info.available else "—",
            status,
        )

    total = sum(info.total_size for _, info in infos if info.available)
    table.add_row("", "[bold]Total[/bold]", "", f"[bold]{format_size(total)}[/bold]", "", "")
    console.print(table)
    console.print()


def show_details(info: CacheInfo) -> None:
    """Show detailed breakdown for a single cache."""
    if not info.available:
        console.print(f"  [dim]{info.name} cache not found.[/dim]")
        return
    if not info.entries:
        console.print(f"  [dim]{info.name} cache is empty.[/dim]")
        return

    table = Table(title=f"{info.name} Cache Details", title_style="bold cyan", show_lines=True)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Description", style="white")
    table.add_column("Path", style="dim")
    table.add_column("Size", style="bold green", justify="right")

    for idx, entry in enumerate(info.entries, 1):
        table.add_row(str(idx), entry.description, str(entry.path), format_size(entry.size))

    console.print(table)
    console.print()


def delete_single_cache(provider: CacheProvider, info: CacheInfo) -> None:
    """Interactively delete entries from a single cache."""
    if not info.available or not info.entries:
        console.print(f"  [dim]Nothing to remove for {info.name}.[/dim]")
        return

    show_details(info)

    if len(info.entries) > 1:
        choice = Prompt.ask(
            f"  Delete which entries? [bold]a[/bold]ll, entry numbers (comma-separated), or [bold]n[/bold]one",
            default="n",
        )
        if choice.lower() in ("n", "none"):
            return
        if choice.lower() in ("a", "all"):
            entries_to_delete = None  # means all
        else:
            try:
                indices = [int(x.strip()) - 1 for x in choice.split(",")]
                entries_to_delete = [info.entries[i] for i in indices if 0 <= i < len(info.entries)]
            except (ValueError, IndexError):
                console.print("  [red]Invalid selection.[/red]")
                return
    else:
        if not Confirm.ask(f"  Delete {info.entries[0].description}?", default=False):
            return
        entries_to_delete = info.entries

    if not Confirm.ask("  [bold yellow]Confirm deletion?[/bold yellow]", default=False):
        console.print("  [dim]Cancelled.[/dim]")
        return

    freed = provider.clear(entries_to_delete)
    console.print(f"  [bold green]✓ Freed {format_size(freed)}[/bold green]")


def delete_all_caches(infos: list[tuple[CacheProvider, CacheInfo]]) -> None:
    """Delete all caches after confirmation."""
    available = [(p, i) for p, i in infos if i.available and i.entries]
    if not available:
        console.print("[dim]No caches to clean.[/dim]")
        return

    total = sum(i.total_size for _, i in available)
    console.print(f"\n  About to remove [bold]{format_size(total)}[/bold] across {len(available)} caches.")
    if not Confirm.ask("  [bold red]Are you sure?[/bold red]", default=False):
        console.print("  [dim]Cancelled.[/dim]")
        return

    freed_total = 0
    for provider, info in available:
        freed = provider.clear()
        freed_total += freed
        console.print(f"  [green]✓[/green] {info.name}: freed {format_size(freed)}")

    console.print(f"\n  [bold green]Total freed: {format_size(freed_total)}[/bold green]")


def interactive_loop(providers: list[CacheProvider] | None = None) -> None:
    """Main interactive menu loop."""
    if providers is None:
        providers = get_all_providers()

    show_banner()

    while True:
        infos = gather_cache_info(providers)
        show_summary(infos)

        console.print("[bold]Actions:[/bold]")
        console.print("  [cyan]1[/cyan] Show details for a cache")
        console.print("  [cyan]2[/cyan] Clean a single cache")
        console.print("  [cyan]3[/cyan] Clean all caches")
        console.print("  [cyan]q[/cyan] Quit")
        console.print()

        choice = Prompt.ask("Select an action", choices=["1", "2", "3", "q"], default="q")

        if choice == "q":
            console.print("[bold cyan]Bye![/bold cyan] 👋")
            break
        elif choice == "1":
            idx = Prompt.ask(
                "  Cache number",
                choices=[str(i) for i in range(1, len(infos) + 1)],
            )
            _, info = infos[int(idx) - 1]
            show_details(info)
        elif choice == "2":
            idx = Prompt.ask(
                "  Cache number",
                choices=[str(i) for i in range(1, len(infos) + 1)],
            )
            provider, info = infos[int(idx) - 1]
            delete_single_cache(provider, info)
        elif choice == "3":
            delete_all_caches(infos)

        console.print()


def main() -> None:
    """Entry point for the CLI."""
    try:
        interactive_loop()
    except KeyboardInterrupt:
        console.print("\n[bold cyan]Interrupted. Bye![/bold cyan] 👋")
        sys.exit(0)


if __name__ == "__main__":
    main()
