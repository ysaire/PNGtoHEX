"""
crampp — Binary Asset to C++ Header Converter
==============================================

Converts image and font files into embeddable C++ header files (.hpp).

Supported:
  Images : PNG, JPG, JPEG, BMP, ICO, GIF
  Fonts  : TTF, OTF, WOFF, WOFF2

Output modes:
  1. Single merged .hpp
  2. One .hpp per asset

New in v1.1:
  --watch      Auto-reconverts on file change
  --recursive  Scan directories recursively
  --output     Set output path from CLI
  --namespace  Wrap output in a C++ namespace
  --constexpr  Use constexpr instead of const
  --manifest   Append a runtime asset manifest array
  --dry-run    Preview changes without writing

Usage:
  python convert.py [files or dirs ...]
  python convert.py --watch ./icons --output icons_bin.hpp
  python convert.py --recursive ./assets --namespace MyApp
  python convert.py --help

Version : 1.1.0
License : MIT
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Optional

__version__ = "1.1.0"

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

IMG_EXTS:  frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".ico", ".gif"})
FONT_EXTS: frozenset[str] = frozenset({".ttf", ".otf", ".woff", ".woff2"})
ALL_EXTS:  frozenset[str] = IMG_EXTS | FONT_EXTS

CACHE_FILENAME  = ".crampp_cache.json"
HEX_COLS        = 12
WATCH_INTERVAL  = 1.5   # seconds between watch polls

# ─────────────────────────────────────────────────────────────────────────────
# ANSI COLORS  —  amber theme #e1a140
# ─────────────────────────────────────────────────────────────────────────────

class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    AMBER   = "\033[38;2;225;161;64m"
    AMBER_L = "\033[38;2;255;190;90m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    MAGENTA = "\033[95m"
    BLUE    = "\033[38;2;100;180;255m"

# ─────────────────────────────────────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FileInfo:
    path:     str
    abs_path: str
    filename: str
    ext:      str
    var_name: str
    is_font:  bool

@dataclass
class ConversionResult:
    filename:  str
    var_name:  str
    size:      int
    ok:        bool
    out_path:  str
    status:    str   # 'new' | 'modified' | 'unchanged' | 'error' | 'dry-run'
    error_msg: str = ""

@dataclass
class ConversionPlan:
    split_mode: bool
    merge_file: str  = ""
    namespace:  str  = ""
    constexpr:  bool = False
    manifest:   bool = False
    dry_run:    bool = False

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _ansi_len(s: str) -> int:
    return len(re.sub(r"\033\[[0-9;]*m", "", s))

def pad_right(s: str, w: int) -> str:
    return s + " " * max(0, w - _ansi_len(s))

def pad_left(s: str, w: int) -> str:
    return " " * max(0, w - _ansi_len(s)) + s

def _exit_error(msg: str) -> None:
    separator()
    print(f"\n  {C.RED}{msg}{C.RESET}\n")
    separator()
    input(f"\n  {C.DIM}Appuie sur Entrée pour quitter…{C.RESET} ")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# CLI UI
# ─────────────────────────────────────────────────────────────────────────────

def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")

def banner() -> None:
    a = C.AMBER + C.BOLD
    r = C.RESET
    print(f"""
{a}  ██████╗██████╗  █████╗ ███╗   ███╗██████╗ ██████╗
 ██╔════╝██╔══██╗██╔══██╗████╗ ████║██╔══██╗██╔══██╗
 ██║     ██████╔╝███████║██╔████╔██║██████╔╝██████╔╝
 ██║     ██╔══██╗██╔══██║██║╚██╔╝██║██╔═══╝ ██╔═══╝
 ╚██████╗██║  ██║██║  ██║██║ ╚═╝ ██║██║     ██║
  ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝╚═╝     ╚═╝{r}
  {C.DIM}Binary Asset to C++ Header Converter  v{__version__}{r}
