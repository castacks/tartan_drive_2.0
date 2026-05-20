#!/usr/bin/env python3
"""TartanDrive 2.0 — Interactive terminal dataset downloader.

Usage:
  python tartandrive_cli.py                   # interactive TUI
  python tartandrive_cli.py list              # list all datasets
  python tartandrive_cli.py list --type kitti
  python tartandrive_cli.py info <dataset> [--type bags|kitti]
  python tartandrive_cli.py download <dataset> -o /path [--type ...] [--modalities cmd controls ...]
"""

import os
import sys
import yaml
import argparse
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore import UNSIGNED
from botocore.client import Config
import questionary
from questionary import Style
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn,
    DownloadColumn, TransferSpeedColumn, TimeRemainingColumn,
)
from rich import box
from rich.text import Text
from rich.columns import Columns

# ── Constants ──────────────────────────────────────────────────────────────────

BUCKET_NAME  = "tartandrive2"
ENDPOINT_URL = "https://airlab-cloud.andrew.cmu.edu:8080/swift/v1/AUTH_ac8533a83cff4d48bc8c608ad222d330"
ASSETS_DIR   = Path(__file__).parent.parent / "assets"
FILES_YAML   = ASSETS_DIR / "files.yaml"

console = Console()

TUI_STYLE = Style([
    ("qmark",       "fg:#ff9d00 bold"),
    ("question",    "bold"),
    ("answer",      "fg:#00e5ff bold"),
    ("pointer",     "fg:#ff9d00 bold"),
    ("highlighted", "fg:#ff9d00 bold"),
    ("selected",    "fg:#00e5ff"),
    ("separator",   "fg:#555555"),
    ("instruction", "fg:#555555 italic"),
])

BANNER = """\
 ╔╦╗╔═╗╦═╗╔╦╗╔═╗╔╗╔  ╔╦╗╦═╗╦╦  ╦╔═╗  2.0
  ║ ╠═╣╠╦╝ ║ ╠═╣║║║   ║║╠╦╝║╚╗╔╝║╣   ─────────────────────────────
  ╩ ╩ ╩╩╚═ ╩ ╩ ╩╝╚╝  ═╩╝╩╚═╩ ╚╝ ╚═╝  Dataset Downloader\
"""

# ── Downloader ─────────────────────────────────────────────────────────────────

class AirLabDownloader:
    def __init__(self, bucket_name: str = BUCKET_NAME):
        self.client = boto3.client(
            "s3",
            endpoint_url=ENDPOINT_URL,
            config=Config(signature_version=UNSIGNED),
        )
        self.bucket_name = bucket_name

    def download(self, source: str, dest: str,
                 progress: Progress | None = None,
                 task_id=None) -> bool:
        try:
            resp  = self.client.get_object(Bucket=self.bucket_name, Key=source)
            total = int(resp.get("ContentLength", 0))
            path  = Path(dest)
            path.parent.mkdir(parents=True, exist_ok=True)

            if progress and task_id is not None:
                progress.update(task_id, total=total)

            with open(dest, "wb") as f:
                for chunk in resp["Body"].iter_chunks(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        if progress and task_id is not None:
                            progress.advance(task_id, len(chunk))
            return True
        except Exception as e:
            console.print(f"  [red]Error:[/red] {source.split('/')[-1]} — {e}")
            return False

    def download_silent(self, source: str, dest: str) -> bool:
        try:
            resp = self.client.get_object(Bucket=self.bucket_name, Key=source)
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp["Body"].iter_chunks(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
            return True
        except Exception:
            return False

# ── Data helpers ───────────────────────────────────────────────────────────────

def load_file_map() -> dict:
    with open(FILES_YAML) as f:
        return yaml.safe_load(f)

def list_bags(file_map: dict) -> list[str]:
    return [k.split('/')[-2] for k in file_map['bags'].keys()]

def list_kitti(file_map: dict) -> list[str]:
    return [k.split('/')[-2] for k in file_map['kitti'].keys()]

def get_bag_files(file_map: dict, directory: str) -> list[str]:
    return file_map['bags'].get(f"bags/{directory}/", {}).get('files', [])

def get_kitti_items(file_map: dict, directory: str) -> dict:
    """Returns {'__top__': [...], 'mod_name': [...], ...}."""
    entry = file_map['kitti'].get(f"kitti/all_topics/{directory}/", {})
    result: dict[str, list] = {}
    for k, v in entry.items():
        if k == 'files':
            result['__top__'] = v
        else:
            mod_name = k.rstrip('/').split('/')[-1]
            result[mod_name] = v.get('files', [])
    return result

def fetch_metadata(downloader: AirLabDownloader, directory: str) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        ok = downloader.download_silent(f"bags/{directory}/info.yaml", tmp_path)
        if not ok:
            return {}
        with open(tmp_path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

def fetch_all_metadata(downloader: AirLabDownloader,
                       datasets: list[str],
                       max_workers: int = 10) -> dict[str, dict]:
    """Download info.yaml for every dataset in parallel."""
    result: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_metadata, downloader, d): d for d in datasets}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result[name] = future.result()
            except Exception:
                result[name] = {}
    return result

def fmt_duration(value) -> str:
    if isinstance(value, (int, float)) and value > 0:
        m, s = divmod(int(value), 60)
        return f"{m}m{s:02d}s" if m else f"{s}s"
    return str(value) if value else "—"

def fmt_speed(value) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.1f} m/s"
    return str(value) if value else "—"

