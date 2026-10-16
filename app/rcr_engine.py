"""
rcr_engine.py
=============
Core backend engine for the Zero-Experience RCR Field Inspection Platform.
Integrates:
  1. Field polygon loading & validation (.gpkg, .geojson, .kml, .shp/.zip)
  2. Google Earth Engine Sentinel-2 NDVI season progress curve extraction
     -> Identifies Peak Canopy Date (t_peak) & 80% Senescence Target Date (t_0),
        plus recommended Before (t_-1) and After (t_+1) scouting dates.
  3. Dual PlanetScope Image Acquisition:
     - Option A: Personal Planet API Key (Data API catalog search + Orders API AOI clip & download)
     - Option B: Manual File Upload / Local Directory Auto-Matching for (t_peak, t_-1, t_0, t_+1)
  4. Multi-Temporal RCR Classification (Early 3-sigma canopy mask + 13 normalized features
     + L2 Logistic Regression production model) & visual report generation.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# Ensure Conda DLL paths (Library\bin), GDAL_DATA, PROJ_LIB, and SHAPE_RESTORE_SHX are set
_ENV_PREFIX = Path(sys.executable).resolve().parent
_CONDA_DLL_DIRS = [
    _ENV_PREFIX,
    _ENV_PREFIX / "Library" / "mingw-w64" / "bin",
    _ENV_PREFIX / "Library" / "usr" / "bin",
    _ENV_PREFIX / "Library" / "bin",
    _ENV_PREFIX / "Scripts",
    _ENV_PREFIX / "bin",
]
_existing_dll_dirs = [str(d) for d in _CONDA_DLL_DIRS if d.exists()]
os.environ["PATH"] = os.pathsep.join(_existing_dll_dirs + [os.environ.get("PATH", "")])
if hasattr(os, "add_dll_directory"):
    for _d_str in _existing_dll_dirs:
        try:
            os.add_dll_directory(_d_str)
        except Exception:
            pass

_GDAL_CANDIDATE = _ENV_PREFIX / "Library" / "share" / "gdal"
_PROJ_CANDIDATE = _ENV_PREFIX / "Library" / "share" / "proj"
if _GDAL_CANDIDATE.exists():
    os.environ["GDAL_DATA"] = str(_GDAL_CANDIDATE)
if _PROJ_CANDIDATE.exists():
    os.environ["PROJ_LIB"] = str(_PROJ_CANDIDATE)

os.environ["SHAPE_RESTORE_SHX"] = "YES"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["MPLBACKEND"] = "Agg"

import geopandas as gpd
import joblib
import matplotlib
matplotlib.use("Agg", force=True)
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap, ListedColormap
from matplotlib.figure import Figure
import numpy as np
import pandas as pd
import rasterio
from rasterio.mask import mask
from rasterio.warp import Resampling, reproject
import requests
import shapely
from shapely.geometry import box, shape
from shapely.ops import unary_union

from planet_files import extract_sr_rasters_from_zip, select_sr_rasters

# ==============================================================================
# 1. PATHS, PALETTES, FONTS & CONSTANTS
# ==============================================================================

APP_DIR = Path(__file__).resolve().parent
STANDALONE_ROOT = APP_DIR.parent
FONTS_DIR = APP_DIR / "fonts"
FONTS_DIR.mkdir(parents=True, exist_ok=True)


def _setup_montserrat_font() -> None:
    """
    Ensures the Montserrat font family is available to Matplotlib for all figures.
    Downloads and caches official Montserrat .ttf weights into `platform/fonts/`
    if not already present on disk, then registers them with matplotlib.font_manager.
    """
    font_urls = {
        "Montserrat-Regular.ttf": "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-Regular.ttf",
        "Montserrat-Medium.ttf": "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-Medium.ttf",
        "Montserrat-SemiBold.ttf": "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-SemiBold.ttf",
        "Montserrat-Bold.ttf": "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-Bold.ttf",
    }
    for fname, url in font_urls.items():
        fpath = FONTS_DIR / fname
        if not fpath.exists() or fpath.stat().st_size < 10000:
            try:
                r = requests.get(url, timeout=8)
                if r.status_code == 200 and len(r.content) > 10000:
                    fpath.write_bytes(r.content)
            except Exception:
                pass
        if fpath.exists() and fpath.stat().st_size > 10000:
            try:
                font_manager.fontManager.addfont(str(fpath))
            except Exception:
                pass

    matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["font.sans-serif"] = ["Montserrat", "Segoe UI", "Arial", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    matplotlib.rcParams["figure.dpi"] = 500
    matplotlib.rcParams["savefig.dpi"] = 500


_setup_montserrat_font()

MODEL_PATH = STANDALONE_ROOT / "model" / "rcrc_3_1_probability_model.joblib"
WORKSPACE_DIR = STANDALONE_ROOT / "workspace"
SAT_BASE_DIR = WORKSPACE_DIR / "satellite_imagery"
VECTORS_DIR = WORKSPACE_DIR / "field_boundaries"
SAMPLE_VECTORS_DIR = STANDALONE_ROOT / "sample_data"
OUTPUT_BASE = WORKSPACE_DIR / "outputs"
DIR_CLASSIFIED = OUTPUT_BASE / "classifications"
DIR_INDICES = OUTPUT_BASE / "visual_indices"
DIR_REPORTS = OUTPUT_BASE / "reports"

for _d in [DIR_CLASSIFIED, DIR_INDICES, DIR_REPORTS, SAT_BASE_DIR, VECTORS_DIR, SAMPLE_VECTORS_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

DEFAULT_GEE_PROJECT = (
    os.environ.get("EARTHENGINE_PROJECT")
    or os.environ.get("GEE_PROJECT")
    or ("ee-brunodp385" if os.environ.get("USERNAME", "").lower() == "brunodp" else "")
)
OPTIMAL_THRESHOLD = 0.48
TARGET_DROP_RATIO = 0.80  # 80% of peak canopy NDVI

# University of Illinois Urbana-Champaign Official Colors
ILLINI_BLUE = "#13294B"
ILLINI_BLUE_ACCENT = "#1D58A7"
ILLINI_ORANGE = "#FF5F05"
ILLINI_ORANGE_LIGHT = "#F59E0B"
ILLINI_ORANGE_DARK = "#C2410C"

# Custom Peach Palette (from qgis_import_and_stylish.py & qgis_load_rcr_rasters.py)
PEACH_PROB_PALETTE = [
    (0.0, "#FCE3CD"),
    (0.2, "#F5C09A"),
    (0.4, "#EE9973"),
    (0.6, "#E5734F"),
    (0.8, "#DE543E"),
    (1.0, "#D93836"),
]
DISC_HEALTHY_HEX = "#FCE3CD"
DISC_DISEASED_HEX = "#D93836"

FEATURE_NAMES = [
    "NormNDVI", "NormGNDVI", "NormNDRE", "NormEVI",
    "NormCI", "NormCIRe", "NormCSI",
    "Normb6", "Normb8", "Normb7", "Normb4", "Normb2", "Normb5"
]

PLANET_DATA_API_URL = "https://api.planet.com/data/v1/quick-search"
PLANET_ORDERS_API_URL = "https://api.planet.com/compute/ops/orders/v2"
PLANET_SCENE_TILES_URL = "https://tiles.planet.com/data/v1/PSScene/{scene_id}/{z}/{x}/{y}.png"


# ==============================================================================
# 2. POLYGON LOADING & GEOMETRY HELPERS
# ==============================================================================

def load_uploaded_or_local_polygon(
    file_path_or_bytes,
    filename: str = "uploaded.gpkg",
    field_id: Optional[str] = None
) -> Tuple[gpd.GeoDataFrame, str, float, float]:
    """
    Loads a field boundary polygon from a file path, raw bytes, list of uploaded
    files (e.g. .shp + .shx + .dbf + .prj), standalone .shp file, or .zip archive.
    Strips 3D Z-coordinates and unifies multipart features into a clean 2D geometry.
    Returns:
      (gdf_wgs84, resolved_field_id, area_ha, area_acres)
    """
    os.environ["SHAPE_RESTORE_SHX"] = "YES"

    if isinstance(file_path_or_bytes, (str, Path)):
        path = Path(file_path_or_bytes)
        gdf = _read_vector_path(path, field_id)
        inferred_id = field_id or _infer_field_id_from_name(path.stem)
    else:
        tmpdir = Path(tempfile.mkdtemp(prefix="rcr_poly_"))
        try:
            uploaded_list = file_path_or_bytes if isinstance(file_path_or_bytes, list) else [file_path_or_bytes]
            for item in uploaded_list:
                fname = Path(getattr(item, "name", filename)).name
                dest = tmpdir / fname
                if hasattr(item, "getvalue"):
                    raw_bytes = item.getvalue()
                elif hasattr(item, "read"):
                    if hasattr(item, "seek"):
                        item.seek(0)
                    raw_bytes = item.read()
                    if hasattr(item, "seek"):
                        item.seek(0)
                else:
                    raw_bytes = item
                dest.write_bytes(raw_bytes)

                if fname.lower().endswith(".zip"):
                    with zipfile.ZipFile(dest, "r") as zf:
                        zf.extractall(tmpdir)

            candidates = (
                list(tmpdir.rglob("*.gpkg"))
                + list(tmpdir.rglob("*.geojson"))
                + list(tmpdir.rglob("*.json"))
                + list(tmpdir.rglob("*.kml"))
                + list(tmpdir.rglob("*.shp"))
            )
            if not candidates:
                raise ValueError(
                    "Please upload a .shp, .gpkg, .geojson, .kml, or .zip file containing a polygon."
                )

            primary_vec = candidates[0]
            gdf = _read_vector_path(primary_vec, field_id)
            inferred_id = field_id or _infer_field_id_from_name(primary_vec.stem)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # Smart CRS detection if missing (e.g. standalone .shp uploaded without .prj)
    if gdf.crs is None:
        minx, miny, maxx, maxy = gdf.total_bounds
        if abs(minx) <= 180.0 and abs(maxx) <= 180.0 and abs(miny) <= 90.0 and abs(maxy) <= 90.0:
            gdf = gdf.set_crs("EPSG:4326")
        else:
            gdf = gdf.set_crs("EPSG:32615")

    # Convert to pure 2D geometry (strips Z dimension) and filter to polygon geometries only
    # (safely ignoring points, waypoints, lines that may exist in Google Earth KML files)
    poly_geoms = [
        geom for geom in shapely.force_2d(gdf.geometry.values)
        if geom.geom_type in ("Polygon", "MultiPolygon") and not geom.is_empty
    ]
    if not poly_geoms:
        raise ValueError(
            f"No polygon boundaries found in the uploaded file. "
            "Please ensure your KML or shapefile contains a closed polygon."
        )

    clean_geom_2d = unary_union(poly_geoms)
    gdf_clean = gpd.GeoDataFrame({"field_id": [inferred_id]}, geometry=[clean_geom_2d], crs=gdf.crs)

    gdf_wgs84 = gdf_clean.to_crs("EPSG:4326")
    try:
        utm_crs = gdf_wgs84.estimate_utm_crs()
        gdf_utm = gdf_wgs84.to_crs(utm_crs)
    except Exception:
        gdf_utm = gdf_wgs84.to_crs("EPSG:32615")
    area_m2 = float(gdf_utm.geometry.area.sum())
    area_ha = round(area_m2 / 10000.0, 2)
    area_acres = round(area_ha * 2.47105, 2)

    return gdf_wgs84, inferred_id, area_ha, area_acres


def _expand_view_bounds(
    dst_bounds: Tuple[float, float, float, float],
    pad_frac: float = 0.10,
    target_aspect: Optional[float] = None,
) -> Tuple[float, float, float, float]:
    """
    Pads `dst_bounds = (xmin, ymin, xmax, ymax)` (projected metres) by `pad_frac` and, if requested,
    symmetrically expands them to a `target_aspect` (width/height). Returns `(xmin, ymin, xmax, ymax)`.
    """
    xmin, ymin, xmax, ymax = dst_bounds
    w_field = max(10.0, float(xmax - xmin))
    h_field = max(10.0, float(ymax - ymin))

    pad_x = max(10.0, w_field * pad_frac)
    pad_y = max(10.0, h_field * pad_frac)
    b_xmin = xmin - pad_x
    b_xmax = xmax + pad_x
    b_ymin = ymin - pad_y
    b_ymax = ymax + pad_y

    # If a target aspect ratio is specified, symmetrically expand bounds so the view fills the wide frame
    if target_aspect is not None and target_aspect > 0:
        cur_w = b_xmax - b_xmin
        cur_h = b_ymax - b_ymin
        cur_aspect = cur_w / max(1e-6, cur_h)
        if cur_aspect < target_aspect:
            # Field is squarer/narrower than target aspect: expand horizontally
            desired_w = cur_h * target_aspect
            diff_x = (desired_w - cur_w) / 2.0
            b_xmin -= diff_x
            b_xmax += diff_x
        elif cur_aspect > target_aspect:
            # Field is wider than target aspect: expand vertically
            desired_h = cur_w / target_aspect
            diff_y = (desired_h - cur_h) / 2.0
            b_ymin -= diff_y
            b_ymax += diff_y

    return b_xmin, b_ymin, b_xmax, b_ymax


def _deg2tile(lat_deg: float, lon_deg: float, zoom: int) -> Tuple[int, int]:
    """Converts WGS84 lat/lon to Web Mercator XYZ tile indices at `zoom`."""
    import math

    lat_rad = math.radians(max(-85.0511, min(85.0511, lat_deg)))
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return xtile, ytile


def _fetch_xyz_mosaic_in_memory(
    fetch_tile: Callable[[int, int, int], Optional[np.ndarray]],
    dst_crs,
    view_bounds: Tuple[float, float, float, float],
    n_bands: int = 3,
    zoom_candidates: Tuple[int, ...] = (18, 17, 16, 15, 14),
    max_tiles: int = 28,
    dst_w: int = 1440,
) -> np.ndarray:
    """
    Generic in-memory XYZ mosaic: picks the highest zoom in `zoom_candidates` needing at most
    `max_tiles` tiles to cover `view_bounds` (in `dst_crs`), fetches them concurrently with
    `fetch_tile(z, x, y) -> HxWx n_bands uint8 array | None`, and reprojects the mosaic to `dst_crs`.
    Returns a `(n_bands, dst_h, dst_w)` uint8 array (missing tiles stay zero).
    """
    import concurrent.futures
    from rasterio.transform import from_origin, from_bounds
    from rasterio.warp import transform_bounds

    b_xmin, b_ymin, b_xmax, b_ymax = view_bounds
    lon_min, lat_min, lon_max, lat_max = transform_bounds(
        dst_crs, "EPSG:4326", b_xmin, b_ymin, b_xmax, b_ymax, densify_pts=21
    )

    chosen_z = zoom_candidates[-1]
    for z_cand in zoom_candidates:
        x0, y0 = _deg2tile(lat_max, lon_min, z_cand)
        x1, y1 = _deg2tile(lat_min, lon_max, z_cand)
        n_tiles = (abs(x1 - x0) + 1) * (abs(y1 - y0) + 1)
        if n_tiles <= max_tiles:
            chosen_z = z_cand
            break

    x_min, y_min = _deg2tile(lat_max, lon_min, chosen_z)
    x_max, y_max = _deg2tile(lat_min, lon_max, chosen_z)
    if x_min > x_max:
        x_min, x_max = x_max, x_min
    if y_min > y_max:
        y_min, y_max = y_max, y_min

    n_cols_t = x_max - x_min + 1
    n_rows_t = y_max - y_min + 1
    mosaic = np.zeros((n_bands, n_rows_t * 256, n_cols_t * 256), dtype=np.uint8)

    tile_coords = [
        (tx, ty)
        for ty in range(y_min, y_max + 1)
        for tx in range(x_min, x_max + 1)
    ]

    def _fetch_one_tile(coord):
        tx, ty = coord
        try:
            return tx, ty, fetch_tile(chosen_z, tx, ty)
        except Exception:
            return tx, ty, None

    # Fetch tiles concurrently across threads for rapid mosaic generation
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_fetch_one_tile, tile_coords))

    for tx, ty, arr in results:
        if arr is not None and arr.shape[:2] == (256, 256):
            r0 = (ty - y_min) * 256
            c0 = (tx - x_min) * 256
            mosaic[:, r0 : r0 + 256, c0 : c0 + 256] = np.transpose(arr[:, :, :n_bands], (2, 0, 1))

    origin_shift = 20037508.342789244
    res_3857 = (2.0 * origin_shift) / (256.0 * (2.0 ** chosen_z))
    west_3857 = x_min * 256.0 * res_3857 - origin_shift
    north_3857 = origin_shift - y_min * 256.0 * res_3857
    src_transform = from_origin(west_3857, north_3857, res_3857, res_3857)

    aspect = (b_ymax - b_ymin) / max(1e-6, (b_xmax - b_xmin))
    dst_h = max(240, min(1440, int(round(dst_w * aspect))))
    dst_transform = from_bounds(b_xmin, b_ymin, b_xmax, b_ymax, dst_w, dst_h)

    warped = np.zeros((n_bands, dst_h, dst_w), dtype=np.uint8)
    for band_idx in range(n_bands):
        reproject(
            source=mosaic[band_idx],
            destination=warped[band_idx],
            src_transform=src_transform,
            src_crs="EPSG:3857",
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear,
        )
    return warped


def _fetch_google_satellite_basemap_in_memory(
    dst_crs,
    dst_bounds: Tuple[float, float, float, float],
    pad_frac: float = 0.10,
    target_aspect: Optional[float] = None,
) -> Optional[Tuple[np.ndarray, List[float]]]:
    """
    Fetches Google Satellite tiles (`mt1.google.com/vt/lyrs=s`) purely in RAM (`io.BytesIO`,
    zero files saved to disk) for `dst_bounds = (xmin, ymin, xmax, ymax)` in `dst_crs`,
    optionally expanding to a `target_aspect` (width/height ratio such as 16/9) for panoramic view,
    reprojects the mosaic in memory to `dst_crs`, and returns `(rgb_hwc_uint8, [xmin, xmax, ymin, ymax])`.
    """
    from PIL import Image

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    def _fetch_google_tile(z: int, tx: int, ty: int) -> Optional[np.ndarray]:
        url = f"https://mt1.google.com/vt/lyrs=s&x={tx}&y={ty}&z={z}"
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            return np.asarray(img, dtype=np.uint8)
        return None

    try:
        b_xmin, b_ymin, b_xmax, b_ymax = _expand_view_bounds(dst_bounds, pad_frac, target_aspect)
        warped = _fetch_xyz_mosaic_in_memory(
            _fetch_google_tile, dst_crs, (b_xmin, b_ymin, b_xmax, b_ymax), n_bands=3
        )
        rgb_hwc = np.transpose(warped, (1, 2, 0))
        return rgb_hwc, [b_xmin, b_xmax, b_ymin, b_ymax]
    except Exception:
        return None


def plot_boundary_preview(poly_gdf: gpd.GeoDataFrame, field_id: str) -> Figure:
    """High-resolution panoramic 16:9 boundary preview plot overlaid on in-memory Google Satellite imagery."""
    # 16:9 widescreen aspect ratio (1.778)
    target_aspect = 16.0 / 9.0
    fig_w = 11.0
    fig_h = round(fig_w / target_aspect, 3)  # ~6.188 inches
    fig = Figure(figsize=(fig_w, fig_h), dpi=300, facecolor="#FFFFFF")
    ax = fig.subplots()
    ax.set_facecolor("#13294B")

    try:
        utm_crs = poly_gdf.estimate_utm_crs() or "EPSG:32615"
    except Exception:
        utm_crs = "EPSG:32615"
    gdf_utm = poly_gdf.to_crs(utm_crs)
    geom = gdf_utm.geometry.iloc[0]
    xmin, ymin, xmax, ymax = gdf_utm.total_bounds

    sat_res = _fetch_google_satellite_basemap_in_memory(
        utm_crs, (xmin, ymin, xmax, ymax), pad_frac=0.15, target_aspect=target_aspect
    )
    if sat_res is not None:
        sat_rgb, sat_extent = sat_res
        ax.imshow(sat_rgb, extent=sat_extent, origin="upper", interpolation="bilinear", zorder=1)
        ax.set_xlim(sat_extent[0], sat_extent[1])
        ax.set_ylim(sat_extent[2], sat_extent[3])
    else:
        ax.set_aspect("equal", adjustable="datalim")

    polys = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", [geom]))
    for p in polys:
        if hasattr(p, "exterior") and p.exterior is not None:
            x, y = p.exterior.xy
            # High-visibility filled polygon with Illini Orange overlay
            ax.fill(x, y, facecolor=ILLINI_ORANGE, edgecolor="none", alpha=0.20, zorder=2)
            # High-contrast crisp white halo boundary
            ax.plot(x, y, color="#FFFFFF", linewidth=3.6, alpha=0.95, zorder=3)
            # Illini orange primary boundary stroke
            ax.plot(x, y, color=ILLINI_ORANGE, linewidth=2.0, zorder=4)

    ax.set_title(
        f"Field boundary · {field_id}",
        fontsize=11.5,
        fontweight="600",
        color=ILLINI_BLUE,
        pad=9,
    )
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_edgecolor("#DCE2EC")
        spine.set_linewidth(1.2)
    fig.tight_layout(pad=0.25)
    return fig


def _infer_field_id_from_name(stem: str) -> str:
    clean = re.sub(r"^(boun[d]?|boundary|field)[_-]?", "", stem, flags=re.IGNORECASE)
    return clean.strip().upper() if clean.strip() else "FIELD_1"


def _is_null_or_corrupt_shapefile(shp_path: Path) -> bool:
    """
    Checks if an uploaded .shp file is empty or filled with null bytes (0x00),
    which happens on Windows when dragging an unhydrated OneDrive 'Files On-Demand'
    cloud stub directly into the browser before OneDrive downloads it locally.
    Valid ESRI .shp files always begin with big-endian magic number 9994 (0x0000270A).
    """
    if not shp_path.exists() or shp_path.stat().st_size < 100:
        return True
    head = shp_path.read_bytes()[:16]
    return head[:4] != b"\x00\x00'\n"


def _read_vector_path(path: Path, field_id: Optional[str] = None) -> gpd.GeoDataFrame:
    os.environ["SHAPE_RESTORE_SHX"] = "YES"
    try:
        import pyogrio
        pyogrio.set_gdal_config_options({"SHAPE_RESTORE_SHX": "YES"})
    except Exception:
        pass

    if path.suffix.lower() == ".shp":
        stem = path.stem
        clean_fid = _infer_field_id_from_name(stem)
        candidate_stems = [
            stem,
            f"Boun_{clean_fid}",
            f"boun_{clean_fid}",
            clean_fid,
            f"field_{clean_fid}",
            f"Field_{clean_fid}",
            f"boundary_{clean_fid}",
        ]

        search_folders = [
            VECTORS_DIR,
            SAMPLE_VECTORS_DIR,
            STANDALONE_ROOT.parent / "rcrc" / "data" / "raw" / "vectors" / "field_boudaries",
            STANDALONE_ROOT.parent / "rcrc" / "data" / "raw" / "vectors" / "field_boundaries",
            Path.home() / "OneDrive - University of Illinois - Urbana" / "Projects" / "rcrc" / "data" / "raw" / "vectors" / "field_boudaries",
            Path.home() / "OneDrive - University of Illinois - Urbana" / "Remote Sensing at UIUC" / "RCR - Soybeans" / "Polygons - RCR Fields",
        ]

        found_valid_source = False
        for search_folder in search_folders:
            if not search_folder.exists():
                continue
            for cand_stem in candidate_stems:
                direct_cand = search_folder / f"{cand_stem}.shp"
                cands = [direct_cand] if direct_cand.exists() else []
                if not cands and search_folder.is_dir():
                    try:
                        cands = list(search_folder.glob(f"{cand_stem}.shp"))
                    except Exception:
                        pass
                for cand_shp in cands:
                    if not _is_null_or_corrupt_shapefile(cand_shp):
                        for ext in [".shp", ".prj", ".shx", ".dbf", ".cpg"]:
                            src_side = cand_shp.with_suffix(ext)
                            dst_side = path.with_suffix(ext)
                            if src_side.exists():
                                if not dst_side.exists() or (ext == ".shp" and _is_null_or_corrupt_shapefile(dst_side)):
                                    shutil.copy2(src_side, dst_side)
                                elif ext != ".shp":
                                    try:
                                        sb = dst_side.read_bytes()
                                        if len(sb) == 0 or all(b == 0 for b in sb):
                                            shutil.copy2(src_side, dst_side)
                                    except Exception:
                                        pass
                        found_valid_source = True
                        break
                if found_valid_source:
                    break
            if found_valid_source:
                break

        for side_ext in [".shx", ".prj", ".dbf", ".cpg"]:
            side_p = path.with_suffix(side_ext)
            if side_p.exists():
                try:
                    sb = side_p.read_bytes()
                    if len(sb) == 0 or all(b == 0 for b in sb):
                        side_p.unlink()
                except Exception:
                    pass

        if _is_null_or_corrupt_shapefile(path):
            raise ValueError(
                f"The file '{path.name}' is empty or contains zero placeholder bytes (0x00). "
                "This typically happens when Windows or OneDrive has not finished downloading the file locally, "
                "or when dragging an unhydrated cloud placeholder into the browser. "
                "Please verify that the shapefile is downloaded, upload a .zip containing the full shapefile (.shp, .shx, .dbf, .prj), "
                "or select the field directly from the Field Library."
            )

    gdf = gpd.read_file(path)
    if gdf.empty:
        raise ValueError(f"Vector file '{path.name}' contains no geometries.")

    if len(gdf) == 1:
        return gdf.iloc[[0]].copy()

    if field_id:
        id_candidates = ["field_id", "field", "name", "id", "fid", "plot_id"]
        matched_col = next((c for c in gdf.columns if c.lower() in id_candidates), None)
        if matched_col:
            matches = gdf[gdf[matched_col].astype(str).str.upper() == field_id.upper()]
            if matches.empty:
                alt = field_id.replace("-", "_") if "-" in field_id else field_id.replace("_", "-")
                matches = gdf[gdf[matched_col].astype(str).str.upper() == alt.upper()]
            if not matches.empty:
                return matches.copy()

    return gdf.copy()


def list_demo_vector_files() -> List[Path]:
    """Lists available sample field boundary files in sample_data/ or workspace/field_boundaries/."""
    files = []
    for folder in [SAMPLE_VECTORS_DIR, VECTORS_DIR]:
        if folder.exists():
            for ext in ("*.gpkg", "*.shp", "*.geojson"):
                files.extend(folder.glob(ext))
    return sorted(
        [f for f in files if "routine_field_boundaries" not in f.name.lower()],
        key=lambda p: p.name.lower()
    )


# ==============================================================================
# 3. STEP 1: SENTINEL-2 PHENOLOGY & CLASSIFICATION WINDOW (GEE)
# ==============================================================================

def extract_sentinel2_phenology(
    poly_gdf: gpd.GeoDataFrame,
    field_id: str,
    year: int,
    target_drop_ratio: float = TARGET_DROP_RATIO,
    gee_project: str = DEFAULT_GEE_PROJECT,
    use_cached_if_available: bool = True
) -> Dict:
    """
    Extracts Sentinel-2 NDVI time series from May 15 to Oct 15 for the given year.
    Identifies along the continuous daily trajectory line:
      - peak_date (t_peak): Peak canopy date for baseline canopy mask
      - target_date (t_0): Post-peak date where continuous curve crosses 80% of peak NDVI
      - before_date: Optimal target minus 5 days (t_0 - 5 days) on the continuous curve
      - after_date: Optimal target plus 5 days (t_0 + 5 days) on the continuous curve
    """
    cache_csv = DIR_REPORTS / f"{field_id}_{year}_phenology_curve.csv"
    source_used = "Google Earth Engine (live Sentinel-2)"

    df = None
    if use_cached_if_available and cache_csv.exists():
        try:
            df = pd.read_csv(cache_csv)
            df["date"] = pd.to_datetime(df["date"])
            source_used = f"Cached Sentinel-2 curve ({cache_csv.name})"
        except Exception:
            df = None

    if df is None:
        import ee
        proj_clean = (gee_project or "").strip() or None
        try:
            sa_email = os.environ.get("EARTHENGINE_SERVICE_ACCOUNT", "").strip()
            sa_key = os.environ.get("EARTHENGINE_PRIVATE_KEY", "").strip()
            creds_json = os.environ.get("EARTHENGINE_CREDENTIALS_JSON", "").strip()
            if sa_email and sa_key:
                sa_key_formatted = sa_key.replace("\\n", "\n")
                credentials = ee.ServiceAccountCredentials(sa_email, key_data=sa_key_formatted)
                ee.Initialize(credentials, project=proj_clean)
            elif creds_json:
                import tempfile
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
                    tf.write(creds_json)
                    tf_path = tf.name
                credentials = ee.ServiceAccountCredentials(None, key_file=tf_path)
                ee.Initialize(credentials, project=proj_clean)
            elif proj_clean:
                ee.Initialize(project=proj_clean)
            else:
                ee.Initialize()
        except Exception as init_err:
            err_msg = str(init_err)
            if "Not signed up for Earth Engine" in err_msg or "project is not registered" in err_msg or "403" in err_msg:
                proj_hint = f" '{proj_clean}'" if proj_clean else ""
                raise RuntimeError(
                    f"Google Earth Engine connection error for project{proj_hint}:\n\n"
                    f"Details: {err_msg}\n\n"
                    "How to resolve this:\n"
                    "1. Enter your personal Google Earth Engine Project ID in the left sidebar settings "
                    "(e.g., 'ee-yourusername' or your Google Cloud Project ID).\n"
                    "2. If you don't have an Earth Engine account yet, register for free at https://code.earthengine.google.com.\n"
                    "3. For local Windows: run 'authenticate_earth_engine.bat' to authorize.\n"
                    "   For Streamlit Cloud: set EARTHENGINE_SERVICE_ACCOUNT and EARTHENGINE_PRIVATE_KEY in Secrets.\n\n"
                    "💡 Note: Step 2 is completely optional! If you already have PlanetScope imagery, "
                    "you can skip directly to Step 3 (Stage Imagery) and Step 4 (RCR Classification)."
                ) from init_err
            elif "credentials" in err_msg.lower() or "auth" in err_msg.lower():
                raise RuntimeError(
                    f"Google Earth Engine credentials not found or expired:\n\n"
                    f"Details: {err_msg}\n\n"
                    "For local Windows: double-click 'authenticate_earth_engine.bat' to authorize.\n"
                    "For Streamlit Cloud: configure EARTHENGINE_SERVICE_ACCOUNT & EARTHENGINE_PRIVATE_KEY in Secrets.\n\n"
                    "💡 Note: Step 2 is completely optional if you already have PlanetScope imagery!"
                ) from init_err
            else:
                raise RuntimeError(
                    f"Google Earth Engine initialization failed:\n\n"
                    f"Details: {err_msg}\n\n"
                    "Please verify your Google Earth Engine project ID in the sidebar settings or run 'authenticate_earth_engine.bat'."
                ) from init_err

        poly_geojson = poly_gdf.to_crs("EPSG:4326").geometry.iloc[0].__geo_interface__
        ee_poly = ee.Geometry(poly_geojson)
        start_date = f"{year}-05-15"
        end_date = f"{year}-10-15"

        def mask_s2_clouds(image):
            scl = image.select("SCL")
            valid_pixels = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
            return image.updateMask(valid_pixels)

        def add_ndvi(image):
            ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")
            return image.addBands(ndvi)

        collection = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(ee_poly)
            .filterDate(start_date, end_date)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 70))
            .map(mask_s2_clouds)
            .map(add_ndvi)
        )

        def extract_mean(img):
            mean_dict = img.select("NDVI").reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=ee_poly,
                scale=10,
                maxPixels=1e8
            )
            return ee.Feature(None, {
                "date": img.date().format("YYYY-MM-dd"),
                "ndvi_mean": mean_dict.get("NDVI")
            })

        reduced_fc = collection.map(extract_mean).filter(ee.Filter.notNull(["ndvi_mean"]))
        features = reduced_fc.getInfo().get("features", [])

        if not features:
            raise ValueError(f"No cloud-free Sentinel-2 observations found for {field_id} in {year}.")

        records = [
            {"date": f["properties"]["date"], "ndvi": f["properties"]["ndvi_mean"]}
            for f in features
        ]
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"])
        df = df.groupby("date", as_index=False)["ndvi"].mean()
        df = df.sort_values("date").reset_index(drop=True)
        df["ndvi_smooth"] = df["ndvi"].rolling(window=3, center=True, min_periods=1).median()
        df.to_csv(cache_csv, index=False)

    if "ndvi_smooth" not in df.columns:
        df["ndvi_smooth"] = df["ndvi"].rolling(window=3, center=True, min_periods=1).median()

    df = df.sort_values("date").reset_index(drop=True)

    # Build a daily continuous trajectory from the smoothed Sentinel-2 curve
    daily_dates = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
    x_obs = (df["date"] - df["date"].min()).dt.total_seconds().to_numpy()
    y_obs = df["ndvi_smooth"].to_numpy(dtype=float)
    x_daily = (daily_dates - df["date"].min()).total_seconds().to_numpy()

    try:
        from scipy.interpolate import PchipInterpolator
        if len(x_obs) >= 3:
            interp_fn = PchipInterpolator(x_obs, y_obs)
            y_daily = np.clip(interp_fn(x_daily), 0.0, 1.0)
        else:
            y_daily = np.interp(x_daily, x_obs, y_obs)
    except Exception:
        y_daily = np.interp(x_daily, x_obs, y_obs)

    daily_df = pd.DataFrame({"date": daily_dates, "ndvi_line": y_daily})

    # 1. Peak canopy date from the continuous line
    peak_idx = int(daily_df["ndvi_line"].idxmax())
    peak_date = pd.to_datetime(daily_df.loc[peak_idx, "date"])
    peak_ndvi = float(daily_df.loc[peak_idx, "ndvi_line"])

    # 2. Optimal classification point (80% of peak NDVI on the post-peak continuous line)
    target_cutoff_ndvi = float(peak_ndvi * target_drop_ratio)
    post_peak_daily = daily_df[daily_df["date"] >= peak_date].copy()
    post_peak_daily["diff"] = (post_peak_daily["ndvi_line"] - target_cutoff_ndvi).abs()

    target_row = post_peak_daily.sort_values("diff").iloc[0]
    target_date = pd.to_datetime(target_row["date"])
    actual_target_ndvi = float(target_row["ndvi_line"])

    # 3. Pre-target (-5 days) and post-target (+5 days) around the optimal target date
    before_date = target_date - timedelta(days=5)
    after_date = target_date + timedelta(days=5)

    before_row = daily_df.loc[(daily_df["date"] - before_date).abs().idxmin()]
    after_row = daily_df.loc[(daily_df["date"] - after_date).abs().idxmin()]
    before_ndvi = float(before_row["ndvi_line"])
    after_ndvi = float(after_row["ndvi_line"])

    return {
        "df": df,
        "daily_df": daily_df,
        "source": source_used,
        "csv_path": cache_csv,
        "peak_date": peak_date,
        "peak_ndvi": peak_ndvi,
        "target_cutoff_ndvi": target_cutoff_ndvi,
        "target_date": target_date,
        "actual_target_ndvi": actual_target_ndvi,
        "before_date": before_date,
        "before_ndvi": before_ndvi,
        "after_date": after_date,
        "after_ndvi": after_ndvi,
    }


def plot_phenology_curve(
    pheno_res: Dict,
    field_id: str,
    year: int,
    matched_dates: Optional[Dict[str, str]] = None
) -> Figure:
    """
    Generates an ultra-high-resolution (500 DPI) Google Material Design / Presentation
    style figure of the Sentinel-2 NDVI trajectory in Montserrat typography and UIUC colors,
    using sentence case throughout.
    """
    import matplotlib.dates as mdates

    df = pheno_res["df"]
    daily_df = pheno_res.get("daily_df")
    peak_date = pheno_res["peak_date"]
    peak_ndvi = pheno_res["peak_ndvi"]
    target_date = pheno_res["target_date"]
    actual_target_ndvi = pheno_res["actual_target_ndvi"]
    target_cutoff_ndvi = pheno_res["target_cutoff_ndvi"]
    before_date = pheno_res["before_date"]
    before_ndvi = pheno_res.get("before_ndvi", target_cutoff_ndvi)
    after_date = pheno_res["after_date"]
    after_ndvi = pheno_res.get("after_ndvi", target_cutoff_ndvi)

    fig = Figure(figsize=(11.2, 5.2), dpi=500, facecolor="#FFFFFF")
    ax = fig.add_axes([0.07, 0.13, 0.89, 0.71])
    ax.set_facecolor("#FFFFFF")

    # 1. Shaded ±5-day optimal scouting window band
    ax.axvspan(
        before_date, after_date,
        color=ILLINI_ORANGE, alpha=0.09, zorder=1,
        label=f"+/-5-day scouting window ({before_date.strftime('%b %d')} to {after_date.strftime('%b %d')})"
    )

    # 2. Soft area fill and continuous trajectory line
    if daily_df is not None:
        ax.fill_between(
            daily_df["date"], 0.0, daily_df["ndvi_line"],
            color=ILLINI_BLUE_ACCENT, alpha=0.06, zorder=2
        )
        ax.plot(
            daily_df["date"], daily_df["ndvi_line"], "-",
            color=ILLINI_BLUE, linewidth=3.2, solid_capstyle="round", zorder=4,
            label="Continuous NDVI trajectory"
        )
    else:
        ax.plot(
            df["date"], df["ndvi_smooth"], "-",
            color=ILLINI_BLUE, linewidth=3.2, solid_capstyle="round", zorder=4,
            label="Continuous NDVI trajectory"
        )

    # 3. Raw Sentinel-2 points as clean Material dots with white halo
    ax.scatter(
        df["date"], df["ndvi"],
        facecolor="#64748B", edgecolor="#FFFFFF",
        s=54, linewidth=1.3, alpha=0.84, zorder=5,
        label="Sentinel-2 cloud-free observations"
    )

    # 4. Horizontal reference line for 80% peak NDVI cutoff
    ax.axhline(
        target_cutoff_ndvi, color="#94A3B8", linestyle="--", alpha=0.65, linewidth=1.1, zorder=2
    )
    ax.text(
        df["date"].min() + timedelta(days=2), target_cutoff_ndvi + 0.02,
        f"Target cutoff ({target_cutoff_ndvi:.2f})",
        fontsize=8.5, fontweight="bold", color="#64748B"
    )

    # 5. Drop-lines & markers for peak, -5d, optimal target, and +5d
    ax.vlines(peak_date, 0.0, peak_ndvi, color=ILLINI_BLUE_ACCENT, linestyle=":", linewidth=1.6, zorder=3)
    ax.scatter(
        [peak_date], [peak_ndvi],
        color=ILLINI_BLUE_ACCENT, edgecolor="#FFFFFF",
        s=115, linewidth=2.2, zorder=7,
        label=f"Peak canopy ({peak_date.strftime('%Y-%m-%d')})"
    )
    ax.annotate(
        f"Peak canopy\n{peak_date.strftime('%b %d')} ({peak_ndvi:.2f})",
        xy=(peak_date, peak_ndvi),
        xytext=(-10, 16),
        textcoords="offset points",
        ha="center", va="bottom",
        fontsize=8.5, fontweight="bold", color="#FFFFFF",
        bbox=dict(boxstyle="round,pad=0.38,rounding_size=0.45", facecolor=ILLINI_BLUE, edgecolor="none"),
        zorder=8,
    )

    ax.vlines([before_date, after_date], 0.0, [before_ndvi, after_ndvi], color=ILLINI_ORANGE, linestyle=":", linewidth=1.3, alpha=0.75, zorder=3)
    ax.scatter(
        [before_date, after_date], [before_ndvi, after_ndvi],
        facecolor="#FFFFFF", edgecolor=ILLINI_ORANGE,
        s=75, linewidth=2.2, zorder=7,
        label=f"+/-5 days ({before_date.strftime('%b %d')} / {after_date.strftime('%b %d')})"
    )
    ax.annotate(
        f"-5d: {before_date.strftime('%b %d')}",
        xy=(before_date, before_ndvi),
        xytext=(-8, 12),
        textcoords="offset points",
        ha="right", va="bottom",
        fontsize=8, fontweight="bold", color=ILLINI_BLUE,
        bbox=dict(boxstyle="round,pad=0.28,rounding_size=0.35", facecolor="#FFF3EC", edgecolor=ILLINI_ORANGE, linewidth=0.9),
        zorder=8,
    )
    ax.annotate(
        f"+5d: {after_date.strftime('%b %d')}",
        xy=(after_date, after_ndvi),
        xytext=(8, -18),
        textcoords="offset points",
        ha="left", va="top",
        fontsize=8, fontweight="bold", color=ILLINI_BLUE,
        bbox=dict(boxstyle="round,pad=0.28,rounding_size=0.35", facecolor="#FFF3EC", edgecolor=ILLINI_ORANGE, linewidth=0.9),
        zorder=8,
    )

    ax.vlines(target_date, 0.0, actual_target_ndvi, color=ILLINI_ORANGE, linestyle="-", linewidth=2.0, zorder=3)
    ax.scatter(
        [target_date], [actual_target_ndvi],
        color=ILLINI_ORANGE, edgecolor="#FFFFFF",
        s=135, linewidth=2.4, zorder=8,
        label=f"Optimal target ({target_date.strftime('%Y-%m-%d')})"
    )
    ax.annotate(
        f"Optimal target\n{target_date.strftime('%b %d')} ({actual_target_ndvi:.2f})",
        xy=(target_date, actual_target_ndvi),
        xytext=(14, 18),
        textcoords="offset points",
        ha="left", va="bottom",
        fontsize=8.5, fontweight="bold", color="#FFFFFF",
        bbox=dict(boxstyle="round,pad=0.38,rounding_size=0.45", facecolor=ILLINI_ORANGE, edgecolor="none"),
        zorder=9,
    )

    # 6. Google Presentation typography, grid, and frameless spines
    fig.text(
        0.07, 0.93,
        f"Canopy NDVI trajectory and scouting window - Field {field_id} ({year})",
        fontsize=10.5, fontweight="600", color=ILLINI_BLUE
    )
    fig.text(
        0.07, 0.888,
        f"Peak baseline: {peak_date.strftime('%Y-%m-%d')}   |   Optimal target: {target_date.strftime('%Y-%m-%d')}   |   Suggested +/-5-day window: {before_date.strftime('%Y-%m-%d')} to {after_date.strftime('%Y-%m-%d')}",
        fontsize=8.3, color="#4A5B75"
    )

    ax.set_ylim(0.0, 1.12)
    ax.set_ylabel("Canopy NDVI", fontsize=9.0, fontweight="600", color="#4A5B75", labelpad=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=15))

    ax.grid(axis="y", linestyle="-", linewidth=0.9, color="#EEF2F6", zorder=0)
    ax.grid(axis="x", visible=False)

    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#CBD5E1")
    ax.spines["bottom"].set_linewidth(1.1)

    ax.tick_params(axis="y", colors="#4A5B75", labelsize=8.3, length=0, pad=6)
    ax.tick_params(axis="x", colors="#4A5B75", labelsize=8.3, length=4, color="#CBD5E1", pad=6)

    ax.legend(
        loc="lower left",
        fontsize=7.8,
        frameon=True,
        facecolor="#F8FAFC",
        edgecolor="#E2E8F0",
        ncol=2,
        borderpad=0.6,
    )
    return fig


# ==============================================================================
# 4. STEP 2A: PERSONAL PLANET API SEARCH & CLIP ORDERING
# ==============================================================================

def _clean_planet_key(api_key: str) -> str:
    """Strips whitespace, newlines, and surrounding quotes from Planet API keys."""
    if not api_key:
        return ""
    return str(api_key).strip().strip("'").strip('"').strip()


def _planet_request(
    method: str,
    url: str,
    api_key: str,
    json: Optional[dict] = None,
    timeout: int = 35,
    stream: bool = False,
    headers: Optional[dict] = None,
) -> requests.Response:
    """
    Sends an HTTP request to Planet APIs with multi-auth strategy:
    1. Primary: Authorization: api-key <KEY> (Standard for Planet Orders API v2)
    2. Fallback: HTTP Basic Auth (key, "") if 401 is returned (for legacy endpoints)
    Catches 401/403 and provides human-readable exceptions.
    """
    clean_key = _clean_planet_key(api_key)
    if not clean_key:
        raise ValueError("Planet API key cannot be empty. Please enter your API key in Step 3 Option B.")

    req_headers = dict(headers or {})
    req_headers["Authorization"] = f"api-key {clean_key}"
    if json is not None:
        req_headers["Content-Type"] = "application/json"

    resp = requests.request(
        method=method,
        url=url,
        headers=req_headers,
        json=json,
        timeout=timeout,
        stream=stream,
    )

    # Fallback to Basic Auth if api-key header was rejected with 401
    if resp.status_code == 401:
        fb_headers = {k: v for k, v in req_headers.items() if k.lower() != "authorization"}
        resp_fb = requests.request(
            method=method,
            url=url,
            auth=(clean_key, ""),
            headers=fb_headers,
            json=json,
            timeout=timeout,
            stream=stream,
        )
        if resp_fb.status_code != 401:
            return resp_fb

    return resp


def _extract_item_filename(item: dict) -> str:
    """Safely extracts the file name from a Planet Orders API result item."""
    for k in ("name", "path", "filename", "file_name"):
        val = item.get(k)
        if val and isinstance(val, str):
            return Path(val).name
    loc = item.get("location") or ""
    if loc and isinstance(loc, str):
        base_loc = loc.split("?")[0].rstrip("/")
        cand = Path(base_loc).name
        if cand and ("." in cand):
            return cand
    return ""


def _download_planet_file(loc: str, target_path: Path, clean_key: str):
    """Downloads a file from Planet Orders API results handling S3 signed URLs and Planet API auth."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if "api.planet.com" in loc:
        resp = _planet_request("GET", loc, api_key=clean_key, stream=True, timeout=180)
        resp.raise_for_status()
        with open(target_path, "wb") as f_out:
            for chunk in resp.iter_content(chunk_size=32768):
                if chunk:
                    f_out.write(chunk)
    else:
        with requests.get(loc, stream=True, timeout=180) as r_dl:
            if r_dl.status_code in (401, 403):
                r_dl.close()
                r_dl = _planet_request("GET", loc, api_key=clean_key, stream=True, timeout=180)
            r_dl.raise_for_status()
            with open(target_path, "wb") as f_out:
                for chunk in r_dl.iter_content(chunk_size=32768):
                    if chunk:
                        f_out.write(chunk)


