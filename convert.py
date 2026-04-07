"""
ch2hp — Binary Asset to C++ Header Converter
=============================================
Converts PNG images and font files into embeddable C++ header files (.hpp).

Supports:
  Images : PNG, JPG, JPEG, BMP, ICO, GIF
  Fonts  : TTF, OTF, WOFF, WOFF2

Output modes:
  1. Single merged .hpp  (all assets in one file)
  2. One .hpp per asset  (named after the C++ variable)

Features:
  - Smart diff: only re-converts changed files (MD5 cache)
  - Drag & drop support on Windows CMD
  - ANSI-colored CLI with progress bar

Usage:
  python convert.py [file1 file2 ...]
  python convert.py --help

Version : 1.0.0
License : MIT
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
#  VERSION
# ─────────────────────────────────────────────────────────────────────────────

__version__ = "1.0.0"

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

IMG_EXTS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".ico", ".gif"})
FONT_EXTS: frozenset[str] = frozenset({".ttf", ".otf", ".woff", ".woff2"})
ALL_EXTS: frozenset[str] = IMG_EXTS | FONT_EXTS

CACHE_FILENAME = ".convert_cache.json"
HEX_COLS = 12  # bytes per line in generated arrays

# ─────────────────────────────────────────────────────────────────────────────
#  ANSI COLORS
# ─────────────────────────────────────────────────────────────────────────────

class C:
    """ANSI color/style codes."""
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    CYAN   = "\033[96m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    MAGENTA= "\033[95m"
    BLUE   = "\033[94m"

def _ansi_visible_len(s: str) -> int:
    """Return the visible length of a string, excluding ANSI escape codes."""
    return len(re.sub(r"\033\[[0-9;]*m", "", s))

def pad_right(s: str, width: int) -> str:
    """Right-pad a string to `width` visible characters (ANSI-safe)."""
    return s + " " * max(0, width - _ansi_visible_len(s))

def pad_left(s: str, width: int) -> str:
    """Left-pad a string to `width` visible characters (ANSI-safe)."""
    return " " * max(0, width - _ansi_visible_len(s)) + s

# ─────────────────────────────────────────────────────────────────────────────
#  DATA MODELS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FileInfo:
    """Metadata for a single input file."""
    path: str
    abs_path: str
    filename: str
    ext: str
    var_name: str
    is_font: bool

@dataclass
class ConversionResult:
    """Outcome of converting a single asset."""
    filename: str
    var_name: str
    size: int
    ok: bool
    out_path: str
    status: str          # 'new' | 'modified' | 'unchanged' | 'error'
    error_msg: str = ""

@dataclass
class ConversionPlan:
    """User-selected options before conversion starts."""
    split_mode: bool
    merge_file: str = ""  # only used when split_mode is False

# ─────────────────────────────────────────────────────────────────────────────
#  CLI UI
# ─────────────────────────────────────────────────────────────────────────────

def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")

def banner() -> None:
    print(f"""
{C.CYAN}{C.BOLD}
  ╔══════════════════════════════════════════════════════╗
  ║        ██████╗██╗  ██╗██████╗     ██╗  ██╗██████╗    ║
  ║       ██╔════╝██║  ██║╚════██╗    ██║  ██║██╔══██╗   ║
  ║       ██║     ███████║ █████╔╝    ███████║██████╔╝   ║
  ║       ██║     ██╔══██║██╔═══╝     ██╔══██║██╔═══╝    ║
  ║       ╚██████╗██║  ██║███████╗    ██║  ██║██║        ║
  ║        ╚═════╝╚═╝  ╚═╝╚══════╝    ╚═╝  ╚═╝╚═╝        ║
  ║                                                      ║
  ║    PNG / Fonts  ──►  C++ Header (.hpp)  v{__version__:<9}   ║
  ╚══════════════════════════════════════════════════════╝{C.RESET}
