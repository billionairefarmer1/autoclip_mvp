"""
Font resolution for burned-in captions.

libass fails *silently* when it cannot find the requested font family: you get
a clean-looking video with no text. That is a terrible failure mode for an
app meant to run on any PC, so we actively find a font that exists and tell
FFmpeg where it lives (``fontsdir``).

Strategy
--------
1. Honour an explicit font the user picked in settings, if it can be found.
2. Otherwise pick a platform-appropriate bold sans-serif family.
3. Verify it by scanning known font directories (no fontconfig dependency,
   because Windows/macOS do not have it and Linux containers often do not).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# (font file, family name) - ordered by preference.
_FONT_CANDIDATES = {
    "Windows": [
        ("C:/Windows/Fonts/arialbd.ttf", "Arial"),
        ("C:/Windows/Fonts/ariblk.ttf", "Arial Black"),
        ("C:/Windows/Fonts/segoeuib.ttf", "Segoe UI"),
        ("C:/Windows/Fonts/tahomabd.ttf", "Tahoma"),
        ("C:/Windows/Fonts/verdanab.ttf", "Verdana"),
    ],
    "Darwin": [
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "Arial"),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", "Arial"),
        ("/System/Library/Fonts/Helvetica.ttc", "Helvetica"),
        ("/System/Library/Fonts/SFNS.ttf", "SF Pro Text"),
        ("/Library/Fonts/Arial Bold.ttf", "Arial"),
    ],
    "Linux": [
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVu Sans"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVu Sans"),
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "Liberation Sans"),
        ("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf", "Liberation Sans"),
        ("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", "DejaVu Sans"),
        ("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", "DejaVu Sans"),
        ("/usr/local/share/fonts/DejaVuSans-Bold.ttf", "DejaVu Sans"),
    ],
}

_FONT_DIRS = {
    "Windows": ["C:/Windows/Fonts", os.path.expandvars("%LOCALAPPDATA%/Microsoft/Windows/Fonts")],
    "Darwin": ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
               os.path.expanduser("~/Library/Fonts")],
    "Linux": [
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.fonts"),
        os.path.expanduser("~/.local/share/fonts"),
    ],
}


@dataclass
class ResolvedFont:
    family: str
    fontsdir: Optional[str] = None
    file: Optional[str] = None

    def to_dict(self) -> dict:
        return {"family": self.family, "fontsdir": self.fontsdir, "file": self.file}


def _platform() -> str:
    if os.name == "nt":
        return "Windows"
    if os.uname().sysname == "Darwin" if hasattr(os, "uname") else False:
        return "Darwin"
    return "Linux"


def resolve_font(preferred: Optional[str] = None, prefer_bold: bool = True) -> ResolvedFont:
    """Find a caption font that is guaranteed to render."""
    import platform as _pf

    system = _pf.system()
    candidates = list(_FONT_CANDIDATES.get(system, _FONT_CANDIDATES["Linux"]))

    # If the user asked for something specific, try to locate its files first.
    if preferred:
        found = _find_family(preferred)
        if found:
            return found
        logger.info(
            "Caption font '%s' was not found on this system; falling back to a bundled family",
            preferred,
        )

    for path, family in candidates:
        candidate = Path(path)
        if candidate.is_file():
            fontsdir = str(candidate.parent)
            return ResolvedFont(family=family, fontsdir=fontsdir, file=str(candidate))

    # Nothing from the list exists: ask fontconfig as a last resort.
    famdir = _fontconfig_family()
    if famdir:
        return famdir

    logger.warning(
        "No caption font found. Captions may render as empty boxes; install a font "
        "(e.g. DejaVu Sans) or set caption_font in settings."
    )
    return ResolvedFont(family=preferred or "Arial", fontsdir=None)


def _find_family(family: str) -> Optional[ResolvedFont]:
    """Locate a family by scanning font directories for a matching file name."""
    needles = {family.lower().replace(" ", "").replace("-", "")}
    aliases = {
        "arial": {"arial", "ariblk"},
        "helvetica": {"helvetica", "helveticaneue"},
        "dejavusans": {"dejavusans"},
        "liberationsans": {"liberationsans"},
        "segoeui": {"segoeui"},
        "impact": {"impact"},
        "montserrat": {"montserrat"},
        "roboto": {"roboto"},
        "inter": {"inter"},
        "opensans": {"opensans"},
    }
    needles |= aliases.get(next(iter(needles)), set())

    for directory in _FONT_DIRS.get(_platform(), []):
        base = Path(directory)
        if not base.is_dir():
            continue
        try:
            for font in base.rglob("*.[to]tf"):
                stem = font.stem.lower().replace(" ", "").replace("-", "")
                if any(stem.startswith(needle) for needle in needles):
                    return ResolvedFont(family=family, fontsdir=str(font.parent), file=str(font))
        except (OSError, PermissionError):  # pragma: no cover - hostile filesystems
            continue
    return None


def _fontconfig_family() -> Optional[ResolvedFont]:
    """Ask fontconfig for any installed font family."""
    if not shutil.which("fc-match"):
        return None
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{family}\n%{file}", "sans-serif"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        if len(lines) >= 2 and Path(lines[1]).is_file():
            family = lines[0].split(",")[0].strip()
            return ResolvedFont(family=family, fontsdir=str(Path(lines[1]).parent), file=lines[1])
    except Exception:  # noqa: BLE001
        pass
    return None


def font_directories() -> List[str]:
    """All font directories that exist on this machine."""
    found = []
    for directory in _FONT_DIRS.get(_platform(), []):
        if directory and Path(directory).is_dir():
            found.append(directory)
    return found


def ass_fontsdir_option(fontsdir: Optional[str]) -> str:
    """Format the ``fontsdir`` option for FFmpeg's ass filter."""
    if not fontsdir:
        return ""
    value = str(fontsdir).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return f":fontsdir='{value}'"