def _unpack_zip_archive(zip_path: Path, extract_dir: Path) -> List[str]:
    """Unpacks a ZIP archive into extract_dir and returns paths to all valid GeoTIFF rasters."""
    try:
        extracted, _ = extract_sr_rasters_from_zip(zip_path, extract_dir)
        return [str(p) for p in extracted]
    except Exception as e:
        print(f"Warning: Failed to unpack ZIP bundle {zip_path.name}: {e}")
        return []


def get_planet_order_status(api_key: str, order_id: str) -> Dict[str, Any]:
    """
    Queries the current status of an order in Planet Orders API v2.
    Non-blocking, single HTTP check.
    """
    clean_order_id = str(order_id).strip().strip("'").strip('"')
    clean_key = _clean_planet_key(api_key)
    order_url = f"{PLANET_ORDERS_API_URL}/{clean_order_id}"

    resp = _planet_request("GET", order_url, api_key=clean_key, timeout=25)
    if resp.status_code == 401:
        raise PermissionError(
            f"Planet API authentication failed (HTTP 401 Unauthorized). "
            f"Please verify that your Planet API key is valid and has permission to access Order ID '{clean_order_id}'."
        )
    if resp.status_code == 404:
        raise FileNotFoundError(
            f"Planet Order ID '{clean_order_id}' was not found (HTTP 404). "
            f"Please verify that the Order ID UUID was copied accurately."
        )
    resp.raise_for_status()

    data = resp.json()
    state = str(data.get("state", "unknown")).lower()
    results = data.get("_links", {}).get("results") or data.get("results") or []
    
    sr_names = set(select_sr_rasters(_extract_item_filename(item) for item in results))
    raster_items = [
        item for item in results
        if _extract_item_filename(item) in sr_names or _extract_item_filename(item).lower().endswith(".zip")
    ]

    return {
        "status": "ok",
        "order_id": clean_order_id,
        "state": state,
        "created_at": data.get("created_on", ""),
        "last_message": data.get("last_message", ""),
        "raster_count": len(raster_items),
        "is_ready": state in ("success", "partial"),
        "is_processing": state in ("queued", "running", "preparing", "starting"),
        "is_failed": state in ("failed", "cancelled"),
    }


