"""
Recognizes which file of a PlanetScope order is the one used for RCR classification.

A Planet order delivers several files per capture, e.g.:
    20230705_162822_14_2480.json
    20230705_162822_14_2480_metadata.json
    20230705_162822_14_2480_3B_AnalyticMS_8b_metadata_clip.xml
    20230705_162822_14_2480_3B_udm2_clip.tif                       <- cloud/usable-data mask (NOT imagery)
    20230705_162822_14_2480_3B_AnalyticMS_SR_8b_harmonized_clip.tif <- the 8-band surface reflectance image we need

Only the surface reflectance (AnalyticMS_SR) GeoTIFF is kept. This module has no heavy
dependencies so it can be imported by both the Streamlit app and the engine.
"""

from __future__ import annotations

import io
import re
import shutil
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

TIF_EXTS = (".tif", ".tiff", ".geotif", ".geotiff")

# Planet asset names look like "<scene id>_3B_<asset>_clip.tif"; split on the product level marker.
_PLANET_ASSET_RE = re.compile(r"^(?P<scene>.+?)_(?:1b|3b|basic|ortho)_(?P<asset>.+)$", re.IGNORECASE)


def describe_planet_file(name: str) -> Tuple[bool, str]:
    """Returns (is_classification_raster, reason) for a single file name."""
    base = Path(name).name
    low = base.lower()
    if base.startswith("._") or "__macosx" in name.lower():
        return False, "system file"
    if not low.endswith(TIF_EXTS):
        if low.endswith(".json"):
            return False, "metadata (.json)"
        if low.endswith(".xml"):
            return False, "metadata (.xml)"
        return False, "not a GeoTIFF"
    if "udm" in low:
        return False, "cloud / usable-data mask (UDM2)"
    m = _PLANET_ASSET_RE.match(Path(low).stem)
    if m:
        asset = m.group("asset")
        if "analyticms_sr" not in asset and "analytic_sr" not in asset:
            if "visual" in asset:
                return False, "visual (RGB) product, not surface reflectance"
            return False, "not a surface reflectance (SR) product"
    # Planet SR image, or a GeoTIFF with non-Planet naming (e.g. renamed by the user)
    return True, "8-band surface reflectance image"


def is_planet_sr_raster(name: str) -> bool:
    return describe_planet_file(name)[0]


def _preference(name: str) -> Tuple[int, int]:
    low = name.lower()
    return ("8b" in low, "harmonized" in low)


def select_sr_rasters(names: Iterable[str]) -> List[str]:
    """
    Filters names down to the classification rasters. If a capture has more than one SR
    variant (e.g. 4-band and 8-band, or harmonized and non-harmonized), keeps the 8-band
    harmonized one.
    """
    best: Dict[str, str] = {}
    order: List[str] = []
    for name in names:
        if not is_planet_sr_raster(name):
            continue
        stem = Path(name).stem
        m = _PLANET_ASSET_RE.match(stem)
        key = m.group("scene").lower() if m else stem.lower()
        if key not in best:
            best[key] = name
            order.append(key)
        elif _preference(Path(name).name) > _preference(Path(best[key]).name):
            best[key] = name
    return [best[k] for k in order]


def iter_zip_members(zf: zipfile.ZipFile, depth: int = 0):
    """Yields (member_name, opener) for every file in a ZIP, descending one level into nested ZIPs."""
    for info in zf.infolist():
        if info.is_dir():
            continue
        if info.filename.lower().endswith(".zip") and depth < 1:
            try:
                # Left open (in memory) so callers can read members after iteration finishes
                inner = zipfile.ZipFile(io.BytesIO(zf.read(info)))
            except zipfile.BadZipFile:
                continue
            yield from iter_zip_members(inner, depth + 1)
        else:
            yield info.filename, (lambda z=zf, i=info: z.open(i))


def extract_sr_rasters_from_zip(zip_path: Path, dest_dir: Path) -> Tuple[List[Path], List[Tuple[str, str]]]:
    """
    Extracts only the classification rasters from a Planet order ZIP into dest_dir (flattened).
    Returns (extracted_paths, skipped) where skipped is a list of (file name, reason).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = list(iter_zip_members(zf))
        keep = set(select_sr_rasters(m for m, _ in members))
        extracted: List[Path] = []
        skipped: List[Tuple[str, str]] = []
        for member, opener in members:
            base = Path(member).name
            if member not in keep:
                ok, reason = describe_planet_file(member)
                if ok:
                    reason = "duplicate of a preferred 8-band harmonized image"
                if reason != "system file" and base.lower() != "manifest.json":
                    skipped.append((base, reason))
                continue
            target = dest_dir / base
            with opener() as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(target)
    return extracted, skipped