def local_path(source: str, dest: str, dataset_type: str) -> str:
    """Map a bucket key to a local file path under dest."""
    if dataset_type in ('bags', 'rosbags'):
        rel = source[len('bags/'):]
    else:
        rel = source[len('kitti/all_topics/'):]
    return os.path.join(dest, rel)

# ── Rich display helpers ───────────────────────────────────────────────────────

def print_header():
    console.print(Panel(
        Text(BANNER, style="bold cyan"),
        border_style="dim cyan",
        padding=(0, 2),
    ))
    console.print()

def bags_table(datasets: list[str], file_map: dict,
               all_meta: dict[str, dict] | None = None) -> Table:
    t = Table(box=box.SIMPLE_HEAD, border_style="dim", header_style="bold cyan",
              show_edge=False, title="[bold]Rosbags[/bold]", title_justify="left")
    t.add_column("Name", style="white")
    t.add_column("Bags", justify="right", style="yellow")
    t.add_column("Files", justify="right", style="dim")
    if all_meta is not None:
        t.add_column("Duration", justify="right", style="green")
        t.add_column("Top speed", justify="right", style="magenta")
    for name in datasets:
        files = get_bag_files(file_map, name)
        n_bags = sum(1 for f in files if f.endswith('.bag'))
        row = [name, str(n_bags), str(len(files))]
        if all_meta is not None:
            meta = all_meta.get(name, {})
            row += [fmt_duration(meta.get('duration')), fmt_speed(meta.get('top_speed'))]
        t.add_row(*row)
    return t

def kitti_table(datasets: list[str], file_map: dict,
                all_meta: dict[str, dict] | None = None) -> Table:
    t = Table(box=box.SIMPLE_HEAD, border_style="dim", header_style="bold cyan",
              show_edge=False, title="[bold]Kitti[/bold]", title_justify="left")
    t.add_column("Name", style="white")
    t.add_column("Modalities", justify="right", style="yellow")
    t.add_column("Top files", justify="right", style="dim")
    if all_meta is not None:
        t.add_column("Duration", justify="right", style="green")
        t.add_column("Top speed", justify="right", style="magenta")
    for name in datasets:
        items = get_kitti_items(file_map, name)
        n_mod = sum(1 for k in items if not k.startswith('__'))
        n_top = len(items.get('__top__', []))
        row = [name, str(n_mod), str(n_top)]
        if all_meta is not None:
            meta = all_meta.get(name, {})
            row += [fmt_duration(meta.get('duration')), fmt_speed(meta.get('top_speed'))]
        t.add_row(*row)
    return t

def metadata_panel(meta: dict) -> Panel:
    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column("key",   style="bold dim", no_wrap=True)
    t.add_column("value", style="white")
    for k, v in meta.items():
        t.add_row(str(k), str(v))
    return Panel(t, title="[bold]Metadata[/bold]", border_style="dim cyan", padding=(0, 1))