def _optional_float(value, ndigits: int = 1) -> Optional[float]:
    """Rounds a numeric Planet property, returning None when it is missing or non-numeric."""
    try:
        return None if value is None else round(float(value), ndigits)
    except (TypeError, ValueError):
        return None


def render_planet_scene_previews(
    api_key: str,
    poly_gdf: gpd.GeoDataFrame,
    scene_ids: List[str],
    out_dir: Path,
    max_scenes: int = 12,
    max_tiles_per_scene: int = 16,
) -> List[Dict]:
    """
    Builds a true-colour, field-cropped preview PNG for each PlanetScope scene by streaming its
    Planet XYZ tiles (`tiles.planet.com`, visual browse imagery — no Orders / download quota used),
    then overlaying the field boundary. PNGs are cached in `out_dir/planet_previews/` keyed by
    scene ID and boundary geometry, so a scene is never fetched twice for the same field.

    Returns one dict per scene: `{"scene_id", "status": "ok" | "no_data" | "error", "png_path", "message"}`.
    """
    import hashlib
    from PIL import Image, ImageDraw

    clean_key = _clean_planet_key(api_key)
    if not clean_key:
        raise ValueError("Planet API key cannot be empty. Please enter your API key in Step 3 Option B.")

    poly_gdf_4326 = poly_gdf.to_crs("EPSG:4326")
    try:
        utm_crs = poly_gdf_4326.estimate_utm_crs() or "EPSG:32615"
    except Exception:
        utm_crs = "EPSG:32615"
    geom_utm = unary_union(poly_gdf_4326.to_crs(utm_crs).geometry.values)
    view_bounds = _expand_view_bounds(geom_utm.bounds, pad_frac=0.20, target_aspect=4.0 / 3.0)
    b_xmin, b_ymin, b_xmax, b_ymax = view_bounds

    boundary_key = hashlib.md5(unary_union(poly_gdf_4326.geometry.values).wkb).hexdigest()[:10]
    preview_dir = Path(out_dir) / "planet_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    previews: List[Dict] = []

    for scene_id in list(scene_ids)[:max_scenes]:
        png_path = preview_dir / f"{scene_id}_{boundary_key}.png"
        if png_path.exists():
            previews.append({"scene_id": scene_id, "status": "ok", "png_path": str(png_path), "message": ""})
            continue

        status_codes: List[int] = []

        def _fetch_planet_tile(z: int, tx: int, ty: int, _sid: str = scene_id) -> Optional[np.ndarray]:
            url = PLANET_SCENE_TILES_URL.format(scene_id=_sid, z=z, x=tx, y=ty)
            # The key travels only in this server-side request; it never reaches the browser
            try:
                r = session.get(url, params={"api_key": clean_key}, timeout=15)
            except requests.RequestException:
                status_codes.append(0)
                return None
            status_codes.append(r.status_code)
            if r.status_code == 200 and r.content:
                img = Image.open(io.BytesIO(r.content)).convert("RGBA")
                return np.asarray(img, dtype=np.uint8)
            return None

        try:
            warped = _fetch_xyz_mosaic_in_memory(
                _fetch_planet_tile,
                utm_crs,
                view_bounds,
                n_bands=4,
                zoom_candidates=(16, 15, 14, 13, 12),
                max_tiles=max_tiles_per_scene,
                dst_w=640,
            )
        except Exception as exc:
            previews.append({"scene_id": scene_id, "status": "error", "png_path": "", "message": type(exc).__name__})
            continue

        if status_codes and all(code in (401, 403) for code in status_codes):
            raise PermissionError(
                "Planet refused the preview tile requests (HTTP 401/403). "
                "Please verify that your Planet API key is valid and has PlanetScope access."
            )

        alpha = warped[3].astype(np.float32) / 255.0
        if float(alpha.max()) <= 0.0:
            previews.append({
                "scene_id": scene_id,
                "status": "no_data",
                "png_path": "",
                # Planet answers 404 both for invalid keys and for scenes the key cannot access
                "message": "Planet returned no imagery for this scene over your field "
                           "(the scene may not be accessible with this API key).",
            })
            continue

        # Composite RGB over a dark navy backdrop so areas outside the scene footprint read as no-data
        backdrop = np.array([19, 41, 75], dtype=np.float32).reshape(3, 1, 1)
        rgb = warped[:3].astype(np.float32) * alpha + backdrop * (1.0 - alpha)
        img = Image.fromarray(np.transpose(rgb.clip(0, 255).astype(np.uint8), (1, 2, 0)), mode="RGB")

        # Overlay the field boundary (white halo + Illini orange stroke) in pixel space
        img_w, img_h = img.size
        sx = img_w / max(1e-6, b_xmax - b_xmin)
        sy = img_h / max(1e-6, b_ymax - b_ymin)
        draw = ImageDraw.Draw(img)
        polys = [geom_utm] if geom_utm.geom_type == "Polygon" else list(getattr(geom_utm, "geoms", []))
        for p in polys:
            if p.geom_type != "Polygon":
                continue
            for ring in [p.exterior, *p.interiors]:
                pts = [((x - b_xmin) * sx, (b_ymax - y) * sy) for x, y in ring.coords]
                draw.line(pts, fill=(255, 255, 255), width=4, joint="curve")
                draw.line(pts, fill=ILLINI_ORANGE, width=2, joint="curve")

        img.save(png_path, format="PNG", optimize=True)
        previews.append({"scene_id": scene_id, "status": "ok", "png_path": str(png_path), "message": ""})

    return previews