""")

def separator(char: str = "─", width: int = 58, color: str = C.DIM) -> None:
    print(f"  {color}{char * width}{C.RESET}")

def progress_bar(current: int, total: int, width: int = 40) -> str:
    filled = int(width * current / total) if total else width
    bar    = "█" * filled + "░" * (width - filled)
    pct    = int(100 * current / total) if total else 100
    return f"{C.CYAN}[{bar}]{C.RESET} {C.BOLD}{pct}%{C.RESET}"

def prompt(label: str, default: str = "") -> str:
    """Display a prompt and return user input, falling back to `default`."""
    hint = f"  {C.DIM}[Entrée = {default}]{C.RESET}" if default else ""
    print(f"\n  {C.BOLD}{label}{C.RESET}{hint}")
    print(f"  {C.CYAN}❯{C.RESET}  ", end="", flush=True)
    return input().strip() or default

def print_help() -> None:
    print(__doc__)

# ─────────────────────────────────────────────────────────────────────────────
#  FILE COLLECTION
# ─────────────────────────────────────────────────────────────────────────────

def parse_paths(raw: str) -> list[str]:
    """
    Split a raw string of paths into individual paths.

    Handles Windows drag-and-drop where paths containing spaces are
    automatically quoted and multiple paths are space-separated.

    Example input: '"C:\\path with space\\a.ttf" C:\\simple\\b.ttf'
    """
    quoted    = re.findall(r'"([^"]+)"', raw)
    remainder = re.sub(r'"[^"]*"', "", raw)
    unquoted  = [p.strip() for p in remainder.split() if p.strip()]
    return quoted + unquoted

def filter_paths(raw_list: list[str]) -> tuple[list[str], list[str]]:
    """
    Separate a list of raw path strings into valid asset paths and skipped ones.

    Returns:
        (valid_paths, skipped_paths)
    """
    valid, skipped = [], []
    for p in raw_list:
        p = p.strip('"').strip("'")
        if os.path.isfile(p) and os.path.splitext(p)[1].lower() in ALL_EXTS:
            valid.append(p)
        elif p:
            skipped.append(p)
    return valid, skipped

def collect_files(argv: list[str]) -> tuple[list[str], list[str]]:
    """
    Resolve input files from CLI arguments or an interactive drag-and-drop prompt.

    Returns:
        (valid_paths, skipped_paths)

    Raises:
        SystemExit: if no valid files are provided after prompting.
    """
    valid, skipped = filter_paths(argv)

    if not valid:
        separator()
        print(f"\n  {C.YELLOW}Aucun fichier détecté en argument.{C.RESET}\n")
        print(f"  {C.CYAN}Glisse tes fichiers ici puis appuie sur Entrée :{C.RESET}")
        print(f"  {C.CYAN}❯{C.RESET}  ", end="", flush=True)
        raw = input().strip()
        if not raw:
            _exit_error("Aucun fichier fourni.")
        valid, skipped = filter_paths(parse_paths(raw))

    if not valid:
        exts = ", ".join(sorted(e.lstrip(".").upper() for e in ALL_EXTS))
        _exit_error(
            f"Aucun fichier valide reconnu.\n"
            f"  {C.DIM}Formats acceptés : {exts}{C.RESET}"
        )

    return valid, skipped

# ─────────────────────────────────────────────────────────────────────────────
#  VARIABLE NAMING
# ─────────────────────────────────────────────────────────────────────────────

def make_varname_image(filename: str) -> str:
    """
    Derive a valid C++ identifier from an image filename.

    Example: 'my-icon.png' → 'my_icon'
    """
    base = os.path.splitext(filename)[0]
    return re.sub(r"[^a-zA-Z0-9_]", "_", base)

def make_varname_font(filename: str) -> str:
    """
    Derive a valid C++ identifier from a font filename, preserving
    both family name and variant.

    Examples:
        'Roboto-BoldItalic.ttf'  → 'Roboto_BoldItalic'
        'Inter_18pt-Regular.otf' → 'Inter_18pt_Regular'
    """
    base = os.path.splitext(filename)[0]
    name = re.sub(r"[-\s]+", "_", base)
    name = re.sub(r"[^a-zA-Z0-9_]", "", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name

def make_varname(filename: str) -> str:
    """Dispatch to the appropriate naming function based on file extension."""
    ext = os.path.splitext(filename)[1].lower()
    return make_varname_font(filename) if ext in FONT_EXTS else make_varname_image(filename)

def build_file_info(path: str) -> FileInfo:
    """Build a :class:`FileInfo` from a validated file path."""
    abs_path = os.path.abspath(path)
    filename = os.path.basename(path)
    ext      = os.path.splitext(filename)[1].lower()
    return FileInfo(
        path=path,
        abs_path=abs_path,
        filename=filename,
        ext=ext,
        var_name=make_varname(filename),
        is_font=(ext in FONT_EXTS),
    )

# ─────────────────────────────────────────────────────────────────────────────
#  CACHE
# ─────────────────────────────────────────────────────────────────────────────

CacheData = dict[str, dict]  # {abs_path: {hash, var_name, size, cpp_entry}}

def compute_md5(file_path: str) -> Optional[str]:
    """Return the MD5 hex digest of a file, or None on read error."""
    md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                md5.update(chunk)
        return md5.hexdigest()
    except OSError:
        return None

def load_cache(cache_path: str) -> CacheData:
    """
    Load the JSON cache from disk.

    Returns an empty dict if the file does not exist or is corrupted.
    """
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

def save_cache(cache_path: str, data: CacheData) -> None:
    """Persist the cache to disk, silently ignoring write errors."""
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass

def get_file_status(info: FileInfo, cache: CacheData) -> tuple[str, Optional[str]]:
    """
    Determine whether a file is new, modified, or unchanged.

    Returns:
        (status, current_hash) where status is one of
        'new' | 'modified' | 'unchanged' | 'error'
    """
    current_hash = compute_md5(info.abs_path)
    if current_hash is None:
        return "error", None
    if info.abs_path not in cache:
        return "new", current_hash
    if cache[info.abs_path].get("hash") != current_hash:
        return "modified", current_hash
    return "unchanged", current_hash

# ─────────────────────────────────────────────────────────────────────────────
#  C++ GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def build_cpp_entry(var_name: str, content: bytes, filename: str) -> str:
    """
    Generate a C++ array definition for the given binary content.

    Example output::

        // font.ttf  (12,345 bytes)
        const unsigned char Roboto_Bold[] = {
            0x00, 0x01, ...
        };
        const unsigned int Roboto_Bold_len = 12345;
    """
    hex_data = [f"0x{b:02x}" for b in content]
    lines    = []
    for i in range(0, len(hex_data), HEX_COLS):
        chunk = ", ".join(hex_data[i : i + HEX_COLS])
        lines.append(chunk)

    array_body = ",\n    ".join(lines)
    return (
        f"// {filename}  ({len(content):,} bytes)\n"
        f"const unsigned char {var_name}[] = {{\n"
        f"    {array_body}\n"
        f"}};\n"
        f"const unsigned int {var_name}_len = {len(content)};\n\n"
    )

def hpp_header(guard: str, asset_count: int) -> str:
    """Return the opening lines of a generated .hpp file."""
    return (
        f"#ifndef {guard}\n"
        f"#define {guard}\n\n"
        f"// Generated by ch2hp v{__version__} — {asset_count} asset(s)\n\n"
    )

def hpp_footer(guard: str) -> str:
    return f"#endif // {guard}\n"

def make_include_guard(filepath: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", os.path.basename(filepath).upper())

# ─────────────────────────────────────────────────────────────────────────────
#  MODE SELECTION
# ─────────────────────────────────────────────────────────────────────────────

def select_plan(script_dir: str) -> ConversionPlan:
    """Interactively ask the user to choose output mode and filename."""
    separator()
    print(f"\n  {C.BOLD}Mode de sortie :{C.RESET}\n")
    print(f"  {C.CYAN}[1]{C.RESET}  Fichier unique   {C.DIM}(tout mergé dans assets_bin.hpp){C.RESET}")
    print(f"  {C.CYAN}[2]{C.RESET}  Un fichier/asset {C.DIM}(NomVariable.hpp pour chacun){C.RESET}")
    choice = prompt("Choix", default="1")

    if choice == "2":
        return ConversionPlan(split_mode=True)

    separator()
    print(f"  {C.DIM}Dossier de sortie : {script_dir}{C.RESET}")
    name = prompt("Nom du fichier fusionné", default="assets_bin.hpp")
    if not name.endswith(".hpp"):
        name += ".hpp"
    merge_path = name if os.path.isabs(name) else os.path.join(script_dir, name)
    return ConversionPlan(split_mode=False, merge_file=merge_path)

# ─────────────────────────────────────────────────────────────────────────────
#  CONVERSION
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_output(name: str, script_dir: str) -> str:
    """Return an absolute path, rooted in script_dir if not already absolute."""
    return name if os.path.isabs(name) else os.path.join(script_dir, name)

def convert_split(
    files: list[FileInfo],
    cache: CacheData,
    new_cache: CacheData,
    script_dir: str,
) -> list[ConversionResult]:
    """Convert each asset to its own .hpp file, skipping unchanged ones."""
    results: list[ConversionResult] = []
    total = len(files)

    for i, info in enumerate(files, 1):
        out_path = _resolve_output(info.var_name + ".hpp", script_dir)
        status, current_hash = get_file_status(info, cache)

        print(
            f"\r  {progress_bar(i - 1, total)}  "
            f"{C.DIM}{info.filename[:30]:<30}{C.RESET}",
            end="", flush=True,
        )

        # Skip if unchanged AND the output file already exists on disk
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
            raw     = open(info.path, "rb").read()
            entry   = build_cpp_entry(info.var_name, raw, info.filename)
            guard   = make_include_guard(out_path)
            content = hpp_header(guard, 1) + entry + hpp_footer(guard)

            with open(out_path, "w", encoding="utf-8") as f:
                f.write(content)

            new_cache[info.abs_path] = {
                "hash": current_hash,
                "var_name": info.var_name,
                "size": len(raw),
                "cpp_entry": entry,
            }
            results.append(ConversionResult(
                filename=info.filename, var_name=info.var_name,
                size=len(raw), ok=True, out_path=out_path, status=status,
            ))
        except OSError as exc:
            results.append(ConversionResult(
                filename=info.filename, var_name=info.var_name,
                size=0, ok=False, out_path="", status="error",
                error_msg=str(exc),
            ))
        time.sleep(0.02)

    return results

def convert_merge(
    files: list[FileInfo],
    merge_file: str,
    cache: CacheData,
    new_cache: CacheData,
) -> list[ConversionResult]:
    """
    Convert all assets into a single .hpp file.

    Unchanged files are written from the cached C++ snippet instead of
    re-reading the binary, making the diff genuinely useful.
    """
    results: list[ConversionResult] = []
    total   = len(files)
    guard   = make_include_guard(merge_file)
    body    = ""

    for i, info in enumerate(files, 1):
        status, current_hash = get_file_status(info, cache)

        print(
            f"\r  {progress_bar(i - 1, total)}  "
            f"{C.DIM}{info.filename[:30]:<30}{C.RESET}",
            end="", flush=True,
        )

        cached = cache.get(info.abs_path, {})

        # Reuse cached C++ snippet for unchanged files
        if status == "unchanged" and "cpp_entry" in cached:
            entry = cached["cpp_entry"]
            size  = cached.get("size", 0)
            body += entry
            results.append(ConversionResult(
                filename=info.filename, var_name=info.var_name,
                size=size, ok=True, out_path=merge_file, status="unchanged",
            ))
            time.sleep(0.02)
            continue

        try:
            raw   = open(info.path, "rb").read()
            entry = build_cpp_entry(info.var_name, raw, info.filename)
            body += entry
            new_cache[info.abs_path] = {
                "hash": current_hash,
                "var_name": info.var_name,
                "size": len(raw),
                "cpp_entry": entry,
            }
            results.append(ConversionResult(
                filename=info.filename, var_name=info.var_name,
                size=len(raw), ok=True, out_path=merge_file, status=status,
            ))
        except OSError as exc:
            results.append(ConversionResult(
                filename=info.filename, var_name=info.var_name,
                size=0, ok=False, out_path="", status="error",
                error_msg=str(exc),
            ))
        time.sleep(0.02)

    with open(merge_file, "w", encoding="utf-8") as f:
        f.write(hpp_header(guard, total) + body + hpp_footer(guard))

    return results

# ─────────────────────────────────────────────────────────────────────────────
#  DISPLAY
# ─────────────────────────────────────────────────────────────────────────────

_STATUS_LABEL: dict[str, str] = {
    "new":       f"{C.GREEN}✓ nouveau{C.RESET}",
    "modified":  f"{C.YELLOW}↺ modif.{C.RESET}",
    "unchanged": f"{C.DIM}─ intact{C.RESET}",
    "error":     f"{C.RED}✗ erreur{C.RESET}",
}

_STATUS_ANALYSIS_LABEL: dict[str, str] = {
    "new":       f"{C.GREEN}nouveau  {C.RESET}",
    "modified":  f"{C.YELLOW}modifié  {C.RESET}",
    "unchanged": f"{C.DIM}inchangé {C.RESET}",
    "error":     f"{C.RED}erreur   {C.RESET}",
}

_STATUS_ICON: dict[str, str] = {
    "new":       f"{C.GREEN}✓{C.RESET}",
    "modified":  f"{C.YELLOW}↺{C.RESET}",
    "unchanged": f"{C.DIM}─{C.RESET}",
    "error":     f"{C.RED}✗{C.RESET}",
}

def print_detected_files(
    images: list[str],
    fonts: list[str],
    skipped: list[str],
) -> None:
    separator("─", 58, C.CYAN)
    print(f"\n  {C.BOLD}Fichiers détectés :{C.RESET}\n")
    if images:
        print(f"  {C.MAGENTA}🖼  Images  ({len(images)}){C.RESET}")
        for f in images:
            print(f"      {C.DIM}•  {os.path.basename(f)}{C.RESET}")
    if fonts:
        print(f"\n  {C.BLUE}🔤  Fonts   ({len(fonts)}){C.RESET}")
        for f in fonts:
            print(f"      {C.DIM}•  {os.path.basename(f)}{C.RESET}")
    if skipped:
        print(f"\n  {C.RED}✗  Ignorés ({len(skipped)}){C.RESET}")
        for f in skipped:
            print(f"      {C.DIM}•  {os.path.basename(f)}{C.RESET}")

def print_diff_analysis(files: list[FileInfo], cache: CacheData) -> dict[str, tuple[str, Optional[str]]]:
    """
    Display per-file diff status and return a map of abs_path → (status, hash).
    """
    separator()
    print(f"\n  {C.BOLD}Analyse des changements…{C.RESET}\n")

    statuses: dict[str, tuple[str, Optional[str]]] = {}
    counts = {"new": 0, "modified": 0, "unchanged": 0, "error": 0}

    for info in files:
        st, h = get_file_status(info, cache)
        statuses[info.abs_path] = (st, h)
        counts[st] = counts.get(st, 0) + 1
        icon  = _STATUS_ICON.get(st, "?")
        label = _STATUS_ANALYSIS_LABEL.get(st, st)
        print(f"  {icon}  {label}  {C.DIM}{info.filename}{C.RESET}")

    print(
        f"\n  {C.BOLD}Résumé :{C.RESET}  "
        f"{C.GREEN}{counts['new']} nouveau{C.RESET}  "
        f"{C.YELLOW}{counts['modified']} modifié{C.RESET}  "
        f"{C.DIM}{counts['unchanged']} inchangé{C.RESET}\n"
    )
    return statuses

def print_results(
    results: list[ConversionResult],
    plan: ConversionPlan,
    script_dir: str,
) -> None:
    separator("─", 58, C.GREEN)
    print(f"\n  {C.GREEN}{C.BOLD}✓  Conversion terminée !{C.RESET}\n")

    W_FILE, W_VAR, W_SIZE, W_ST = 28, 24, 9, 10
    print(
        f"  {'Fichier source':<{W_FILE}}  "
        f"{'Variable C++':<{W_VAR}}  "
        f"{'Taille':>{W_SIZE}}  "
        f"{'Status':>{W_ST}}"
    )
    separator("·", 58, C.DIM)

    total_bytes = 0
    for r in results:
        if not r.ok:
            row_icon  = f"{C.RED}✗{C.RESET}"
            size_str  = "–"
            st_str    = _STATUS_LABEL["error"]
            fname_col = f"{C.RED}{r.filename}{C.RESET}"
        else:
            row_icon  = f"{C.GREEN}✓{C.RESET}"
            size_str  = f"{r.size:,} B"
            st_str    = _STATUS_LABEL.get(r.status, "?")
            fname_col = r.filename
            total_bytes += r.size

        print(
            f"  {row_icon}  "
            f"{pad_right(fname_col, W_FILE)}  "
            f"{pad_right(C.DIM + r.var_name + C.RESET, W_VAR)}  "
            f"{pad_left(C.BLUE + size_str + C.RESET, W_SIZE)}  "
            f"{pad_left(st_str, W_ST)}"
        )

    separator("·", 58, C.DIM)
    print(f"\n  {C.BOLD}Total :{C.RESET}  {len(results)} fichier(s)  —  {C.BLUE}{total_bytes:,} bytes{C.RESET}")

    if plan.split_mode:
        written = [r for r in results if r.ok and r.status != "unchanged"]
        print(f"\n  {C.BOLD}Fichiers (re)générés ({len(written)}) :{C.RESET}")
        for r in written:
            sz = os.path.getsize(r.out_path)
            print(f"    {C.GREEN}•{C.RESET}  {os.path.basename(r.out_path):<35} {C.DIM}{sz:,} B{C.RESET}")
        skipped = [r for r in results if r.ok and r.status == "unchanged"]
        if skipped:
            print(f"  {C.DIM}  {len(skipped)} fichier(s) inchangé(s) non réécrit(s).{C.RESET}")
    else:
        sz = os.path.getsize(plan.merge_file)
        print(f"  {C.BOLD}Sortie :{C.RESET}  {C.GREEN}{plan.merge_file}{C.RESET}  {C.DIM}({sz:,} B){C.RESET}")

# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _exit_error(message: str) -> None:
    """Print an error message and wait for a keypress before exiting."""
    separator()
    print(f"\n  {C.RED}✗  {message}{C.RESET}\n")
    separator()
    input(f"\n  {C.DIM}Appuie sur Entrée pour quitter…{C.RESET}  ")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    if "--help" in sys.argv or "-h" in sys.argv:
        print_help()
        return

    clear_screen()
    banner()

    # 1. Collect input files
    valid_paths, skipped = collect_files(sys.argv[1:])
    files = [build_file_info(p) for p in valid_paths]

    images = [f.path for f in files if f.ext in IMG_EXTS]
    fonts  = [f.path for f in files if f.ext in FONT_EXTS]
    print_detected_files(images, fonts, skipped)

    # 2. Load cache & select output plan
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cache_path = os.path.join(script_dir, CACHE_FILENAME)
    cache      = load_cache(cache_path)
    new_cache  = copy.deepcopy(cache)

    plan = select_plan(script_dir)

    # 3. Diff analysis (display only — statuses are re-computed inside converters)
    print_diff_analysis(files, cache)

    # 4. Convert
    separator()
    print(f"\n  {C.BOLD}Conversion en cours…{C.RESET}\n")

    if plan.split_mode:
        results = convert_split(files, cache, new_cache, script_dir)
    else:
        results = convert_merge(files, plan.merge_file, cache, new_cache)

    print(f"\r  {progress_bar(len(files), len(files))}  {'':30}\n")

    # 5. Display results & persist cache
    print_results(results, plan, script_dir)

    save_cache(cache_path, new_cache)
    print(f"\n  {C.DIM}💾  Cache mis à jour : {CACHE_FILENAME}{C.RESET}")
    print()
    separator("─", 58, C.CYAN)
    input(f"\n  {C.DIM}Appuie sur Entrée pour quitter…{C.RESET}  ")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {C.DIM}Interrompu.{C.RESET}\n")
    except Exception:
        print(f"\n  {C.RED}{'─' * 58}{C.RESET}")
        print(f"  {C.RED}{C.BOLD}✗  ERREUR — copie ce message et envoie-le{C.RESET}\n")
        traceback.print_exc()
        print(f"\n  {C.RED}{'─' * 58}{C.RESET}")
        input("\n  Appuie sur Entrée pour quitter…  ")