def summary_panel(files: list[str], dest: str) -> Panel:
    t = Table(box=None, show_header=False, padding=(0, 1))
    t.add_column("file", style="dim")
    shown = files[:15]
    for f in shown:
        t.add_row(f.split('/')[-1])
    if len(files) > 15:
        t.add_row(f"[dim]… and {len(files) - 15} more[/dim]")
    body = Text.from_markup(
        f"\n  [bold]Files :[/bold] [yellow]{len(files)}[/yellow]\n"
        f"  [bold]Dest  :[/bold] [cyan]{dest}[/cyan]\n"
    )
    return Panel(
        Text.assemble(body),
        title="[bold]Download summary[/bold]",
        border_style="yellow",
        padding=(0, 1),
    )

# ── Download runner ────────────────────────────────────────────────────────────

def run_download(downloader: AirLabDownloader, files: list[str],
                 dest: str, dataset_type: str):
    ok = failed = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:
        overall = progress.add_task("[cyan]Overall", total=len(files))
        for source in files:
            name = source.split('/')[-1]
            task = progress.add_task(f"[green]{name}", total=None)
            dest_path = local_path(source, dest, dataset_type)
            if downloader.download(source, dest_path, progress, task):
                ok += 1
            else:
                failed += 1
            progress.update(overall, advance=1)
            progress.remove_task(task)

    console.print()
    if failed == 0:
        console.print(f"[bold green]Done![/bold green]  {ok} files → [cyan]{dest}[/cyan]")
    else:
        console.print(
            f"[yellow]Finished with errors:[/yellow] "
            f"{ok} ok, [red]{failed} failed[/red] → [cyan]{dest}[/cyan]"
        )

# ── Interactive TUI ────────────────────────────────────────────────────────────