def search_planet_catalog_for_window(
    api_key: str,
    poly_gdf: gpd.GeoDataFrame,
    peak_date: Optional[datetime] = None,
    target_date: Optional[datetime] = None,
    before_date: Optional[datetime] = None,
    after_date: Optional[datetime] = None,
    season: Optional[str] = None,
    start_date_str: Optional[str] = None,
    end_date_str: Optional[str] = None,
    max_cloud_cover: float = 0.20,
) -> Dict:
    """
    Searches Planet Data API (PSScene, ortho_analytic_8b_sr) for cloud-free scenes
    across the entire season window (default: May 1 to Oct 31 of the soybean season).
    Deduplicates daily passes and highlights the 4 optimal captures suggested by the
    continuous Sentinel-2 NDVI trajectory curve:
      - Early baseline mask (closest to peak canopy t_peak)
      - Pre-target point (closest to t_0 - 5 days)
      - Optimal target point (closest to t_0)
      - Post-target point (closest to t_0 + 5 days)
    Returns all available cloud-free scenes across the season so the user can select
    any combination of dates to download.
    """
    clean_key = _clean_planet_key(api_key)
    poly_gdf_4326 = poly_gdf.to_crs("EPSG:4326")
    poly_geojson = poly_gdf_4326.geometry.iloc[0].__geo_interface__
    field_geom_4326 = unary_union(poly_gdf_4326.geometry.values)
    if not field_geom_4326.is_valid:
        field_geom_4326 = shapely.make_valid(field_geom_4326)

    try:
        utm_crs = poly_gdf.estimate_utm_crs()
        field_geom_utm = unary_union(poly_gdf.to_crs(utm_crs).geometry.values)
        if not field_geom_utm.is_valid:
            field_geom_utm = shapely.make_valid(field_geom_utm)
        field_area_ref = float(field_geom_utm.area)
    except Exception:
        utm_crs = None
        field_area_ref = float(field_geom_4326.area)

    # Resolve season year
    year = None
    if season and str(season).isdigit():
        year = int(season)
    elif target_date:
        year = target_date.year
    elif peak_date:
        year = peak_date.year
    else:
        year = datetime.now().year

    # Search window: default 05/01 to 10/31 of the season year
    start_iso = start_date_str if start_date_str else f"{year}-05-01"
    end_iso = end_date_str if end_date_str else f"{year}-10-31"

    start_search = f"{start_iso}T00:00:00.000Z"
    end_search = f"{end_iso}T23:59:59.000Z"

    search_filter = {
        "type": "AndFilter",
        "config": [
            {"type": "GeometryFilter", "field_name": "geometry", "config": poly_geojson},
            {"type": "DateRangeFilter", "field_name": "acquired", "config": {"gte": start_search, "lte": end_search}},
            {"type": "RangeFilter", "field_name": "cloud_cover", "config": {"lte": max_cloud_cover}},
            {"type": "AssetFilter", "config": ["ortho_analytic_8b_sr"]}
        ]
    }

    payload = {
        "item_types": ["PSScene"],
        "filter": search_filter
    }

    resp = _planet_request("POST", PLANET_DATA_API_URL, api_key=clean_key, json=payload, timeout=35)
    if resp.status_code == 401:
        raise PermissionError("Invalid Planet API key (HTTP 401 Unauthorized). Please check your key in your Planet account.")
    resp.raise_for_status()

    data = resp.json()
    all_features = list(data.get("features", []))

    # Follow quick-search pagination if more than 250 scenes are returned
    next_url = data.get("_links", {}).get("_next")
    pages = 0
    while next_url and pages < 4:
        try:
            r_next = _planet_request("GET", next_url, api_key=clean_key, timeout=35)
            if r_next.status_code != 200:
                break
            data_next = r_next.json()
            all_features.extend(data_next.get("features", []))
            next_url = data_next.get("_links", {}).get("_next")
            pages += 1
        except Exception:
            break

    if not all_features:
        raise ValueError(
            f"No cloud-free 8-band PlanetScope scenes found between {start_iso} and {end_iso} "
            f"with cloud cover <= {int(max_cloud_cover * 100)}%."
        )

    scenes = []
    for f in all_features:
        props = f.get("properties", {})
        acq_str = props.get("acquired", "")[:10]
        if not acq_str:
            continue
        try:
            acq_dt = datetime.strptime(acq_str, "%Y-%m-%d")
        except ValueError:
            continue

        geom_dict = f.get("geometry")
        coverage_pct = 0.0
        if geom_dict:
            try:
                scene_geom = shape(geom_dict)
                if not scene_geom.is_valid:
                    scene_geom = shapely.make_valid(scene_geom)

                if scene_geom.contains(field_geom_4326):
                    coverage_pct = 100.0
                else:
                    inter = field_geom_4326.intersection(scene_geom)
                    if inter.is_empty:
                        coverage_pct = 0.0
                    else:
                        if utm_crs is not None:
                            inter_utm = gpd.GeoSeries([inter], crs="EPSG:4326").to_crs(utm_crs).iloc[0]
                            inter_area = float(inter_utm.area)
                        else:
                            inter_area = float(inter.area)

                        if field_area_ref > 0:
                            ratio = (inter_area / field_area_ref) * 100.0
                            coverage_pct = 100.0 if ratio >= 99.5 else round(max(0.0, ratio), 1)
                        else:
                            coverage_pct = 0.0
            except Exception:
                coverage_pct = 0.0

        scenes.append({
            "scene_id": f["id"],
            "date_str": acq_dt.strftime("%Y%m%d"),
            "date_iso": acq_str,
            "date_dt": acq_dt,
            "coverage_pct": coverage_pct,
            "cloud_cover_pct": round(float(props.get("cloud_cover", 0.0)) * 100.0, 1),
            "sun_elevation": round(float(props.get("sun_elevation", 0.0)), 1),
            # Scene-level usable-pixel metrics (from Planet's UDM2); may be missing on older captures
            "clear_percent": _optional_float(props.get("clear_percent")),
            "visible_percent": _optional_float(props.get("visible_percent")),
            "view_angle": _optional_float(props.get("view_angle")),
            "quality_category": props.get("quality_category", ""),
            "ground_control": bool(props.get("ground_control", True)),
            "instrument": props.get("instrument", "PSB.SD"),
            "suggestion": "",
            "is_suggested": False,
        })

    if not scenes:
        raise ValueError(f"No valid PlanetScope scenes parsed in the window {start_iso} to {end_iso}.")

    # Deduplicate by calendar day: prioritize full field coverage first, then lowest cloud cover
    scenes_df = pd.DataFrame(scenes).sort_values(
        ["date_str", "coverage_pct", "cloud_cover_pct"],
        ascending=[True, False, True]
    ).drop_duplicates("date_str", keep="first")
    scenes_df = scenes_df.sort_values("date_dt").reset_index(drop=True)

    peak_scene = None
    t0_scene = None
    t_minus_1 = None
    t_plus_1 = None

    # Highlight suggested captures based on the continuous Sentinel-2 NDVI trajectory
    if peak_date is not None and target_date is not None:
        # Require full field coverage (>= 95%) for suggestions so partial captures are never auto-suggested
        full_cov_mask = scenes_df["coverage_pct"] >= 95.0
        cand_df = scenes_df[full_cov_mask].copy() if full_cov_mask.any() else scenes_df.copy()

        # 1. Early baseline mask (closest to peak canopy t_peak)
        cand_df["diff_peak"] = (cand_df["date_dt"] - peak_date).abs()
        idx_peak = cand_df.sort_values(["diff_peak", "cloud_cover_pct"]).index[0]
        scenes_df.loc[idx_peak, "suggestion"] = "Suggested (early baseline, t_peak)"
        scenes_df.loc[idx_peak, "is_suggested"] = True
        peak_scene = scenes_df.loc[idx_peak].to_dict()

        # Remaining candidates for target dates
        rem_mask = cand_df.index != idx_peak
        rem_df = cand_df[rem_mask].copy()

        if not rem_df.empty:
            # 2. Optimal target (closest to optimal scouting date t_0)
            rem_df["diff_target"] = (rem_df["date_dt"] - target_date).abs()
            idx_target = rem_df.sort_values(["diff_target", "cloud_cover_pct"]).index[0]
            scenes_df.loc[idx_target, "suggestion"] = "Suggested (optimal target, t_0)"
            scenes_df.loc[idx_target, "is_suggested"] = True
            t0_scene = scenes_df.loc[idx_target].to_dict()
            t0_dt = t0_scene["date_dt"]

            # 3. Pre-target (-5 days before t_0)
            b_target = before_date or (target_date - timedelta(days=5))
            before_cands = rem_df[rem_df["date_dt"] < t0_dt].copy()
            if not before_cands.empty:
                before_cands["diff_before"] = (before_cands["date_dt"] - b_target).abs()
                idx_before = before_cands.sort_values(["diff_before", "cloud_cover_pct"]).index[0]
                scenes_df.loc[idx_before, "suggestion"] = "Suggested (pre-target, -5d)"
                scenes_df.loc[idx_before, "is_suggested"] = True
                t_minus_1 = scenes_df.loc[idx_before].to_dict()

            # 4. Post-target (+5 days after t_0)
            a_target = after_date or (target_date + timedelta(days=5))
            after_cands = rem_df[rem_df["date_dt"] > t0_dt].copy()
            if not after_cands.empty:
                after_cands["diff_after"] = (after_cands["date_dt"] - a_target).abs()
                idx_after = after_cands.sort_values(["diff_after", "cloud_cover_pct"]).index[0]
                scenes_df.loc[idx_after, "suggestion"] = "Suggested (post-target, +5d)"
                scenes_df.loc[idx_after, "is_suggested"] = True
                t_plus_1 = scenes_df.loc[idx_after].to_dict()

    suggested_scene_ids = [
        s["scene_id"] for s in [peak_scene, t_minus_1, t0_scene, t_plus_1]
        if s is not None and "scene_id" in s
    ]

    clean_scenes_df = scenes_df.drop(
        columns=["diff_peak", "diff_target", "diff_before", "diff_after"], errors="ignore"
    )
    all_scenes_list = clean_scenes_df.to_dict(orient="records")

    return {
        "all_scenes": all_scenes_list,
        "suggested_scene_ids": suggested_scene_ids,
        "early_baseline": peak_scene,
        "before_target": t_minus_1,
        "optimal_target": t0_scene,
        "after_target": t_plus_1,
        "search_window": f"{start_iso} to {end_iso}",
        "total_scenes_found": len(all_scenes_list),
    }


