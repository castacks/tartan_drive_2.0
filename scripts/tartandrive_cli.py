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

CONFIG_PATH = Path.home() / ".tartandrive" / "config.yaml"
MAX_WORKERS = 32
DEFAULT_CONFIG: dict = {
    "download": {
        "workers":      8,
        "chunk_size_mb": 1,
    }
}

# ── Config helpers ─────────────────────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False)
        return _deep_merge({}, DEFAULT_CONFIG)
    with open(CONFIG_PATH) as f:
        on_disk = yaml.safe_load(f) or {}
    return _deep_merge(DEFAULT_CONFIG, on_disk)

def save_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)

def _get_nested(cfg: dict, key: str):
    val = cfg
    for part in key.split("."):
        if not isinstance(val, dict) or part not in val:
            return None
        val = val[part]
    return val

def _set_nested(cfg: dict, key: str, raw: str):
    for cast in (int, float):
        try:
            value = cast(raw)
            break
        except ValueError:
            pass
    else:
        value = raw
    parts = key.split(".")
    d = cfg
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value
    return value

def _flatten(d: dict, prefix: str = ""):
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            yield from _flatten(v, full)
        else:
            yield full, v

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
    def __init__(self, bucket_name: str = BUCKET_NAME,
                 workers: int = DEFAULT_CONFIG["download"]["workers"],
                 chunk_size_mb: int = DEFAULT_CONFIG["download"]["chunk_size_mb"]):
        self.client = boto3.client(
            "s3",
            endpoint_url=ENDPOINT_URL,
            config=Config(
                signature_version=UNSIGNED,
                max_pool_connections=max(workers, 10),
            ),
        )
        self.bucket_name = bucket_name
        self.chunk_size  = chunk_size_mb * 1024 * 1024

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
                for chunk in resp["Body"].iter_chunks(chunk_size=self.chunk_size):
                    if chunk:
                        f.write(chunk)
                        if progress and task_id is not None:
                            progress.advance(task_id, len(chunk))
            return True
        except Exception as e:
            console.print(f"  [red]Error:[/red] {source.split('/')[-1]} — {e}")
            return False

    def is_complete(self, source: str, dest: str) -> bool:
        local = Path(dest)
        if not local.exists():
            return False
        try:
            head = self.client.head_object(Bucket=self.bucket_name, Key=source)
            return local.stat().st_size == int(head["ContentLength"])
        except Exception:
            return False

    def download_silent(self, source: str, dest: str) -> bool:
        try:
            resp = self.client.get_object(Bucket=self.bucket_name, Key=source)
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp["Body"].iter_chunks(chunk_size=self.chunk_size):
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

def get_all_kitti_modalities(file_map: dict) -> tuple[list[str], list[str]]:
    """Return (sorted modalities, sorted top-level file stems) across all kitti datasets."""
    all_mods: set[str] = set()
    all_tops: set[str] = set()
    for entry in file_map['kitti'].values():
        for k, v in entry.items():
            if k == 'files':
                for f in v:
                    all_tops.add(Path(f).stem)
            else:
                all_mods.add(k.rstrip('/').split('/')[-1])
    return sorted(all_mods), sorted(all_tops)

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
                 dest: str, dataset_type: str, workers: int = DEFAULT_CONFIG["download"]["workers"]):
    ok = failed = skipped = 0
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

        def _dl(source: str) -> str:
            dest_path = local_path(source, dest, dataset_type)
            name = source.split('/')[-1]
            if downloader.is_complete(source, dest_path):
                progress.console.print(f"  [dim]↩ skip  {name}[/dim]")
                return "skip"
            task = progress.add_task(f"[green]{name}", total=None)
            result = downloader.download(source, dest_path, progress, task)
            progress.remove_task(task)
            return "ok" if result else "fail"

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_dl, src): src for src in files}
            for future in as_completed(futures):
                status = future.result()
                if status == "ok":
                    ok += 1
                elif status == "skip":
                    skipped += 1
                else:
                    failed += 1
                progress.advance(overall, 1)

    console.print()
    parts = []
    if ok:
        parts.append(f"[green]{ok} downloaded[/green]")
    if skipped:
        parts.append(f"[dim]{skipped} skipped[/dim]")
    if failed:
        parts.append(f"[red]{failed} failed[/red]")
    status_line = ", ".join(parts)
    prefix = "[bold green]Done![/bold green]" if not failed else "[yellow]Finished with errors:[/yellow]"
    console.print(f"{prefix}  {status_line}  → [cyan]{dest}[/cyan]")