def interactive(file_map: dict, downloader: AirLabDownloader):
    print_header()

    # Persistent state across the loop
    dtype:       str | None       = None
    datasets:    list[str]        = []
    all_meta:    dict[str, dict]  = {}
    meta_for:    str | None       = None   # dtype for which all_meta was fetched
    chosen:      str | None       = None
    files_to_dl: list[str]        = []
    dest:        str | None       = None

    state = "TYPE"

    while True:

        # ── TYPE ──────────────────────────────────────────────────────────────
        if state == "TYPE":
            dtype = questionary.select(
                "Dataset type?",
                choices=["rosbags", "kitti"],
                style=TUI_STYLE,
            ).ask()
            if dtype is None:       # Ctrl+C → quit
                return
            chosen = files_to_dl = dest = None
            state = "DATASET"

        # ── DATASET ───────────────────────────────────────────────────────────
        elif state == "DATASET":
            console.print()
            if dtype == "rosbags":
                datasets = list_bags(file_map)
                if meta_for != "rosbags":
                    with console.status(
                        f"[dim]Loading metadata for {len(datasets)} datasets…[/dim]"
                    ):
                        all_meta = fetch_all_metadata(downloader, datasets)
                    meta_for = "rosbags"
                console.print(bags_table(datasets, file_map, all_meta))
                console.print()

                col_w   = max(len(d) for d in datasets)
                choices = [questionary.Choice("← Back", value="__back__")] + [
                    questionary.Choice(
                        title=(
                            f"{d:<{col_w}}  "
                            f"{fmt_duration(all_meta.get(d, {}).get('duration')):>8}  "
                            f"{fmt_speed(all_meta.get(d, {}).get('top_speed')):>10}"
                        ),
                        value=d,
                    )
                    for d in datasets
                ]
                chosen = questionary.select(
                    "Select dataset  (↑↓ · Enter = confirm):",
                    choices=choices,
                    style=TUI_STYLE,
                ).ask()

            else:   # kitti — metadata lives in the bags/ folder, same names
                datasets = list_kitti(file_map)
                if meta_for != "kitti":
                    with console.status(
                        f"[dim]Loading metadata for {len(datasets)} datasets…[/dim]"
                    ):
                        all_meta = fetch_all_metadata(downloader, datasets)
                    meta_for = "kitti"
                console.print(kitti_table(datasets, file_map, all_meta))
                console.print()

                choices = [questionary.Choice("← Back", value="__back__")] + [
                    questionary.Choice(
                        title=(
                            f"{d}  "
                            f"[{fmt_duration(all_meta.get(d, {}).get('duration'))}"
                            f" · {fmt_speed(all_meta.get(d, {}).get('top_speed'))}]"
                        ),
                        value=d,
                    )
                    for d in datasets
                ]
                chosen = questionary.select(
                    "Select dataset  (↑↓ · Enter = confirm):",
                    choices=choices,
                    style=TUI_STYLE,
                ).ask()

            if chosen is None or chosen == "__back__":
                state = "TYPE"
                continue

            # Metadata panel (free for rosbags — already loaded)
            console.print()
            if dtype == "rosbags":
                meta = all_meta.get(chosen, {})
                if meta:
                    console.print(metadata_panel(meta))
                    console.print()

            files_to_dl = (
                get_bag_files(file_map, chosen)
                if dtype == "rosbags"
                else []
            )
            state = "MODALITIES" if dtype == "kitti" else "DEST"

        # ── MODALITIES (kitti only) ────────────────────────────────────────────
        elif state == "MODALITIES":
            items = get_kitti_items(file_map, chosen)

            # Top-level files (.tar, .log) — keyed by stem for display
            top_items: dict[str, list[str]] = {}
            for f in items.get('__top__', []):
                stem = Path(f).stem   # depth_left, full_cloud, …
                top_items[stem] = [f]

            # Sub-directory modalities (cmd, controls, …)
            mod_items: dict[str, list[str]] = {
                k: v for k, v in items.items() if not k.startswith('__')
            }

            all_items = {**top_items, **mod_items}

            mt = Table(box=box.SIMPLE_HEAD, border_style="dim", header_style="bold cyan",
                       show_edge=False)
            mt.add_column("Name",  style="white")
            mt.add_column("Type",  style="dim")
            mt.add_column("Files", justify="right", style="dim")
            for name, files in top_items.items():
                mt.add_row(name, "tar/file", str(len(files)))
            for name, files in mod_items.items():
                mt.add_row(name, "modality", str(len(files)))
            console.print(Panel(mt, title="[bold]Available data[/bold]",
                                border_style="dim cyan"))
            console.print()

            selected = questionary.checkbox(
                "Select items  (Space = toggle · a = all · Enter = confirm · Ctrl+C = back):",
                choices=list(all_items.keys()),
                style=TUI_STYLE,
            ).ask()

            if selected is None:    # Ctrl+C → back to dataset
                state = "DATASET"
                continue

            if not selected:
                console.print("[yellow]No item selected — pick at least one.[/yellow]\n")
                continue            # stay in MODALITIES

            files_to_dl = [f for m in selected for f in all_items.get(m, [])]
            state = "DEST"

        # ── DEST ──────────────────────────────────────────────────────────────
        elif state == "DEST":
            console.print()
            default_dest = str(Path.home() / "tartandrive_data" / chosen)
            dest = questionary.path(
                "Destination folder  (Tab = autocomplete · Ctrl+C = back):",
                default=dest or default_dest,
                only_directories=True,
                style=TUI_STYLE,
            ).ask()

            if dest is None:        # Ctrl+C → back
                state = "MODALITIES" if dtype == "kitti" else "DATASET"
                continue
            dest = os.path.expanduser(dest)
            state = "CONFIRM"

        # ── CONFIRM ───────────────────────────────────────────────────────────
        elif state == "CONFIRM":
            console.print()
            console.print(summary_panel(files_to_dl, dest))
            console.print()

            action = questionary.select(
                "Ready?",
                choices=[
                    questionary.Choice("✓  Start download",      value="go"),
                    questionary.Choice("←  Change destination",  value="dest"),
                    questionary.Choice(
                        "←  Change modalities" if dtype == "kitti" else "←  Change dataset",
                        value="back",
                    ),
                    questionary.Choice("✕  Cancel",              value="cancel"),
                ],
                style=TUI_STYLE,
            ).ask()

            if action is None or action == "cancel":
                console.print("[dim]Cancelled.[/dim]")
                return
            elif action == "dest":
                state = "DEST"
            elif action == "back":
                state = "MODALITIES" if dtype == "kitti" else "DATASET"
            else:
                console.print()
                run_download(downloader, files_to_dl, dest, dtype)
                return

# ── CLI sub-commands ───────────────────────────────────────────────────────────

def cmd_list(file_map: dict, dtype: str | None):
    if dtype in (None, 'bags', 'rosbags'):
        console.print(bags_table(list_bags(file_map), file_map))
        console.print()
    if dtype in (None, 'kitti'):
        console.print(kitti_table(list_kitti(file_map), file_map))