def download_planet_order_by_id(
    api_key: str,
    order_id: str,
    field_id: str,
    season: str,
    status_callback: Optional[Callable[[str], None]] = None,
    max_wait_minutes: int = 30
) -> Dict[str, Any]:
    """
    Polls an existing Planet Orders API v2 order ID until completion (handling state 'success'
    or 'partial'), and downloads the clipped Surface Reflectance GeoTIFFs to
    routine_inspection/raw_satellite/<field_id>/<season>/.

    If the order is still processing when max_wait_minutes expires, returns
    a friendly status dict {'status': 'still_processing', ...} without raising an unhandled error.
    """
    clean_order_id = str(order_id).strip().strip("'").strip('"')
    clean_key = _clean_planet_key(api_key)
    out_dir = SAT_BASE_DIR / field_id / str(season)
    out_dir.mkdir(parents=True, exist_ok=True)
    order_url = f"{PLANET_ORDERS_API_URL}/{clean_order_id}"

    start_time = time.time()
    while True:
        poll = _planet_request("GET", order_url, api_key=clean_key, timeout=35)
        if poll.status_code == 401:
            raise PermissionError(
                f"Planet API authentication failed (HTTP 401 Unauthorized). "
                f"Please verify that your Planet API key is valid and has permission to access Order ID '{clean_order_id}'."
            )
        if poll.status_code == 404:
            raise FileNotFoundError(
                f"Planet Order ID '{clean_order_id}' was not found (HTTP 404). "
                f"Please verify that the Order ID UUID was copied accurately."
            )
        poll.raise_for_status()

        data = poll.json()
        state = str(data.get("state", "unknown")).lower()
        elapsed_sec = int(time.time() - start_time)
        elapsed_min = elapsed_sec // 60
        elapsed_rem = elapsed_sec % 60

        if status_callback:
            status_callback(
                f"Planet order [{state.upper()}] (ID: {clean_order_id}) — Elapsed: {elapsed_min}m {elapsed_rem:02d}s (max {max_wait_minutes}m)..."
            )

        if state in ("success", "partial"):
            results = data.get("_links", {}).get("results") or data.get("results") or []
            
            sr_names = set(select_sr_rasters(_extract_item_filename(item) for item in results))
            tif_items = [item for item in results if _extract_item_filename(item) in sr_names]

            zip_items = [
                item for item in results
                if _extract_item_filename(item).lower().endswith(".zip")
            ]

            if not tif_items and not zip_items:
                all_names = [_extract_item_filename(item) for item in results if _extract_item_filename(item)]
                if not results:
                    raise ValueError(
                        f"Planet Order '{clean_order_id}' completed with state '{state}', but returned 0 download files. "
                        "This typically occurs if the order's temporary download links have expired (Planet download URLs expire after 24–48 hours) "
                        "or if assets were delivered directly to cloud storage. "
                        "Please place a new clip order in Step 3 (Search & Order), or upload the GeoTIFFs downloaded from Planet Explorer in Option A."
                    )
                else:
                    raise ValueError(
                        f"Planet Order '{clean_order_id}' completed with state '{state}', but no clipped GeoTIFF rasters or ZIP archives were found in results. "
                        f"Items found in order: {all_names}. "
                        "If you already downloaded this order from Planet Explorer, please upload the GeoTIFFs directly in Option A above."
                    )

            downloaded_tifs = []
            if tif_items:
                total_tifs = len(tif_items)
                for idx, item in enumerate(tif_items):
                    name = _extract_item_filename(item) or f"planet_scene_{idx + 1}.tif"
                    loc = item["location"]
                    target_path = out_dir / name
                    if status_callback:
                        status_callback(f"Downloading clipped GeoTIFF ({idx + 1}/{total_tifs}): {name}...")
                    _download_planet_file(loc, target_path, clean_key)
                    downloaded_tifs.append(str(target_path))

            if zip_items:
                total_zips = len(zip_items)
                for idx, item in enumerate(zip_items):
                    zname = _extract_item_filename(item) or f"order_{clean_order_id}_{idx + 1}.zip"
                    loc = item["location"]
                    zip_path = out_dir / zname
                    if status_callback:
                        status_callback(f"Downloading order ZIP bundle ({idx + 1}/{total_zips}): {zname}...")
                    _download_planet_file(loc, zip_path, clean_key)
                    if status_callback:
                        status_callback(f"Unpacking ZIP bundle {zname}...")
                    unpacked = _unpack_zip_archive(zip_path, out_dir)
                    downloaded_tifs.extend(unpacked)

            if not downloaded_tifs:
                raise ValueError(
                    f"Planet Order '{clean_order_id}' completed with state '{state}', but no valid .tif rasters were found or extracted."
                )

            downloaded_tifs = list(dict.fromkeys(downloaded_tifs))

            if status_callback:
                status_callback(f"Downloaded {len(downloaded_tifs)} clipped PlanetScope GeoTIFF(s) successfully!")

            return {
                "status": "ok",
                "state": state,
                "order_id": clean_order_id,
                "downloaded_files": downloaded_tifs,
            }

        if state in ("failed", "cancelled"):
            last_msg = data.get("last_message", "No error details returned.")
            raise RuntimeError(f"Planet order '{clean_order_id}' ended with state '{state}': {last_msg}")

        # If order is still running and max_wait_minutes has expired
        if (time.time() - start_time) > max_wait_minutes * 60:
            return {
                "status": "still_processing",
                "state": state,
                "order_id": clean_order_id,
                "message": (
                    f"Planet order '{clean_order_id}' is currently still {state.upper()} in Planet's cloud queue. "
                    f"Planet is actively clipping your scenes in the background. Your order has not failed. "
                    f"Please check back in a few minutes or click 'Download order by ID' again once complete."
                ),
            }

        time.sleep(12)