""")

def separator(char: str = "─", width: int = 58, color: str = C.DIM) -> None:
    print(f"  {color}{char * width}{C.RESET}")

def progress_bar(current: int, total: int, width: int = 38) -> str:
    filled = int(width * current / total) if total else width
    bar    = "█" * filled + "░" * (width - filled)
    pct    = int(100 * current / total) if total else 100
    return f"{C.AMBER}[{bar}]{C.RESET} {C.BOLD}{pct}%{C.RESET}"

def prompt(label: str, default: str = "") -> str:
    hint = f" {C.DIM}[Entrée = {default}]{C.RESET}" if default else ""
    print(f"\n  {C.BOLD}{label}{C.RESET}{hint}")
    print(f"  {C.AMBER}❯{C.RESET} ", end="", flush=True)
    return input().strip() or default

# ─────────────────────────────────────────────────────────────────────────────
# ARGUMENT PARSING
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="crampp",
        description="Binary asset to C++ header converter.",
        add_help=True,
    )
    p.add_argument("paths",         nargs="*",                        help="Files or directories to convert")
    p.add_argument("-o", "--output",metavar="FILE",  default="",      help="Output file path (merged mode)")
    p.add_argument("-r", "--recursive", action="store_true",          help="Scan directories recursively")
    p.add_argument("--split",       action="store_true",              help="One .hpp per asset instead of merged")
    p.add_argument("--watch",       action="store_true",              help="Watch for changes and auto-reconvert")
    p.add_argument("--namespace",   metavar="NAME",  default="",      help="Wrap output in a C++ namespace")
    p.add_argument("--constexpr",   action="store_true",              help="Use constexpr instead of const")
    p.add_argument("--manifest",    action="store_true",              help="Append a runtime asset manifest array")
    p.add_argument("--dry-run",     action="store_true",              help="Preview without writing any file")
    p.add_argument("--version",     action="version", version=f"crampp {__version__}")
    return p.parse_args()

# ─────────────────────────────────────────────────────────────────────────────
# FILE COLLECTION
# ─────────────────────────────────────────────────────────────────────────────

def _parse_drag_drop(raw: str) -> list[str]:
    """Split a drag-and-drop string into individual paths (handles quoted paths)."""
    quoted    = re.findall(r'"([^"]+)"', raw)
    remainder = re.sub(r'"[^"]*"', "", raw)
    unquoted  = [p.strip() for p in remainder.split() if p.strip()]
    return quoted + unquoted

def expand_paths(raw_list: list[str], recursive: bool) -> tuple[list[str], list[str]]:
    """Expand files and directories into asset paths. Returns (valid, skipped)."""
    valid, skipped = [], []
    for raw in raw_list:
        p = raw.strip('"').strip("'")
        if os.path.isdir(p):
            walker = os.walk(p) if recursive else [(p, [], os.listdir(p))]
            for root, _, files in walker:
                for f in files:
                    fp = os.path.join(root, f)
                    if os.path.isfile(fp) and os.path.splitext(f)[1].lower() in ALL_EXTS:
                        valid.append(fp)
        elif os.path.isfile(p):
            if os.path.splitext(p)[1].lower() in ALL_EXTS:
                valid.append(p)
            else:
                skipped.append(p)
        elif p:
            skipped.append(p)
    return valid, skipped

def collect_files(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    raw_list = args.paths
    if not raw_list:
        separator()
        print(f"\n  {C.YELLOW}Aucun fichier détecté en argument.{C.RESET}\n")
        print(f"  {C.AMBER}Glisse tes fichiers/dossiers ici puis appuie sur Entrée :{C.RESET}")
        print(f"  {C.AMBER}❯{C.RESET} ", end="", flush=True)
        raw = input().strip()
        if not raw:
            _exit_error("Aucun fichier fourni.")
        raw_list = _parse_drag_drop(raw)

    valid, skipped = expand_paths(raw_list, args.recursive)
    if not valid:
        exts = ", ".join(sorted(e.lstrip(".").upper() for e in ALL_EXTS))
        _exit_error(f"Aucun fichier valide reconnu.\n  {C.DIM}Formats acceptés : {exts}{C.RESET}")
    return valid, skipped

# ─────────────────────────────────────────────────────────────────────────────
# VARIABLE NAMING & COLLISION DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def make_varname_image(filename: str) -> str:
    base = os.path.splitext(filename)[0]
    return re.sub(r"[^a-zA-Z0-9_]", "_", base)

def make_varname_font(filename: str) -> str:
    base = os.path.splitext(filename)[0]
    name = re.sub(r"[-\s]+", "_", base)
    name = re.sub(r"[^a-zA-Z0-9_]", "", name)
    return re.sub(r"_+", "_", name).strip("_")

def make_varname(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return make_varname_font(filename) if ext in FONT_EXTS else make_varname_image(filename)

def build_file_info(path: str) -> FileInfo:
    abs_path = os.path.abspath(path)
    filename = os.path.basename(path)
    ext      = os.path.splitext(filename)[1].lower()
    return FileInfo(
        path=path, abs_path=abs_path, filename=filename,
        ext=ext, var_name=make_varname(filename), is_font=(ext in FONT_EXTS),
    )

def check_collisions(files: list[FileInfo]) -> list[tuple[FileInfo, FileInfo]]:
    """Return pairs of files that resolve to the same C++ variable name."""
    seen:       dict[str, FileInfo]            = {}
    collisions: list[tuple[FileInfo, FileInfo]] = []
    for f in files:
        if f.var_name in seen:
            collisions.append((seen[f.var_name], f))
        else:
            seen[f.var_name] = f
    return collisions

# ─────────────────────────────────────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────────────────────────────────────

CacheData = dict[str, dict]

def compute_md5(file_path: str) -> Optional[str]:
    md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                md5.update(chunk)
        return md5.hexdigest()
    except OSError:
        return None

def load_cache(cache_path: str) -> CacheData:
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

def save_cache(cache_path: str, data: CacheData) -> None:
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass

def get_file_status(info: FileInfo, cache: CacheData) -> tuple[str, Optional[str]]:
    current_hash = compute_md5(info.abs_path)
    if current_hash is None:
        return "error", None
    if info.abs_path not in cache:
        return "new", current_hash
    if cache[info.abs_path].get("hash") != current_hash:
        return "modified", current_hash
    return "unchanged", current_hash

# ─────────────────────────────────────────────────────────────────────────────
# C++ GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def build_cpp_entry(var_name: str, content: bytes, filename: str, constexpr: bool = False) -> str:
    qual     = "constexpr" if constexpr else "const"
    hex_data = [f"0x{b:02x}" for b in content]
    lines    = [", ".join(hex_data[i:i + HEX_COLS]) for i in range(0, len(hex_data), HEX_COLS)]
    body     = ",\n    ".join(lines)
    return (
        f"// {filename} ({len(content):,} bytes)\n"
        f"{qual} unsigned char {var_name}[] = {{\n"
        f"    {body}\n"
        f"}};\n"
        f"{qual} unsigned int {var_name}_len = {len(content)};\n\n"
    )

def build_manifest(results: list[ConversionResult]) -> str:
    ok      = [r for r in results if r.ok and r.status != "error"]
    entries = "\n".join(
        f'    {{"{r.var_name}", {r.var_name}, {r.var_name}_len}},'
        for r in ok
    )
    return (
        f"struct CramppAsset {{\n"
        f"    const char*           name;\n"
        f"    const unsigned char*  data;\n"
        f"    unsigned int          size;\n"
        f"}};\n\n"
        f"static const CramppAsset crampp_manifest[] = {{\n"
        f"{entries}\n"
        f"}};\n"
        f"static const unsigned int crampp_manifest_len = {len(ok)};\n\n"
    )

def hpp_header(guard: str, asset_count: int, namespace: str = "") -> str:
    ns = f"\nnamespace {namespace} {{\n" if namespace else ""
    return (
        f"#ifndef {guard}\n"
        f"#define {guard}\n\n"
        f"// Generated by crampp v{__version__} — {asset_count} asset(s)\n"
        f"{ns}\n"
    )

def hpp_footer(guard: str, namespace: str = "") -> str:
    ns = f"\n}} // namespace {namespace}\n" if namespace else ""
    return f"{ns}\n#endif // {guard}\n"

def make_include_guard(filepath: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", os.path.basename(filepath).upper())

# ─────────────────────────────────────────────────────────────────────────────
# PLAN SELECTION
# ─────────────────────────────────────────────────────────────────────────────

def build_plan(args: argparse.Namespace, script_dir: str) -> ConversionPlan:
    split_mode = args.split

    if not split_mode and not args.output and not args.watch:
        separator()
        print(f"\n  {C.BOLD}Mode de sortie :{C.RESET}\n")
        print(f"  {C.AMBER}[1]{C.RESET} Fichier unique  {C.DIM}(assets_bin.hpp){C.RESET}")
        print(f"  {C.AMBER}[2]{C.RESET} Un fichier/asset")
        split_mode = (prompt("Choix", default="1") == "2")

    merge_file = ""
    if not split_mode:
        if args.output:
            name = args.output
        else:
            separator()
            name = prompt("Nom du fichier fusionné", default="assets_bin.hpp")
        if not name.endswith(".hpp"):
            name += ".hpp"
        merge_file = name if os.path.isabs(name) else os.path.join(script_dir, name)

    return ConversionPlan(
        split_mode=split_mode,
        merge_file=merge_file,
        namespace=args.namespace,
        constexpr=args.constexpr,
        manifest=args.manifest,
        dry_run=args.dry_run,
    )

# ─────────────────────────────────────────────────────────────────────────────
# CONVERSION
# ─────────────────────────────────────────────────────────────────────────────

def _resolve(name: str, script_dir: str) -> str:
    return name if os.path.isabs(name) else os.path.join(script_dir, name)

def convert_split(
    files:     list[FileInfo],
    plan:      ConversionPlan,
    cache:     CacheData,
    new_cache: CacheData,
    script_dir: str,
) -> list[ConversionResult]:
    results: list[ConversionResult] = []
    total = len(files)

    for i, info in enumerate(files, 1):
        out_path = _resolve(info.var_name + ".hpp", script_dir)
        status, current_hash = get_file_status(info, cache)

        print(
            f"\r  {progress_bar(i - 1, total)} "
            f"{C.DIM}{info.filename[:30]:<30}{C.RESET}",
            end="", flush=True,
        )

        if plan.dry_run:
            results.append(ConversionResult(
                filename=info.filename, var_name=info.var_name,
                size=os.path.getsize(info.path), ok=True,
                out_path=out_path, status="dry-run",
            ))
            time.sleep(0.02)
            continue

        if status == "unchanged" and os.path.isfile(out_path):
            cached = cache[info.abs_path]
            results.append(ConversionResult(
                filename=info.filename, var_name=info.var_name,
                size=cached.get("size", 0), ok=True,
                out_path=out_path, status="unchanged",
            ))
            time.sleep(0.02)
            continue

        try:
            raw   = open(info.path, "rb").read()
            entry = build_cpp_entry(info.var_name, raw, info.filename, plan.constexpr)
            guard = make_include_guard(out_path)
            body  = entry
            if plan.manifest:
                tmp = [ConversionResult(filename=info.filename, var_name=info.var_name,
                                        size=len(raw), ok=True, out_path=out_path, status=status)]
                body += build_manifest(tmp)
            content = hpp_header(guard, 1, plan.namespace) + body + hpp_footer(guard, plan.namespace)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(content)
            new_cache[info.abs_path] = {
                "hash": current_hash, "var_name": info.var_name,
                "size": len(raw), "cpp_entry": entry,
            }
            results.append(ConversionResult(
                filename=info.filename, var_name=info.var_name,
                size=len(raw), ok=True, out_path=out_path, status=status,
            ))
        except OSError as exc:
            results.append(ConversionResult(
                filename=info.filename, var_name=info.var_name,
                size=0, ok=False, out_path="", status="error", error_msg=str(exc),
            ))
        time.sleep(0.02)

    return results

def convert_merge(
    files:     list[FileInfo],
    plan:      ConversionPlan,
    cache:     CacheData,
    new_cache: CacheData,
) -> list[ConversionResult]:
    results: list[ConversionResult] = []
    total = len(files)
    guard = make_include_guard(plan.merge_file)
    body  = ""

    for i, info in enumerate(files, 1):
        status, current_hash = get_file_status(info, cache)
        cached = cache.get(info.abs_path, {})

        print(
            f"\r  {progress_bar(i - 1, total)} "
            f"{C.DIM}{info.filename[:30]:<30}{C.RESET}",
            end="", flush=True,
        )

        if plan.dry_run:
            results.append(ConversionResult(
                filename=info.filename, var_name=info.var_name,
                size=os.path.getsize(info.path), ok=True,
                out_path=plan.merge_file, status="dry-run",
            ))
            time.sleep(0.02)
            continue

        if status == "unchanged" and "cpp_entry" in cached:
            body += cached["cpp_entry"]
            results.append(ConversionResult(
                filename=info.filename, var_name=info.var_name,
                size=cached.get("size", 0), ok=True,
                out_path=plan.merge_file, status="unchanged",
            ))
            time.sleep(0.02)
            continue

        try:
            raw   = open(info.path, "rb").read()
            entry = build_cpp_entry(info.var_name, raw, info.filename, plan.constexpr)
            body += entry
            new_cache[info.abs_path] = {
                "hash": current_hash, "var_name": info.var_name,
                "size": len(raw), "cpp_entry": entry,
            }
            results.append(ConversionResult(
                filename=info.filename, var_name=info.var_name,
                size=len(raw), ok=True, out_path=plan.merge_file, status=status,
            ))
        except OSError as exc:
            results.append(ConversionResult(
                filename=info.filename, var_name=info.var_name,
                size=0, ok=False, out_path="", status="error", error_msg=str(exc),
            ))
        time.sleep(0.02)

    if not plan.dry_run:
        if plan.manifest:
            body += build_manifest(results)
        with open(plan.merge_file, "w", encoding="utf-8") as f:
            f.write(hpp_header(guard, total, plan.namespace) + body + hpp_footer(guard, plan.namespace))

    return results

# ─────────────────────────────────────────────────────────────────────────────
# DISPLAY
# ─────────────────────────────────────────────────────────────────────────────

_STATUS_ICON = {
    "new":       f"{C.GREEN}✓{C.RESET}",
    "modified":  f"{C.YELLOW}↺{C.RESET}",
    "unchanged": f"{C.DIM}─{C.RESET}",
    "error":     f"{C.RED}✗{C.RESET}",
    "dry-run":   f"{C.AMBER}◆{C.RESET}",
}
_STATUS_LABEL = {
    "new":       f"{C.GREEN}✓ nouveau{C.RESET}",
    "modified":  f"{C.YELLOW}↺ modif.{C.RESET}",
    "unchanged": f"{C.DIM}─ intact{C.RESET}",
    "error":     f"{C.RED}✗ erreur{C.RESET}",
    "dry-run":   f"{C.AMBER}◆ dry-run{C.RESET}",
}

def print_detected_files(images: list[str], fonts: list[str], skipped: list[str]) -> None:
    separator("─", 58, C.AMBER)
    print(f"\n  {C.BOLD}Fichiers détectés :{C.RESET}\n")
    if images:
        print(f"  {C.MAGENTA}Images ({len(images)}){C.RESET}")
        for f in images:
            print(f"  {C.DIM}• {os.path.basename(f)}{C.RESET}")
    if fonts:
        print(f"\n  {C.BLUE}Fonts ({len(fonts)}){C.RESET}")
        for f in fonts:
            print(f"  {C.DIM}• {os.path.basename(f)}{C.RESET}")
    if skipped:
        print(f"\n  {C.RED}Ignorés ({len(skipped)}){C.RESET}")
        for f in skipped:
            print(f"  {C.DIM}• {os.path.basename(f)}{C.RESET}")

def print_collisions(collisions: list[tuple[FileInfo, FileInfo]]) -> None:
    if not collisions:
        return
    separator("─", 58, C.RED)
    print(f"\n  {C.RED}{C.BOLD}Collision de noms de variables :{C.RESET}\n")
    for a, b in collisions:
        print(f"  {C.RED}✗{C.RESET} {C.BOLD}{a.var_name}{C.RESET}")
        print(f"    {C.DIM}← {a.filename}{C.RESET}")
        print(f"    {C.DIM}← {b.filename}{C.RESET}")
    print(f"\n  {C.YELLOW}Renomme un des fichiers sources pour résoudre le conflit.{C.RESET}\n")
    _exit_error("Conversion annulée — collisions à résoudre.")

def print_diff_analysis(files: list[FileInfo], cache: CacheData) -> None:
    separator()
    print(f"\n  {C.BOLD}Analyse des changements…{C.RESET}\n")
    counts: dict[str, int] = {"new": 0, "modified": 0, "unchanged": 0, "error": 0}
    _labels = {
        "new":       f"{C.GREEN}nouveau  {C.RESET}",
        "modified":  f"{C.YELLOW}modifié  {C.RESET}",
        "unchanged": f"{C.DIM}inchangé {C.RESET}",
        "error":     f"{C.RED}erreur   {C.RESET}",
    }
    for info in files:
        st, _ = get_file_status(info, cache)
        counts[st] = counts.get(st, 0) + 1
        print(f"  {_STATUS_ICON.get(st, '?')} {_labels.get(st, st)} {C.DIM}{info.filename}{C.RESET}")
    print(
        f"\n  {C.BOLD}Résumé :{C.RESET} "
        f"{C.GREEN}{counts['new']} nouveau{C.RESET}  "
        f"{C.YELLOW}{counts['modified']} modifié{C.RESET}  "
        f"{C.DIM}{counts['unchanged']} inchangé{C.RESET}\n"
    )

def print_results(results: list[ConversionResult], plan: ConversionPlan) -> None:
    separator("─", 58, C.GREEN)
    tag = f"{C.AMBER}[DRY-RUN]{C.RESET} " if plan.dry_run else ""
    print(f"\n  {C.GREEN}{C.BOLD}✓ {tag}Conversion terminée !{C.RESET}\n")

    W_FILE, W_VAR, W_SIZE, W_ST = 28, 22, 9, 12
    print(
        f"  {'Fichier source':<{W_FILE}} "
        f"{'Variable C++':<{W_VAR}} "
        f"{'Taille':>{W_SIZE}} "
        f"{'Status':>{W_ST}}"
    )
    separator("·", 58, C.DIM)

    total_bytes = 0
    for r in results:
        if not r.ok:
            icon     = f"{C.RED}✗{C.RESET}"
            size_str = "–"
            st_str   = _STATUS_LABEL["error"]
            fname    = f"{C.RED}{r.filename}{C.RESET}"
        else:
            icon     = f"{C.GREEN}✓{C.RESET}"
            size_str = f"{r.size:,} B"
            st_str   = _STATUS_LABEL.get(r.status, "?")
            fname    = r.filename
            total_bytes += r.size
        print(
            f"  {icon} "
            f"{pad_right(fname, W_FILE)} "
            f"{pad_right(C.DIM + r.var_name + C.RESET, W_VAR)} "
            f"{pad_left(C.BLUE + size_str + C.RESET, W_SIZE)} "
            f"{pad_left(st_str, W_ST)}"
        )

    separator("·", 58, C.DIM)
    print(f"\n  {C.BOLD}Total :{C.RESET} {len(results)} fichier(s) — {C.BLUE}{total_bytes:,} bytes{C.RESET}")

    if not plan.dry_run:
        if plan.split_mode:
            written = [r for r in results if r.ok and r.status != "unchanged"]
            print(f"\n  {C.BOLD}Fichiers générés ({len(written)}) :{C.RESET}")
            for r in written:
                sz = os.path.getsize(r.out_path)
                print(f"  {C.GREEN}•{C.RESET} {os.path.basename(r.out_path):<35} {C.DIM}{sz:,} B{C.RESET}")
            intact = [r for r in results if r.ok and r.status == "unchanged"]
            if intact:
                print(f"  {C.DIM}  {len(intact)} fichier(s) inchangé(s) non réécrit(s).{C.RESET}")
        else:
            sz = os.path.getsize(plan.merge_file)
            print(f"\n  {C.BOLD}Sortie :{C.RESET} {C.GREEN}{plan.merge_file}{C.RESET} {C.DIM}({sz:,} B){C.RESET}")

    extras = []
    if plan.namespace:  extras.append(f"namespace {plan.namespace}")
    if plan.constexpr:  extras.append("constexpr")
    if plan.manifest:   extras.append("manifest crampp_manifest[]")
    if extras:
        print(f"  {C.DIM}Options : {', '.join(extras)}{C.RESET}")

# ─────────────────────────────────────────────────────────────────────────────
# WATCH MODE
# ─────────────────────────────────────────────────────────────────────────────

def watch_loop(
    raw_paths:  list[str],
    plan:       ConversionPlan,
    script_dir: str,
    recursive:  bool,
) -> None:
    """Poll every WATCH_INTERVAL seconds; reconvert on any change."""
    cache_path = os.path.join(script_dir, CACHE_FILENAME)
    print(f"\n  {C.AMBER}{C.BOLD}Watch mode actif{C.RESET} {C.DIM}(Ctrl+C pour arrêter){C.RESET}\n")

    while True:
        valid, _ = expand_paths(raw_paths, recursive)
        files     = [build_file_info(p) for p in valid]
        cache     = load_cache(cache_path)
        new_cache = copy.deepcopy(cache)

        changed = [f for f in files if get_file_status(f, cache)[0] in ("new", "modified")]

        if changed:
            print(f"\n  {C.AMBER}↺{C.RESET} {len(changed)} changement(s) — reconversion…")
            separator()
            print()
            if plan.split_mode:
                results = convert_split(files, plan, cache, new_cache, script_dir)
            else:
                results = convert_merge(files, plan, cache, new_cache)
            print(f"\r  {progress_bar(len(files), len(files))} {'':30}\n")
            print_results(results, plan)
            save_cache(cache_path, new_cache)
            print(f"\n  {C.DIM}Surveillance en cours…{C.RESET}")

        time.sleep(WATCH_INTERVAL)

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    clear_screen()
    banner()

    # 1. Collect files
    valid_paths, skipped = collect_files(args)
    files  = [build_file_info(p) for p in valid_paths]
    images = [f.path for f in files if f.ext in IMG_EXTS]
    fonts  = [f.path for f in files if f.ext in FONT_EXTS]

    print_detected_files(images, fonts, skipped)

    # 2. Collision detection — exits on conflict
    print_collisions(check_collisions(files))

    # 3. Cache & plan
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cache_path = os.path.join(script_dir, CACHE_FILENAME)
    cache      = load_cache(cache_path)
    new_cache  = copy.deepcopy(cache)
    plan       = build_plan(args, script_dir)

    if plan.dry_run:
        separator()
        print(f"\n  {C.AMBER}{C.BOLD}Mode dry-run — aucun fichier ne sera écrit.{C.RESET}\n")

    # 4. Watch mode — runs forever until Ctrl+C
    if args.watch:
        watch_loop(args.paths or ["."], plan, script_dir, args.recursive)
        return

    # 5. Diff analysis
    print_diff_analysis(files, cache)

    # 6. Convert
    separator()
    print(f"\n  {C.BOLD}Conversion en cours…{C.RESET}\n")

    if plan.split_mode:
        results = convert_split(files, plan, cache, new_cache, script_dir)
    else:
        results = convert_merge(files, plan, cache, new_cache)

    print(f"\r  {progress_bar(len(files), len(files))} {'':30}\n")

    # 7. Results & persist cache
    print_results(results, plan)

    if not plan.dry_run:
        save_cache(cache_path, new_cache)
        print(f"\n  {C.DIM}Cache mis à jour : {CACHE_FILENAME}{C.RESET}")

    print()
    separator("─", 58, C.AMBER)
    input(f"\n  {C.DIM}Appuie sur Entrée pour quitter…{C.RESET} ")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {C.DIM}Interrompu.{C.RESET}\n")
    except Exception:
        print(f"\n  {C.RED}{'─' * 58}{C.RESET}")
        print(f"  {C.RED}{C.BOLD}✗ ERREUR — copie ce message et envoie-le{C.RESET}\n")
        traceback.print_exc()
        print(f"\n  {C.RED}{'─' * 58}{C.RESET}")
        input("\n  Appuie sur Entrée pour quitter… ")
