"""
rcr_worker.py
=============
Isolated CLI subprocess worker for executing GDAL / PROJ / GeoPandas / Rasterio / GEE
operations outside the Streamlit (PyArrow/Tornado) server process.

This prevents Windows Conda DLL collisions (sqlite3.dll / proj.dll / gdal.dll / libiomp5md.dll)
from ever crashing the Streamlit web server.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 1. Configure environment variables & DLL directories BEFORE importing any C-extensions
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

import json
import traceback
from datetime import datetime
from matplotlib.backends.backend_agg import FigureCanvasAgg
import geopandas as gpd
import pandas as pd

from rcr_engine import (
    create_results_zip_bytes,
    download_planet_order_by_id,
    extract_sentinel2_phenology,
    find_available_seasons_for_field,
    get_planet_order_status,
    load_uploaded_or_local_polygon,
    plot_boundary_preview,
    plot_multi_date_comparison_grid,
    plot_phenology_curve,
    run_multi_date_rcr_classification,
    search_planet_catalog_for_window,
    submit_and_download_planet_clip_order,
)


def _save_fig_agg(fig, out_path: Path, dpi: int = 500):
    """Saves both a 500-DPI PNG and a vector SVG using pure Agg/SVG canvas without relying on pyplot."""
    FigureCanvasAgg(fig)
    fig.savefig(
        str(out_path),
        format="png",
        dpi=dpi,
        bbox_inches="tight",
        pad_inches=0.12,
        facecolor=fig.get_facecolor(),
    )
    svg_path = out_path.with_suffix(".svg")
    fig.savefig(
        str(svg_path),
        format="svg",
        dpi=dpi,
        bbox_inches="tight",
        pad_inches=0.12,
        facecolor=fig.get_facecolor(),
    )


def _load_geojson_pure(geojson_path: str | Path) -> gpd.GeoDataFrame:
    """Loads a WGS84 GeoJSON file in pure Python/Shapely without invoking GDAL OGR driver."""
    raw = json.loads(Path(geojson_path).read_text(encoding="utf-8"))
    return gpd.GeoDataFrame.from_features(raw["features"], crs="EPSG:4326")


def main():
    if len(sys.argv) < 3:
        print("Usage: python rcr_worker.py <request.json> <response.json>", file=sys.stderr)
        sys.exit(2)

    req_path = Path(sys.argv[1])
    resp_path = Path(sys.argv[2])

    try:
        req = json.loads(req_path.read_text(encoding="utf-8"))
        action = req["action"]
        out_dir = Path(req.get("out_dir", req_path.parent))
        out_dir.mkdir(parents=True, exist_ok=True)

        if action == "load_polygon":
            input_path = Path(req["input_path"])
            field_id = req.get("field_id")
            gdf_wgs84, resolved_id, area_ha, area_ac = load_uploaded_or_local_polygon(
                input_path, filename=input_path.name, field_id=field_id
            )

            geojson_path = out_dir / "active_boundary.geojson"
            geojson_path.write_text(gdf_wgs84.to_json(), encoding="utf-8")

            preview_png = out_dir / "boundary_preview.png"
            fig = plot_boundary_preview(gdf_wgs84, resolved_id)
            _save_fig_agg(fig, preview_png, dpi=500)

            avail_seasons = find_available_seasons_for_field(resolved_id)

            resp = {
                "status": "ok",
                "field_id": resolved_id,
                "area_ha": area_ha,
                "area_acres": area_ac,
                "boundary_geojson_path": str(geojson_path),
                "preview_png_path": str(preview_png),
                "available_seasons": avail_seasons,
            }

        elif action == "run_phenology":
            poly_gdf = _load_geojson_pure(req["boundary_geojson_path"])
            field_id = req["field_id"]
            year = int(req["year"])
            target_drop_ratio = float(req.get("target_drop_ratio", 0.75))
            gee_project = req.get("gee_project", "ee-brunodp385")
            use_cached = bool(req.get("use_cached", True))

            pheno_res = extract_sentinel2_phenology(
                poly_gdf=poly_gdf,
                field_id=field_id,
                year=year,
                target_drop_ratio=target_drop_ratio,
                gee_project=gee_project,
                use_cached_if_available=use_cached,
            )

            pheno_png = out_dir / f"{field_id}_{year}_phenology.png"
            fig = plot_phenology_curve(pheno_res, field_id, year)
            _save_fig_agg(fig, pheno_png, dpi=500)

            resp = {
                "status": "ok",
                "source": pheno_res["source"],
                "csv_path": str(pheno_res["csv_path"]),
                "pheno_png_path": str(pheno_png),
                "peak_date": pheno_res["peak_date"].strftime("%Y-%m-%d"),
                "peak_ndvi": round(float(pheno_res["peak_ndvi"]), 4),
                "target_cutoff_ndvi": round(float(pheno_res["target_cutoff_ndvi"]), 4),
                "target_date": pheno_res["target_date"].strftime("%Y-%m-%d"),
                "actual_target_ndvi": round(float(pheno_res["actual_target_ndvi"]), 4),
                "before_date": pheno_res["before_date"].strftime("%Y-%m-%d"),
                "before_ndvi": round(float(pheno_res.get("before_ndvi", 0.0)), 4),
                "after_date": pheno_res["after_date"].strftime("%Y-%m-%d"),
                "after_ndvi": round(float(pheno_res.get("after_ndvi", 0.0)), 4),
            }

        elif action == "search_planet":
            poly_gdf = _load_geojson_pure(req["boundary_geojson_path"])
            peak_dt = datetime.strptime(req["peak_date"], "%Y-%m-%d") if req.get("peak_date") else None
            target_dt = datetime.strptime(req["target_date"], "%Y-%m-%d") if req.get("target_date") else None
            before_dt = datetime.strptime(req["before_date"], "%Y-%m-%d") if req.get("before_date") else None
            after_dt = datetime.strptime(req["after_date"], "%Y-%m-%d") if req.get("after_date") else None
            season = str(req.get("season", ""))
            start_date_str = req.get("start_date")
            end_date_str = req.get("end_date")

            sr = search_planet_catalog_for_window(
                api_key=req["api_key"],
                poly_gdf=poly_gdf,
                peak_date=peak_dt,
                target_date=target_dt,
                before_date=before_dt,
                after_date=after_dt,
                season=season,
                start_date_str=start_date_str,
                end_date_str=end_date_str,
                max_cloud_cover=float(req.get("max_cloud_cover", 0.20)),
            )

            def _clean_scene(s):
                if not s:
                    return None
                return {
                    k: (v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v) if isinstance(v, pd.Timedelta) else v)
                    for k, v in s.items()
                    if k not in ("date_dt", "diff_peak", "diff_target", "diff_before", "diff_after")
                }

            all_scenes_clean = [_clean_scene(s) for s in sr.get("all_scenes", [])]

            resp = {
                "status": "ok",
                "all_scenes": all_scenes_clean,
                "suggested_scene_ids": sr.get("suggested_scene_ids", []),
                "early_baseline": _clean_scene(sr.get("early_baseline")),
                "before_target": _clean_scene(sr.get("before_target")),
                "optimal_target": _clean_scene(sr.get("optimal_target")),
                "after_target": _clean_scene(sr.get("after_target")),
                "search_window": sr.get("search_window", ""),
                "total_scenes_found": sr.get("total_scenes_found", len(all_scenes_clean)),
            }

        elif action == "order_planet":
            poly_gdf = _load_geojson_pure(req["boundary_geojson_path"])
            status_file = out_dir / "planet_order_status.txt"

            def _cb(msg: str):
                status_file.write_text(msg, encoding="utf-8")

            max_wait = int(req.get("max_wait_minutes", 30))
            order_res = submit_and_download_planet_clip_order(
                api_key=req["api_key"],
                poly_gdf=poly_gdf,
                scene_ids=req["scene_ids"],
                field_id=req["field_id"],
                season=str(req["season"]),
                status_callback=_cb,
                max_wait_minutes=max_wait,
            )
            resp = order_res

        elif action == "resume_planet_order":
            order_id = str(req["order_id"]).strip()
            field_id = req["field_id"]
            season = str(req["season"])
            status_file = out_dir / "planet_order_status.txt"

            def _cb(msg: str):
                status_file.write_text(msg, encoding="utf-8")

            max_wait = int(req.get("max_wait_minutes", 30))
            order_res = download_planet_order_by_id(
                api_key=req["api_key"],
                order_id=order_id,
                field_id=field_id,
                season=season,
                status_callback=_cb,
                max_wait_minutes=max_wait,
            )
            resp = order_res

        elif action == "check_planet_order":
            order_id = str(req["order_id"]).strip()
            status_info = get_planet_order_status(
                api_key=req["api_key"],
                order_id=order_id,
            )
            resp = {
                "status": "ok",
                "order_info": status_info,
            }

        elif action == "run_classification":
            poly_gdf = _load_geojson_pure(req["boundary_geojson_path"])
            field_id = req["field_id"]
            season = str(req["season"])
            early_tif_path = Path(req["early_tif_path"])
            early_date_str = str(req["early_date_str"])
            target_captures = [
                (str(role), str(d_str), Path(t_path))
                for role, d_str, t_path in req["target_captures"]
            ]
            threshold = float(req.get("threshold", 0.48))
            mask_sigma = float(req.get("mask_sigma", 3.0))

            run_res = run_multi_date_rcr_classification(
                field_id=field_id,
                season=season,
                poly_gdf=poly_gdf,
                early_tif_path=early_tif_path,
                early_date_str=early_date_str,
                target_captures=target_captures,
                threshold=threshold,
                mask_sigma=mask_sigma,
            )

            grid_png = out_dir / f"{field_id}_{season}_multi_date_grid.png"
            fig_grid = plot_multi_date_comparison_grid(run_res, field_id, season)
            _save_fig_agg(fig_grid, grid_png, dpi=500)

            summary_csv = out_dir / f"{field_id}_{season}_rcr_summary.csv"
            run_res["summary_df"].to_csv(summary_csv, index=False)

            pheno_mock = None
            pheno_csv_path = req.get("pheno_csv_path")
            if pheno_csv_path and Path(pheno_csv_path).exists():
                pheno_mock = {"df": pd.read_csv(pheno_csv_path)}

            zip_path = out_dir / f"{field_id}_{season}_rcr_outputs.zip"
            zip_bytes = create_results_zip_bytes(run_res, pheno_mock)
            zip_path.write_bytes(zip_bytes)

            clean_results = [
                {k: v for k, v in r.items() if not k.startswith("_")}
                for r in run_res["date_results"]
            ]

            resp = {
                "status": "ok",
                "date_results": clean_results,
                "grid_png_path": str(grid_png),
                "summary_csv_path": str(summary_csv),
                "zip_path": str(zip_path),
            }

        else:
            raise ValueError(f"Unknown worker action: {action}")

        resp_path.write_text(json.dumps(resp, indent=2), encoding="utf-8")

    except Exception as exc:
        err_resp = {
            "status": "error",
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        resp_path.write_text(json.dumps(err_resp, indent=2), encoding="utf-8")
        sys.exit(1)


if __name__ == "__main__":
    main()