def submit_and_download_planet_clip_order(
    api_key: str,
    poly_gdf: gpd.GeoDataFrame,
    scene_ids: List[str],
    field_id: str,
    season: str,
    status_callback: Optional[Callable[[str], None]] = None,
    max_wait_minutes: int = 30
) -> Dict[str, Any]:
    """
    Submits a polygon-clipped, Sentinel-2-harmonized order to Planet Orders API v2 for the selected scene IDs,
    records the Order ID, polls until completion, and downloads the clipped GeoTIFFs to
    routine_inspection/raw_satellite/<field_id>/<season>/.
    """
    clean_key = _clean_planet_key(api_key)
    poly_geojson = poly_gdf.to_crs("EPSG:4326").geometry.iloc[0].__geo_interface__
    out_dir = SAT_BASE_DIR / field_id / str(season)
    out_dir.mkdir(parents=True, exist_ok=True)

    unique_scene_ids = list(dict.fromkeys(scene_ids))
    order_payload = {
        "name": f"RCR_{field_id}_{season}_clipped",
        "products": [
            {
                "item_ids": unique_scene_ids,
                "item_type": "PSScene",
                "product_bundle": "analytic_8b_sr_udm2"
            }
        ],
        # Clip to the field, then harmonize to Sentinel-2 so the delivered rasters match the
        # *_AnalyticMS_SR_8b_harmonized_clip.tif files produced by Planet Explorer orders.
        "tools": [
            {
                "clip": {
                    "aoi": poly_geojson
                }
            },
            {
                "harmonize": {
                    "target_sensor": "Sentinel-2"
                }
            }
        ]
    }

    if status_callback:
        status_callback(f"Submitting polygon-clipped order ({len(unique_scene_ids)} scene(s)) to Planet Orders API v2...")

    resp = _planet_request("POST", PLANET_ORDERS_API_URL, api_key=clean_key, json=order_payload, timeout=35)
    if resp.status_code == 401:
        raise PermissionError("Planet API authentication failed (HTTP 401 Unauthorized). Please verify your Planet API key.")
    resp.raise_for_status()

    order_info = resp.json()
    order_id = order_info["id"]

    # Save order ID to disk so it can be resumed even if interrupted
    order_id_file = out_dir / "last_planet_order_id.txt"
    order_id_file.write_text(order_id, encoding="utf-8")

    if status_callback:
        status_callback(f"Order submitted successfully! Order ID: {order_id}. Polling Planet clipping progress...")

    return download_planet_order_by_id(
        api_key=clean_key,
        order_id=order_id,
        field_id=field_id,
        season=season,
        status_callback=status_callback,
        max_wait_minutes=max_wait_minutes,
    )


# ==============================================================================
# 5. STEP 2B: LOCAL FOLDER SCANNING & MANUAL UPLOAD MATCHING
# ==============================================================================

def parse_date_from_filename(filename: str) -> Optional[str]:
    """Extracts an 8-digit YYYYMMDD date string from a PlanetScope filename."""
    m1 = re.search(r"(20[1-3][0-9][01][0-9][0-3][0-9])", filename)
    if m1:
        return m1.group(1)
    m2 = re.search(r"(20[1-3][0-9])[-_]([01][0-9])[-_]([0-3][0-9])", filename)
    if m2:
        return f"{m2.group(1)}{m2.group(2)}{m2.group(3)}"
    return None


def find_local_field_tifs(field_id: str, season: str) -> Dict[str, Path]:
    """
    Scans routine_inspection/raw_satellite/ and data/raw/satellite/ for all valid
    PlanetScope Surface Reflectance GeoTIFFs for a given field and season.
    Returns {YYYYMMDD: Path}.
    """
    candidate_dirs = [
        SAT_BASE_DIR / field_id / str(season),
        SAT_BASE_DIR / str(season) / field_id,
        SAT_BASE_DIR / field_id,
        SAT_BASE_DIR / field_id.replace("-", "_"),
        SAT_BASE_DIR / field_id.replace("_", "-"),
    ]
    if field_id in ("FIELD_1", "PC3"):
        candidate_dirs.extend([
            SAT_BASE_DIR / "FIELD_1" / str(season),
            SAT_BASE_DIR / "PC3" / str(season),
        ])
    found: Dict[str, Path] = {}
    for d in candidate_dirs:
        if d.exists():
            tifs = [f for f in d.rglob("*.tif*") if f.is_file()]
            use_tifs = [Path(p) for p in select_sr_rasters(str(f) for f in sorted(tifs))]
            for tif in use_tifs:
                d_str = parse_date_from_filename(tif.name)
                if d_str and d_str.startswith(str(season)):
                    found[d_str] = tif
    return dict(sorted(found.items()))


def find_available_seasons_for_field(field_id: str) -> List[int]:
    """Returns a sorted list of years (descending) that have local PlanetScope TIFs for field_id."""
    years = set()
    for yr in [2026, 2025, 2024, 2023, 2022]:
        if find_local_field_tifs(field_id, str(yr)):
            years.add(yr)
    return sorted(years, reverse=True)


