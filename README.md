# PNGtoHEX (ch2hp)

Converts image and font files into embeddable C++ header files (`.hpp`).

Each asset is written as a `const unsigned char` array and a matching `_len` constant, ready to include directly in any C++ project — no file system required at runtime.

## Supported formats

Images: PNG, JPG, JPEG, BMP, ICO, GIF  
Fonts: TTF, OTF, WOFF, WOFF2

## Requirements

Python 3. No third-party dependencies.

## Usage

```
python convert.py [file1 file2 ...]
python convert.py --help
```

If no files are passed as arguments, the script prompts for drag-and-drop input (Windows CMD compatible, handles quoted paths with spaces).

## Output modes

The script asks interactively:

**1. Merged** — all assets combined into a single file (default: `assets_bin.hpp`)  
**2. Split** — one `.hpp` per asset, named after the generated C++ variable

## Generated output

```cpp
// icon.png (1,240 bytes)
const unsigned char icon[] = {
    0x89, 0x50, 0x4e, 0x47, ...
};
const unsigned int icon_len = 1240;
```

Variable names are derived from filenames. Font files preserve family name and variant (e.g. `Roboto-BoldItalic.ttf` → `Roboto_BoldItalic`). Each generated file includes a proper `#ifndef` include guard.

## Smart diffing

The script caches an MD5 hash of every processed file in `.convert_cache.json`. On subsequent runs, unchanged files are skipped (or reused from cache in merged mode), so only modified or new assets are re-converted.

## Notes

- The CLI is in French.
- To target a specific directory rather than dropping files, pass paths as arguments.
- The cache file is written next to `convert.py` and can be deleted to force a full rebuild.