def cmd_info(file_map: dict, downloader: AirLabDownloader,
             dataset: str, dtype: str):
    if dtype in ('bags', 'rosbags'):
        with console.status("[dim]Fetching metadata…[/dim]"):
            meta = fetch_metadata(downloader, dataset)
        if meta:
            console.print(metadata_panel(meta))
        files = get_bag_files(file_map, dataset)
        t = Table(box=box.SIMPLE, border_style="dim", show_header=False)
        t.add_column("file", style="dim")
        for f in files:
            t.add_row(f.split('/')[-1])
        console.print(Panel(t, title=f"[bold]{dataset}[/bold] — {len(files)} files",
                            border_style="dim cyan"))
    else:
        items = get_kitti_items(file_map, dataset)
        t = Table(box=box.SIMPLE_HEAD, border_style="dim", header_style="bold cyan")
        t.add_column("Modality")
        t.add_column("Files", justify="right", style="yellow")
        for k, v in items.items():
            if not k.startswith('__'):
                t.add_row(k, str(len(v)))
        top = len(items.get('__top__', []))
        t.add_row("[dim]top-level files[/dim]", str(top))
        console.print(Panel(t, title=f"[bold]{dataset}[/bold]", border_style="dim cyan"))

def cmd_download(file_map: dict, downloader: AirLabDownloader,
                 dataset: str, dtype: str, output: str,
                 modalities: list[str] | None):
    if dtype in ('bags', 'rosbags'):
        files_to_dl = get_bag_files(file_map, dataset)
    else:
        items = get_kitti_items(file_map, dataset)
        # Build a flat name→files map (top-level files by stem + sub-dirs)
        all_items: dict[str, list[str]] = {
            Path(f).stem: [f] for f in items.get('__top__', [])
        }
        all_items.update({k: v for k, v in items.items() if not k.startswith('__')})
        if modalities:
            files_to_dl = []
            for m in modalities:
                if m not in all_items:
                    console.print(f"[yellow]Warning:[/yellow] unknown item '{m}', skipping")
                    continue
                files_to_dl.extend(all_items[m])
        else:
            files_to_dl = [f for v in all_items.values() for f in v]

    if not files_to_dl:
        console.print("[yellow]No files to download.[/yellow]")
        return

    dest = os.path.expanduser(output)
    console.print(summary_panel(files_to_dl, dest))
    console.print()
    run_download(downloader, files_to_dl, dest, dtype)

# ── Entry point ────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tartandrive",
        description="TartanDrive 2.0 — terminal dataset downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  tartandrive_cli.py                                  # interactive TUI
  tartandrive_cli.py list
  tartandrive_cli.py list --type kitti
  tartandrive_cli.py info 2023-10-26-14-42-35_turnpike_afternoon_fall
  tartandrive_cli.py info 2023-10-26-14-42-35_turnpike_afternoon_fall --type kitti
  tartandrive_cli.py download 2023-10-26-14-42-35_turnpike_afternoon_fall -o ~/data
  tartandrive_cli.py download 2023-10-26-14-42-35_turnpike_afternoon_fall --type kitti \\
      -o ~/data --modalities cmd controls gps_odom
""",
    )
    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list", help="List available datasets")
    p_list.add_argument("--type", dest="dtype",
                        choices=["bags", "rosbags", "kitti"],
                        help="Filter by type")

    p_info = sub.add_parser("info", help="Show dataset details and file list")
    p_info.add_argument("dataset")
    p_info.add_argument("--type", dest="dtype",
                        choices=["bags", "rosbags", "kitti"], default="bags")

    p_dl = sub.add_parser("download", help="Download a dataset")
    p_dl.add_argument("dataset")
    p_dl.add_argument("--type", dest="dtype",
                      choices=["bags", "rosbags", "kitti"], default="bags")
    p_dl.add_argument("-o", "--output", required=True,
                      help="Destination folder")
    p_dl.add_argument("-m", "--modalities", nargs="+",
                      metavar="MOD",
                      help="(kitti only) Modalities to download")

    return parser

def main():
    parser = build_parser()
    args   = parser.parse_args()

    file_map   = load_file_map()
    downloader = AirLabDownloader()

    if args.command == "list":
        cmd_list(file_map, args.dtype)
    elif args.command == "info":
        cmd_info(file_map, downloader, args.dataset, args.dtype)
    elif args.command == "download":
        cmd_download(file_map, downloader, args.dataset, args.dtype,
                     args.output, args.modalities)
    else:
        interactive(file_map, downloader)

if __name__ == "__main__":
    main()