def auto_match_four_dates(
    available_dates: List[str],
    peak_date: datetime,
    target_date: datetime
) -> Dict[str, Optional[str]]:
    """
    Given a sorted list of available YYYYMMDD strings, automatically picks:
      - early_date: closest to peak_date
      - target_0 (t_0): closest to target_date (excluding early_date)
      - target_minus_1 (t_-1): capture immediately before t_0
      - target_plus_1 (t_+1): capture immediately after t_0
    """
    if not available_dates:
        return {"early": None, "before": None, "target": None, "after": None}

    dts = [(d, datetime.strptime(d, "%Y%m%d")) for d in sorted(set(available_dates))]

    # 1. Closest to Peak Canopy Date
    early_d = min(dts, key=lambda x: abs((x[1] - peak_date).days))[0]

    # Remaining dates for late-season classification
    rem = [x for x in dts if x[0] != early_d]
    if not rem:
        return {"early": early_d, "before": None, "target": None, "after": None}

    # 2. If there are exactly 3 remaining dates, assign them chronologically as (Before, Target, After)
    if len(rem) == 3:
        return {
            "early": early_d,
            "before": rem[0][0],
            "target": rem[1][0],
            "after": rem[2][0],
        }

    # Otherwise find the closest to target_date as t_0, then pick neighbors
    t0_idx = min(range(len(rem)), key=lambda i: abs((rem[i][1] - target_date).days))
    t0_d = rem[t0_idx][0]
    before_d = rem[t0_idx - 1][0] if t0_idx - 1 >= 0 else None
    after_d = rem[t0_idx + 1][0] if t0_idx + 1 < len(rem) else None

    return {
        "early": early_d,
        "before": before_d,
        "target": t0_d,
        "after": after_d,
    }


# ==============================================================================
# 6. STEP 3: MULTI-DATE RCR CLASSIFICATION & VISUAL REPORT ENGINE
# ==============================================================================

def _clip_raster_to_polygon(raster_path: Path, poly_gdf: gpd.GeoDataFrame):
    r_path = Path(raster_path)
    with rasterio.open(r_path) as src:
        bounds_projected = poly_gdf.to_crs(src.crs)
        poly_geom = bounds_projected.geometry.unary_union
        poly_box = box(*poly_geom.bounds)
        raster_box = box(*src.bounds)

        # Helper to compute distance between centroids safely
        def _calc_dist_km():
            try:
                pc = poly_box.centroid
                rc = raster_box.centroid
                if not (np.isnan(pc.x) or np.isnan(pc.y) or np.isnan(rc.x) or np.isnan(rc.y)):
                    return round(pc.distance(rc) / 1000.0, 1)
            except Exception:
                pass
            return None

        # Validate spatial intersection before invoking mask
        if not poly_box.intersects(raster_box):
            dist_km = _calc_dist_km()
            dist_str = f"approximately {dist_km:.1f} km away" if dist_km is not None else "in a different geographic region"
            raise ValueError(
                f"Spatial overlap error: Field boundary does not overlap GeoTIFF raster '{r_path.name}'. "
                f"The raster is located {dist_str} from your field. "
                "This occurs when using a Planet Order ID or GeoTIFF that was created for a different field. "
                "In Planet's system, Order IDs are permanently tied to the exact field boundary submitted at ordering time. "
                "To scout this field, please submit a new clip order for this field boundary in Step 3."
            )

        shapes = [poly_geom]
        try:
            clipped_img, clipped_transform = mask(src, shapes, crop=True, nodata=0)
        except ValueError as val_err:
            if "do not overlap" in str(val_err).lower():
                dist_km = _calc_dist_km()
                dist_str = f"~{dist_km:.1f} km" if dist_km is not None else "far"
                raise ValueError(
                    f"Spatial overlap error: Field boundary does not overlap GeoTIFF raster '{r_path.name}'. "
                    f"Distance between centers: {dist_str}. "
                    "This occurs when using imagery from a Planet Order ID created for a different field."
                ) from val_err
            raise

        meta = src.meta.copy()
        meta.update({
            "height": clipped_img.shape[1],
            "width": clipped_img.shape[2],
            "transform": clipped_transform
        })
        return clipped_img, meta