# ── Interactive TUI ────────────────────────────────────────────────────────────

def interactive(file_map: dict, downloader: AirLabDownloader, workers: int = DEFAULT_CONFIG["download"]["workers"]):
    print_header()

    # Persistent state across the loop
    dtype:         str | None       = None
    datasets:      list[str]        = []
    all_meta:      dict[str, dict]  = {}
    meta_for:      str | None       = None   # dtype for which all_meta was fetched
    chosen:        str | None       = None
    files_to_dl:   list[str]        = []
    dest:          str | None       = None
    bm_modalities: list[str]        = []     # kitti_bm: selected channels/files
    bm_datasets:   list[str]        = []     # kitti_bm: selected experiments

    state = "TYPE"

    while True:

        # ── TYPE ──────────────────────────────────────────────────────────────
        if state == "TYPE":
            dtype = questionary.select(
                "Dataset type?",
                choices=[
                    questionary.Choice("rosbags",               value="rosbags"),
                    questionary.Choice("kitti — by experiment", value="kitti"),
                    questionary.Choice("kitti — by modality",   value="kitti_bm"),
                    questionary.Separator(),
                    questionary.Choice("⚙  Advanced settings",  value="settings"),
                ],
                style=TUI_STYLE,
            ).ask()
            if dtype is None:
                return
            if dtype == "settings":
                state = "SETTINGS"
                continue
            chosen = files_to_dl = dest = None
            bm_modalities = []
            bm_datasets = []
            state = "MODALITY_PICK" if dtype == "kitti_bm" else "DATASET"

        # ── SETTINGS ──────────────────────────────────────────────────────────
        elif state == "SETTINGS":
            cfg     = load_config()
            dl_cfg  = cfg["download"]
            console.print()
            setting_choices = [
                questionary.Choice(
                    f"workers        = [bold]{workers}[/bold]  (parallel threads, 1–{MAX_WORKERS})",
                    value="workers",
                ),
                questionary.Choice(
                    f"chunk_size_mb  = [bold]{dl_cfg['chunk_size_mb']}[/bold]  (MB per read chunk)",
                    value="chunk_size_mb",
                ),
                questionary.Separator(),
                questionary.Choice("← Back", value="back"),
            ]
            key = questionary.select(
                "Advanced settings  (Enter to edit · Ctrl+C = back):",
                choices=setting_choices,
                style=TUI_STYLE,
            ).ask()
            if key is None or key == "back":
                state = "TYPE"
                continue

            validators = {
                "workers": lambda x: (
                    (x.isdigit() and 1 <= int(x) <= MAX_WORKERS)
                    or f"Enter a number between 1 and {MAX_WORKERS}"
                ),
                "chunk_size_mb": lambda x: (
                    (x.isdigit() and int(x) >= 1)
                    or "Enter a number >= 1"
                ),
            }
            new_val = questionary.text(
                f"{key}  (current: {dl_cfg[key]}):",
                default=str(dl_cfg[key]),
                validate=validators[key],
                style=TUI_STYLE,
            ).ask()
            if new_val is not None:
                _set_nested(cfg, f"download.{key}", new_val)
                save_config(cfg)
                workers    = cfg["download"]["workers"]
                downloader = AirLabDownloader(
                    workers=workers,
                    chunk_size_mb=cfg["download"]["chunk_size_mb"],
                )
                console.print(
                    f"  [green]Saved[/green] download.{key} = "
                    f"[cyan]{cfg['download'][key]}[/cyan]  "
                    f"[dim](→ {CONFIG_PATH})[/dim]\n"
                )

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

                chosen = questionary.autocomplete(
                    "Search dataset  (type to filter · Tab = complete · Enter = confirm · Ctrl+C = back):",
                    choices=datasets,
                    style=TUI_STYLE,
                    validate=lambda x: x in datasets or "Please select a valid dataset name",
                    match_middle=True,
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

                chosen = questionary.autocomplete(
                    "Search dataset  (type to filter · Tab = complete · Enter = confirm · Ctrl+C = back):",
                    choices=datasets,
                    style=TUI_STYLE,
                    validate=lambda x: x in datasets or "Please select a valid dataset name",
                    match_middle=True,
                ).ask()

            if chosen is None:
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

        # ── MODALITY_PICK (kitti_bm: pick channels first) ─────────────────────
        elif state == "MODALITY_PICK":
            all_mods, all_tops = get_all_kitti_modalities(file_map)

            mt = Table(box=box.SIMPLE_HEAD, border_style="dim", header_style="bold cyan",
                       show_edge=False, title="[bold]Available channels & files[/bold]",
                       title_justify="left")
            mt.add_column("Name",  style="white")
            mt.add_column("Type",  style="dim")
            for m in all_mods:
                mt.add_row(m, "modality")
            for s in all_tops:
                mt.add_row(s, "tar / file")
            console.print(mt)
            console.print()

            mod_choices = [
                questionary.Choice(item, checked=(item in bm_modalities))
                for item in all_mods + all_tops
            ]
            selected = questionary.checkbox(
                "Select channels/files  (Space = toggle · a = all · Enter = confirm · Ctrl+C = back):",
                choices=mod_choices,
                style=TUI_STYLE,
            ).ask()

            if selected is None:
                state = "TYPE"
                continue
            if not selected:
                console.print("[yellow]No channel selected — pick at least one.[/yellow]\n")
                continue

            bm_modalities = selected
            state = "DATASET_MULTI"

        # ── DATASET_MULTI (kitti_bm: pick experiments) ─────────────────────────
        elif state == "DATASET_MULTI":
            console.print()
            datasets = list_kitti(file_map)
            if meta_for != "kitti":
                with console.status(
                    f"[dim]Loading metadata for {len(datasets)} datasets…[/dim]"
                ):
                    all_meta = fetch_all_metadata(downloader, datasets)
                meta_for = "kitti"
            console.print(kitti_table(datasets, file_map, all_meta))
            console.print()

            ds_choices = [
                questionary.Choice(d, checked=(d in bm_datasets))
                for d in datasets
            ]
            selected_ds = questionary.checkbox(
                "Select experiments  (Space = toggle · a = all · Enter = confirm · Ctrl+C = back):",
                choices=ds_choices,
                style=TUI_STYLE,
            ).ask()

            if selected_ds is None:
                state = "MODALITY_PICK"
                continue
            if not selected_ds:
                console.print("[yellow]No experiment selected — pick at least one.[/yellow]\n")
                continue

            bm_datasets = selected_ds

            # Build flat file list from selected datasets × selected modalities
            files_to_dl = []
            for ds in bm_datasets:
                items = get_kitti_items(file_map, ds)
                ds_items: dict[str, list[str]] = {
                    Path(f).stem: [f] for f in items.get('__top__', [])
                }
                ds_items.update({k: v for k, v in items.items() if not k.startswith('__')})
                for mod in bm_modalities:
                    files_to_dl.extend(ds_items.get(mod, []))

            state = "DEST"

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
            default_dest = str(Path.home() / "tartandrive_data" / (chosen or "kitti_selection"))
            dest = questionary.path(
                "Destination folder  (Tab = autocomplete · Ctrl+C = back):",
                default=dest or default_dest,
                only_directories=True,
                style=TUI_STYLE,
            ).ask()

            if dest is None:        # Ctrl+C → back
                if dtype == "kitti_bm":
                    state = "DATASET_MULTI"
                elif dtype == "kitti":
                    state = "MODALITIES"
                else:
                    state = "DATASET"
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
                        "←  Change experiments" if dtype == "kitti_bm"
                        else "←  Change modalities" if dtype == "kitti"
                        else "←  Change dataset",
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
                if dtype == "kitti_bm":
                    state = "DATASET_MULTI"
                elif dtype == "kitti":
                    state = "MODALITIES"
                else:
                    state = "DATASET"
            else:
                console.print()
                run_download(downloader, files_to_dl, dest,
                            "kitti" if dtype == "kitti_bm" else dtype, workers)
                return

# ── CLI sub-commands ───────────────────────────────────────────────────────────

def cmd_config(action: str, key: str | None, raw_value: str | None):
    cfg = load_config()
    if action == "show" or (action is None):
        t = Table(box=box.SIMPLE_HEAD, border_style="dim", header_style="bold cyan",
                  show_edge=False)
        t.add_column("Key",     style="white")
        t.add_column("Value",   style="cyan")
        t.add_column("Default", style="dim")
        for k, v in _flatten(cfg):
            default = _get_nested(DEFAULT_CONFIG, k)
            marker = " [yellow]*[/yellow]" if v != default else ""
            t.add_row(k, f"{v}{marker}", str(default))
        console.print(Panel(t,
            title=f"[bold]Config[/bold]  [dim]{CONFIG_PATH}[/dim]",
            border_style="dim cyan"))

    elif action == "get":
        val = _get_nested(cfg, key)
        if val is None:
            console.print(f"[red]Unknown key:[/red] {key}")
            sys.exit(1)
        console.print(f"{key} = [cyan]{val}[/cyan]")

    elif action == "set":
        value = _set_nested(cfg, key, raw_value)
        if key == "download.workers":
            value = max(1, min(int(value), MAX_WORKERS))
            _set_nested(cfg, key, str(value))
        save_config(cfg)
        console.print(f"[green]Set[/green] {key} = [cyan]{value}[/cyan]  "
                      f"[dim](saved to {CONFIG_PATH})[/dim]")

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
                 modalities: list[str] | None, workers: int):
    if dtype in ('bags', 'rosbags'):
        files_to_dl = get_bag_files(file_map, dataset)
    else:
        items = get_kitti_items(file_map, dataset)
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
    run_download(downloader, files_to_dl, dest, dtype, workers)

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
    p_dl.add_argument("--workers", type=int, default=None, metavar="N",
                      help=f"Parallel download threads 1–{MAX_WORKERS} (overrides config)")

    p_cfg = sub.add_parser("config", help="Read or write config values")
    cfg_sub = p_cfg.add_subparsers(dest="config_action")
    cfg_sub.add_parser("show", help="Show all config values")
    p_cfg_get = cfg_sub.add_parser("get", help="Get a config value")
    p_cfg_get.add_argument("key", help="Dotted key, e.g. download.workers")
    p_cfg_set = cfg_sub.add_parser("set", help="Set a config value")
    p_cfg_set.add_argument("key",   help="Dotted key, e.g. download.workers")
    p_cfg_set.add_argument("value", help="New value")

    return parser

def main():
    parser = build_parser()
    args   = parser.parse_args()

    if args.command == "config":
        key = getattr(args, "key",   None)
        val = getattr(args, "value", None)
        cmd_config(args.config_action, key, val)
        return

    cfg      = load_config()
    workers  = cfg["download"]["workers"]
    chunk_mb = cfg["download"]["chunk_size_mb"]

    if args.command == "download" and args.workers is not None:
        workers = max(1, min(args.workers, MAX_WORKERS))

    file_map   = load_file_map()
    downloader = AirLabDownloader(workers=workers, chunk_size_mb=chunk_mb)

    if args.command == "list":
        cmd_list(file_map, args.dtype)
    elif args.command == "info":
        cmd_info(file_map, downloader, args.dataset, args.dtype)
    elif args.command == "download":
        cmd_download(file_map, downloader, args.dataset, args.dtype,
                     args.output, args.modalities, workers)
    else:
        interactive(file_map, downloader, workers)

if __name__ == "__main__":
    main()