def _min_max_scale(arr: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
    valid_pixels = arr[valid_mask]
    if len(valid_pixels) == 0:
        return np.full_like(arr, np.nan, dtype="float32")
    p_min = np.nanmin(valid_pixels)
    p_max = np.nanmax(valid_pixels)
    if p_max == p_min:
        scaled = np.zeros_like(arr, dtype="float32")
    else:
        scaled = (arr - p_min) / (p_max - p_min)
    scaled[~valid_mask] = np.nan
    return scaled.astype("float32")


def _export_single_band_geotiff(out_path: Path, array: np.ndarray, meta: dict, dtype, nodata_val):
    out_meta = meta.copy()
    out_meta.update({
        "driver": "GTiff",
        "count": 1,
        "dtype": dtype,
        "nodata": nodata_val
    })
    clean_arr = np.nan_to_num(array, nan=nodata_val)
    with rasterio.open(out_path, "w", **out_meta) as dst:
        dst.write(clean_arr.astype(dtype), 1)


def run_multi_date_rcr_classification(
    field_id: str,
    season: str,
    poly_gdf: gpd.GeoDataFrame,
    early_tif_path: Path,
    early_date_str: str,
    target_captures: List[Tuple[str, str, Path]],
    threshold: float = OPTIMAL_THRESHOLD,
    mask_sigma: float = 3.0,
) -> Dict:
    """
    Runs the complete RCR classification pipeline across the selected target captures.
    Returns summary metrics, raster arrays for plotting, and exported file paths.
    """
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Production model not found at {MODEL_PATH}")

    model = joblib.load(MODEL_PATH)

    # Step A: Early-season canopy mask (mean - mask_sigma * SD)
    early_img, early_meta = _clip_raster_to_polygon(early_tif_path, poly_gdf)
    if early_img.shape[0] < 8:
        raise ValueError(
            f"The early baseline raster '{early_tif_path.name}' has only {early_img.shape[0]} band(s). "
            "The Red Crown Rot model requires 8-band PlanetScope SuperDove Surface Reflectance imagery "
            "(analytic_8b_sr_udm2)."
        )
    early_red = early_img[5].astype("float32") / 10000.0  # Band 6 (Red)
    early_nir = early_img[7].astype("float32") / 10000.0  # Band 8 (NIR)

    with np.errstate(divide="ignore", invalid="ignore"):
        early_denom = early_nir + early_red
        early_ndvi = np.where(early_denom > 0, (early_nir - early_red) / early_denom, np.nan)

    valid_early = early_ndvi[~np.isnan(early_ndvi)]
    if len(valid_early) == 0:
        raise ValueError("No valid canopy pixels found in the early baseline image.")

    ndvi_threshold = float(np.mean(valid_early)) - (float(mask_sigma) * float(np.std(valid_early)))
    canopy_mask_early = (early_ndvi >= ndvi_threshold) & (~np.isnan(early_ndvi))

    date_results = []
    exported_files = []

    # Process (and therefore report/plot) captures chronologically, regardless of selection order (YYYYMMDD sorts lexically)
    target_captures = sorted(target_captures, key=lambda c: str(c[1]))

    for role_label, target_date_str, target_tif_path in target_captures:
        target_img, out_meta = _clip_raster_to_polygon(target_tif_path, poly_gdf)
        if target_img.shape[0] < 8:
            raise ValueError(
                f"The classification raster '{target_tif_path.name}' has only {target_img.shape[0]} band(s). "
                "The Red Crown Rot model requires 8-band PlanetScope SuperDove Surface Reflectance imagery "
                "(analytic_8b_sr_udm2)."
            )
        h, w = out_meta["height"], out_meta["width"]

        aligned_canopy_mask = np.zeros((h, w), dtype=np.uint8)
        reproject(
            source=canopy_mask_early.astype(np.uint8),
            destination=aligned_canopy_mask,
            src_transform=early_meta["transform"],
            src_crs=early_meta["crs"],
            dst_transform=out_meta["transform"],
            dst_crs=out_meta["crs"],
            resampling=Resampling.nearest
        )
        canopy_mask = aligned_canopy_mask.astype(bool)

        b2 = target_img[1].astype("float32") / 10000.0  # Blue
        b4 = target_img[3].astype("float32") / 10000.0  # Green
        b5 = target_img[4].astype("float32") / 10000.0  # Yellow
        b6 = target_img[5].astype("float32") / 10000.0  # Red
        b7 = target_img[6].astype("float32") / 10000.0  # Red Edge
        b8 = target_img[7].astype("float32") / 10000.0  # NIR

        final_mask = canopy_mask & (~np.isnan(b8)) & (b8 > 0)

        with np.errstate(divide="ignore", invalid="ignore"):
            ndvi = np.where((b8 + b6) > 0, (b8 - b6) / (b8 + b6), np.nan)
            gndvi = np.where((b8 + b4) > 0, (b8 - b4) / (b8 + b4), np.nan)
            ndre = np.where((b8 + b7) > 0, (b8 - b7) / (b8 + b7), np.nan)

            denom_evi = b8 + 6.0 * b6 - 7.5 * b2 + 1.0
            evi = np.where(denom_evi > 0, 2.5 * (b8 - b6) / denom_evi, np.nan)

            ci = np.where(b4 > 0, (b8 / b4) - 1.0, np.nan)
            cire = np.where(b7 > 0, (b8 / b7) - 1.0, np.nan)

            ratio_term = np.where(b7 > 0, b2 / b7, 0.0)
            norm_term = np.where((b8 + b7) > 0, (b8 - b7) / (b8 + b7), 0.0)
            csi = np.where((b7 > 0) & ((b8 + b7) > 0), 2.5 * ratio_term * norm_term, np.nan)

        raw_features = [ndvi, gndvi, ndre, evi, ci, cire, csi, b6, b8, b7, b4, b2, b5]
        normalized_features = [_min_max_scale(feat, final_mask) for feat in raw_features]
        norm_stack = np.stack(normalized_features, axis=0)

        flat_data = norm_stack.reshape(13, -1).T
        valid_flat = (flat_data[:, 0] >= 0) & (~np.isnan(flat_data[:, 0]))

        prob_flat = np.full(h * w, fill_value=np.nan, dtype=np.float32)
        class_flat = np.full(h * w, fill_value=np.nan, dtype=np.float32)

        if np.any(valid_flat):
            valid_df = pd.DataFrame(flat_data[valid_flat], columns=FEATURE_NAMES)
            probs = model.predict_proba(valid_df)[:, 1]
            prob_flat[valid_flat] = probs
            class_flat[valid_flat] = (probs >= threshold).astype(np.float32)

        prob_2d = prob_flat.reshape((h, w))
        class_2d = class_flat.reshape((h, w))

        # Save outputs (RCR probability and RCR classification only)
        file_prefix = f"{field_id}_{season}_{target_date_str}"
        prob_out_path = DIR_CLASSIFIED / f"{file_prefix}_rcr_probability.tif"
        class_out_path = DIR_CLASSIFIED / f"{file_prefix}_rcr_classified.tif"

        _export_single_band_geotiff(prob_out_path, prob_2d, out_meta, np.float32, -9999.0)
        _export_single_band_geotiff(class_out_path, np.nan_to_num(class_2d, nan=-1.0), out_meta, np.int8, -1)

        exported_files.extend([prob_out_path, class_out_path])

        # Area quantification
        pixel_area_m2 = abs(out_meta["transform"].a * out_meta["transform"].e)
        ha_per_pixel = pixel_area_m2 / 10000.0

        total_canopy_pixels = int(np.sum(final_mask))
        total_canopy_ha = round(total_canopy_pixels * ha_per_pixel, 2)
        total_canopy_ac = round(total_canopy_ha * 2.47105, 2)

        rcr_pixels = int(np.nansum(class_2d == 1.0))
        rcr_ha = round(rcr_pixels * ha_per_pixel, 2)
        rcr_ac = round(rcr_ha * 2.47105, 2)
        incidence_pct = round((rcr_pixels / total_canopy_pixels) * 100.0, 2) if total_canopy_pixels > 0 else 0.0

        r_bounds = rasterio.transform.array_bounds(h, w, out_meta["transform"])
        poly_utm = poly_gdf.to_crs(out_meta["crs"])

        date_results.append({
            "role": role_label,
            "field_id": field_id,
            "season": str(season),
            "capture_date": target_date_str,
            "capture_date_iso": f"{target_date_str[:4]}-{target_date_str[4:6]}-{target_date_str[6:8]}",
            "early_mask_date": early_date_str,
            "mask_sigma_used": float(mask_sigma),
            "total_canopy_ha": total_canopy_ha,
            "total_canopy_acres": total_canopy_ac,
            "rcr_affected_ha": rcr_ha,
            "rcr_affected_acres": rcr_ac,
            "rcr_affected_area_pct": incidence_pct,
            "rcr_incidence_pct": incidence_pct,
            "threshold_used": threshold,
            "classified_raster": class_out_path.name,
            "probability_raster": prob_out_path.name,
            # Arrays and spatial metadata for plotting
            "_prob_2d": prob_2d,
            "_class_2d": class_2d,
            "_crs": out_meta["crs"],
            "_bounds": r_bounds,
            "_poly_utm": poly_utm,
        })

    summary_df = pd.DataFrame([
        {k: v for k, v in r.items() if not k.startswith("_")}
        for r in date_results
    ])

    # Update persistent summary CSV
    report_csv_path = DIR_REPORTS / "inspection_summary_table.csv"
    save_cols = [
        "field_id", "season", "capture_date", "early_mask_date",
        "total_canopy_ha", "rcr_affected_ha", "rcr_incidence_pct",
        "threshold_used", "classified_raster"
    ]
    if report_csv_path.exists():
        existing_df = pd.read_csv(report_csv_path)
        combined_df = pd.concat([existing_df, summary_df[save_cols]]).drop_duplicates(
            subset=["field_id", "season", "capture_date"], keep="last"
        )
        combined_df.to_csv(report_csv_path, index=False)
    else:
        summary_df[save_cols].to_csv(report_csv_path, index=False)

    return {
        "early_ndvi": early_ndvi,
        "early_mask": canopy_mask_early,
        "early_threshold": ndvi_threshold,
        "mask_sigma": float(mask_sigma),
        "date_results": date_results,
        "summary_df": summary_df,
        "exported_files": exported_files,
        "report_csv_path": report_csv_path
    }


def _upsample_for_high_res_display(
    prob_2d: np.ndarray,
    class_2d: np.ndarray,
    threshold: float,
    scale_factor: int = 5,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Upsamples low-pixel-count 3m field rasters (typically ~100x120 px) by `scale_factor` (5x -> ~500x600 px)
    using normalized masked convolution so NaN background/waterways never bleed into the canopy,
    producing crisp, high-resolution visual maps for the report figure while preserving exact patterns.
    """
    try:
        from scipy.ndimage import zoom

        valid_mask = (~np.isnan(prob_2d)).astype(np.float32)
        if np.sum(valid_mask) == 0:
            return prob_2d, class_2d

        val_filled = np.nan_to_num(prob_2d, nan=0.0).astype(np.float32)
        num_hi = zoom(val_filled * valid_mask, zoom=scale_factor, order=1, prefilter=False)
        den_hi = zoom(valid_mask, zoom=scale_factor, order=1, prefilter=False)

        mask_hi = den_hi >= 0.5
        prob_hi = np.full(num_hi.shape, np.nan, dtype=np.float32)
        prob_hi[mask_hi] = np.clip(num_hi[mask_hi] / den_hi[mask_hi], 0.0, 1.0)

        class_hi = np.full(num_hi.shape, np.nan, dtype=np.float32)
        class_hi[mask_hi] = (prob_hi[mask_hi] >= threshold).astype(np.float32)
        return prob_hi, class_hi
    except Exception:
        return prob_2d, class_2d


def plot_multi_date_comparison_grid(run_res: Dict, field_id: str, season: str) -> Figure:
    """
    Creates an ultra-high-resolution (500 DPI) Google Material Design / Presentation style
    2-row x N-column visual comparison grid across the selected capture dates over an in-memory
    Google Satellite aerial basemap:
      Row 1: RCR probability (0.00 to 1.00)
      Row 2: Binary RCR classification (0 = Asymptomatic, 1 = RCR diseased) with a point legend
    All map titles share the same uniform UIUC Illini Blue color and use sentence case.
    """
    from matplotlib.lines import Line2D
    from mpl_toolkits.axes_grid1 import make_axes_locatable

    results = sorted(run_res["date_results"], key=lambda r: str(r.get("capture_date", "")))
    n_cols = len(results)

    # Size each map cell to the field's own (padded) aspect ratio so the equal-aspect maps fill their
    # cells instead of being letterboxed inside a fixed-size figure (wide fields -> vertical gaps,
    # tall fields -> horizontal gaps).
    h_over_w = 0.75
    if results[0].get("_bounds") is not None:
        vx0, vy0, vx1, vy1 = _expand_view_bounds(results[0]["_bounds"], pad_frac=0.10)
        h_over_w = (vy1 - vy0) / max(1e-6, vx1 - vx0)
    h_over_w = min(2.8, max(0.35, h_over_w))
    map_long_in = 4.4
    map_w = map_long_in if h_over_w <= 1.0 else map_long_in / h_over_w
    map_h = map_w * h_over_w
    col_w = max(3.4, map_w + 0.75)  # + colorbar & tick labels; floor keeps titles/legend from clipping
    header_in = 0.8
    fig_w = max(6.4, col_w * n_cols)
    fig_h = header_in + (0.35 + map_h) + (0.55 + map_h + 0.4)  # row titles, 2-line title + legend
    fig = Figure(figsize=(fig_w, fig_h), dpi=500, facecolor="#FFFFFF")
    axes = fig.subplots(2, n_cols, squeeze=False)

    # Transparent NaN background so the Google Satellite basemap shows clearly outside the canopy mask
    peach_prob_cmap = LinearSegmentedColormap.from_list("rcr_peach_prob", PEACH_PROB_PALETTE, N=256)
    peach_prob_cmap.set_bad((0.0, 0.0, 0.0, 0.0))

    peach_binary_cmap = ListedColormap([DISC_HEALTHY_HEX, DISC_DISEASED_HEX])
    peach_binary_cmap.set_bad((0.0, 0.0, 0.0, 0.0))

    class_legend_handles = [
        Line2D(
            [0], [0],
            marker="o", linestyle="None", markersize=9.5,
            markerfacecolor=DISC_HEALTHY_HEX, markeredgecolor="#C8A98E", markeredgewidth=1.1,
            label="Asymptomatic",
        ),
        Line2D(
            [0], [0],
            marker="o", linestyle="None", markersize=9.5,
            markerfacecolor=DISC_DISEASED_HEX, markeredgecolor="#9E2220", markeredgewidth=1.1,
            label="RCR diseased",
        ),
    ]

    # Cache in-memory Google Satellite basemap by (crs, bounds) so tiles are only fetched once per field
    basemap_cache = {}

    def _render_panel_with_basemap(ax, data_arr, cmap, r_bounds, dst_crs, poly_utm, interp_mode):
        r_xmin, r_ymin, r_xmax, r_ymax = r_bounds
        raster_extent = [r_xmin, r_xmax, r_ymin, r_ymax]

        cache_key = (str(dst_crs), round(r_xmin, 1), round(r_ymin, 1), round(r_xmax, 1), round(r_ymax, 1))
        if cache_key not in basemap_cache:
            basemap_cache[cache_key] = _fetch_google_satellite_basemap_in_memory(
                dst_crs, (r_xmin, r_ymin, r_xmax, r_ymax), pad_frac=0.10
            )
        sat_basemap = basemap_cache[cache_key]

        if sat_basemap is not None:
            sat_rgb, sat_extent = sat_basemap
            ax.imshow(sat_rgb, extent=sat_extent, interpolation="bilinear", zorder=1)
            im = ax.imshow(
                data_arr,
                cmap=cmap,
                vmin=0.0,
                vmax=1.0,
                extent=raster_extent,
                origin="upper",
                interpolation=interp_mode,
                alpha=0.92,
                zorder=3,
            )
            if poly_utm is not None:
                poly_utm.boundary.plot(ax=ax, color="#FFFFFF", linewidth=1.1, alpha=0.85, zorder=4)
            ax.set_xlim(sat_extent[0], sat_extent[1])
            ax.set_ylim(sat_extent[2], sat_extent[3])
        else:
            ax.set_facecolor("#F8FAFC")
            im = ax.imshow(
                data_arr,
                cmap=cmap,
                vmin=0.0,
                vmax=1.0,
                extent=raster_extent,
                origin="upper",
                interpolation=interp_mode,
                zorder=3,
            )
            if poly_utm is not None:
                poly_utm.boundary.plot(ax=ax, color=ILLINI_BLUE, linewidth=1.0, alpha=0.6, zorder=4)
            dx = max((r_xmax - r_xmin) * 0.08, 10.0)
            dy = max((r_ymax - r_ymin) * 0.08, 10.0)
            ax.set_xlim(r_xmin - dx, r_xmax + dx)
            ax.set_ylim(r_ymin - dy, r_ymax + dy)

        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_edgecolor("#DCE2EC")
            spine.set_linewidth(1.1)
        return im

    for col_idx, item in enumerate(results):
        d_iso = item["capture_date_iso"]
        inc = item["rcr_incidence_pct"]
        rcr_ha = item["rcr_affected_ha"]
        rcr_ac = item["rcr_affected_acres"]
        thresh_item = float(item.get("threshold_used", OPTIMAL_THRESHOLD))
        r_bounds = item.get("_bounds")
        dst_crs = item.get("_crs")
        poly_utm = item.get("_poly_utm")

        prob_arr, class_arr = _upsample_for_high_res_display(
            item["_prob_2d"], item["_class_2d"], thresh_item, scale_factor=5
        )

        # Row 0: RCR probability card
        ax0 = axes[0, col_idx]
        im0 = _render_panel_with_basemap(
            ax0, prob_arr, peach_prob_cmap, r_bounds, dst_crs, poly_utm, interp_mode="bilinear"
        )
        ax0.set_title(
            f"{d_iso}  |  RCR probability",
            fontsize=9.0, fontweight="600", color=ILLINI_BLUE, pad=8
        )

        div0 = make_axes_locatable(ax0)
        cax0 = div0.append_axes("right", size="4.2%", pad=0.10)
        cbar0 = fig.colorbar(im0, cax=cax0, ticks=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
        cbar0.outline.set_edgecolor("#DCE2EC")
        cbar0.outline.set_linewidth(0.9)
        cbar0.ax.tick_params(labelsize=7.8, colors="#4A5B75", length=0, pad=4)

        # Row 1: RCR classification card (same title color: ILLINI_BLUE)
        ax1 = axes[1, col_idx]
        _render_panel_with_basemap(
            ax1, class_arr, peach_binary_cmap, r_bounds, dst_crs, poly_utm, interp_mode="nearest"
        )
        ax1.set_title(
            f"{d_iso}  |  RCR classification\nRCR affected area: {inc:.1f}% ({rcr_ha:.2f} ha / {rcr_ac:.2f} ac)",
            fontsize=8.8, fontweight="600", color=ILLINI_BLUE, pad=8
        )

        div1 = make_axes_locatable(ax1)
        cax1_spacer = div1.append_axes("right", size="4.2%", pad=0.10)
        cax1_spacer.axis("off")

        leg = ax1.legend(
            handles=class_legend_handles,
            loc="upper center",
            bbox_to_anchor=(0.5, -0.04),
            ncol=2,
            frameon=True,
            facecolor="#F8FAFC",
            edgecolor="#DCE2EC",
            fontsize=8.0,
            handletextpad=0.35,
            columnspacing=1.2,
            borderpad=0.45,
        )
        for text in leg.get_texts():
            text.set_color(ILLINI_BLUE)
            text.set_fontweight("500")

    early_raw = str(results[0].get("early_mask_date", ""))
    early_iso = f"{early_raw[:4]}-{early_raw[4:6]}-{early_raw[6:8]}" if len(early_raw) == 8 else early_raw
    thresh_val = results[0].get("threshold_used", OPTIMAL_THRESHOLD)
    sigma_val = float(results[0].get("mask_sigma_used", run_res.get("mask_sigma", 3.0)))

    fig.text(
        0.03, 1.0 - 0.38 / fig_h,
        f"Red crown rot (RCR) spatial assessment - Field {field_id} ({season})",
        fontsize=10.5, fontweight="600", color=ILLINI_BLUE
    )
    fig.text(
        0.03, 1.0 - 0.65 / fig_h,
        f"Early canopy mask baseline: {early_iso} (mean - {sigma_val:g} SD)   |   Classification threshold: t = {thresh_val:.2f}   |   Model: RCRC v3.1",
        fontsize=8.3, color="#4A5B75"
    )

    fig.tight_layout(rect=[0.01, 0.1 / fig_h, 0.99, 1.0 - header_in / fig_h], w_pad=1.2, h_pad=0.8)
    return fig




def create_results_zip_bytes(run_res: Dict, pheno_res: Optional[Dict] = None) -> bytes:
    """Bundles all exported GeoTIFFs and CSV tables into an in-memory ZIP archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in run_res.get("exported_files", []):
            if Path(fpath).exists():
                zf.write(fpath, arcname=f"rasters/{Path(fpath).name}")

        summary_csv_bytes = run_res["summary_df"].to_csv(index=False).encode("utf-8")
        zf.writestr("reports/field_inspection_summary.csv", summary_csv_bytes)

        if pheno_res and "df" in pheno_res:
            pheno_bytes = pheno_res["df"].to_csv(index=False).encode("utf-8")
            zf.writestr("reports/sentinel2_phenology_curve.csv", pheno_bytes)

    buf.seek(0)
    return buf.read()
