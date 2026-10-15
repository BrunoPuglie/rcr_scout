"""
app.py
======
Red crown rot scouting
Smart Plant Pathology Lab — University of Illinois Urbana-Champaign

Architecture Note (Windows Conda Stability):
  Streamlit loads PyArrow/Tornado C++ libraries on startup, which collide with
  GDAL / PROJ / Spatialite DLLs in the same Windows process.
  Therefore, this UI process NEVER imports geopandas, rasterio, or matplotlib.
  All geospatial, GEE, Planet API, and model raster operations execute inside an
  isolated worker subprocess (`rcr_worker.py`), making it impossible for native
  GIS libraries to crash the Streamlit connection.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

# ==============================================================================
# 1. PATHS, CONSTANTS & SUBPROCESS WORKER RUNNER
# ==============================================================================

APP_DIR = Path(__file__).resolve().parent
STANDALONE_ROOT = APP_DIR.parent
WORKER_SCRIPT = APP_DIR / "rcr_worker.py"

WORKSPACE_DIR = STANDALONE_ROOT / "workspace"
SAT_BASE_DIR = WORKSPACE_DIR / "satellite_imagery"
for _p in [WORKSPACE_DIR, SAT_BASE_DIR, WORKSPACE_DIR / "field_boundaries", WORKSPACE_DIR / "outputs"]:
    _p.mkdir(parents=True, exist_ok=True)


def _get_default_gee_project() -> str:
    env_val = os.environ.get("EARTHENGINE_PROJECT") or os.environ.get("GEE_PROJECT")
    if env_val:
        return env_val
    try:
        if hasattr(st, "secrets") and "EARTHENGINE_PROJECT" in st.secrets:
            return str(st.secrets["EARTHENGINE_PROJECT"]).strip()
    except Exception:
        pass
    if os.environ.get("USERNAME", "").lower() == "brunodp":
        return "ee-brunodp385"
    return ""


def _setup_cloud_earth_engine_credentials() -> None:
    """
    If running on Streamlit Cloud and EARTHENGINE_TOKEN / EARTHENGINE_CREDENTIALS
    is present in st.secrets, writes it to ~/.config/earthengine/credentials
    so standard Earth Engine OAuth works seamlessly in the cloud container.
    """
    token_str = None
    try:
        if hasattr(st, "secrets"):
            if "EARTHENGINE_TOKEN" in st.secrets:
                token_str = str(st.secrets["EARTHENGINE_TOKEN"]).strip()
            elif "EARTHENGINE_CREDENTIALS" in st.secrets:
                token_str = str(st.secrets["EARTHENGINE_CREDENTIALS"]).strip()
    except Exception:
        pass
    if not token_str:
        token_str = os.environ.get("EARTHENGINE_TOKEN") or os.environ.get("EARTHENGINE_CREDENTIALS")

    if token_str:
        try:
            ee_dir = Path.home() / ".config" / "earthengine"
            ee_dir.mkdir(parents=True, exist_ok=True)
            cred_file = ee_dir / "credentials"
            cred_file.write_text(token_str, encoding="utf-8")
        except Exception:
            pass


_setup_cloud_earth_engine_credentials()
DEFAULT_GEE_PROJECT = _get_default_gee_project()
OPTIMAL_THRESHOLD = 0.48
TARGET_DROP_RATIO = 0.80


def get_session_workdir() -> Path:
    """Returns a persistent temporary workspace directory for the current browser session."""
    if "session_workdir" not in st.session_state or not Path(st.session_state["session_workdir"]).exists():
        st.session_state["session_workdir"] = tempfile.mkdtemp(prefix="rcr_session_")
    return Path(st.session_state["session_workdir"])


def get_library_boundary_files() -> Dict[str, Path]:
    """Finds all available pre-loaded field boundary files in workspace/field_boundaries and project folders."""
    search_dirs = [
        WORKSPACE_DIR / "field_boundaries",
        STANDALONE_ROOT.parent / "rcrc" / "data" / "raw" / "vectors" / "field_boudaries",
        STANDALONE_ROOT.parent / "rcrc" / "data" / "raw" / "vectors" / "field_boundaries",
    ]
    files_map = {}
    for sdir in search_dirs:
        if sdir.exists():
            for p in sorted(sdir.glob("*.shp")) + sorted(sdir.glob("*.geojson")) + sorted(sdir.glob("*.gpkg")):
                if "sample" in p.name.lower():
                    continue
                clean_name = re.sub(r"^(boun[d]?|boundary|field)[_-]?", "", p.stem, flags=re.IGNORECASE).strip().upper()
                label = f"{clean_name} ({p.name})"
                if label not in files_map:
                    files_map[label] = p
    return files_map


def _build_conda_worker_env() -> Dict[str, str]:
    """
    Builds a clean environment dictionary with Conda's Library\\bin, mingw-w64\\bin,
    and Scripts prepended to PATH so the Windows child process loader resolves all
    delay-loaded C++ DLLs (preventing 0xC06D007F STATUS_DELAY_LOAD_FAILED), while
    also configuring Linux / cloud environments properly.
    """
    env = os.environ.copy()
    env_prefix = Path(sys.executable).resolve().parent
    conda_root = env_prefix.parent if env_prefix.name.lower() in ("bin", "scripts") else env_prefix

    conda_dll_dirs = [
        env_prefix,
        env_prefix / "Library" / "mingw-w64" / "bin",
        env_prefix / "Library" / "usr" / "bin",
        env_prefix / "Library" / "bin",
        env_prefix / "Scripts",
        env_prefix / "bin",
        conda_root / "bin",
    ]
    existing_dirs = [str(d) for d in conda_dll_dirs if d.exists()]
    env["PATH"] = os.pathsep.join(existing_dirs + [env.get("PATH", "")])

    gdal_dir = env_prefix / "Library" / "share" / "gdal"
    proj_dir = env_prefix / "Library" / "share" / "proj"
    if gdal_dir.exists():
        env["GDAL_DATA"] = str(gdal_dir)
    elif (conda_root / "share" / "gdal").exists() and "GDAL_DATA" not in env:
        env["GDAL_DATA"] = str(conda_root / "share" / "gdal")

    if proj_dir.exists():
        env["PROJ_LIB"] = str(proj_dir)
    elif (conda_root / "share" / "proj").exists() and "PROJ_LIB" not in env:
        env["PROJ_LIB"] = str(conda_root / "share" / "proj")

    # Pass app paths in PYTHONPATH for cross-platform subprocess importing
    app_paths = [str(APP_DIR), str(STANDALONE_ROOT)]
    if "PYTHONPATH" in env and env["PYTHONPATH"]:
        env["PYTHONPATH"] = os.pathsep.join(app_paths + [env["PYTHONPATH"]])
    else:
        env["PYTHONPATH"] = os.pathsep.join(app_paths)

    # Pass Streamlit secrets for GEE and Planet if present
    try:
        if hasattr(st, "secrets"):
            for k in [
                "EARTHENGINE_TOKEN",
                "EARTHENGINE_CREDENTIALS",
                "EARTHENGINE_PROJECT",
                "EARTHENGINE_SERVICE_ACCOUNT",
                "EARTHENGINE_PRIVATE_KEY",
                "PLANET_API_KEY",
            ]:
                if k in st.secrets and k not in env:
                    env[k] = str(st.secrets[k])
    except Exception:
        pass

    env["SHAPE_RESTORE_SHX"] = "YES"
    env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    env["MPLBACKEND"] = "Agg"
    return env


def run_worker(payload: Dict) -> Dict:
    """
    Executes `rcr_worker.py` in a clean, isolated Python subprocess with Conda's
    full DLL PATH set prior to process creation.
    """
    workdir = get_session_workdir()
    payload = dict(payload)
    payload.setdefault("out_dir", str(workdir))

    req_file = workdir / "worker_request.json"
    resp_file = workdir / "worker_response.json"
    if resp_file.exists():
        resp_file.unlink()

    req_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(WORKER_SCRIPT), str(req_file), str(resp_file)],
        cwd=str(STANDALONE_ROOT),
        env=_build_conda_worker_env(),
        capture_output=True,
        text=True,
    )

    if resp_file.exists():
        resp = json.loads(resp_file.read_text(encoding="utf-8"))
        if resp.get("status") == "ok":
            return resp
        raise RuntimeError(resp.get("message", "Worker failed") + "\n" + resp.get("traceback", ""))

    raise RuntimeError(
        f"Subprocess exited with code {proc.returncode}.\n"
        f"STDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout}"
    )


def parse_date_from_filename(filename: str) -> Optional[str]:
    match = re.search(r"(202[0-9][01][0-9][0-3][0-9])", filename)
    return match.group(1) if match else None


def format_yyyymmdd_iso(d_str: str) -> str:
    if len(d_str) == 8 and d_str.isdigit():
        return f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]}"
    return d_str


def render_crisp_figure(
    png_path_str: str,
    use_svg_if_available: bool = True,
    max_height_px: Optional[int] = None,
    max_width_px: Optional[int] = None,
    full_width: bool = False,
) -> None:
    """
    Renders a figure directly via an HTML <img> tag with base64 data URI.
    This completely bypasses Streamlit's `st.image()` server-side thumbnail downscaler,
    delivering infinite-resolution vector SVG (or untouched 500-DPI PNG) to the browser.
    """
    import base64

    png_path = Path(png_path_str)
    svg_path = png_path.with_suffix(".svg")

    if use_svg_if_available and svg_path.exists():
        b64 = base64.b64encode(svg_path.read_bytes()).decode("ascii")
        mime = "image/svg+xml"
    elif png_path.exists():
        b64 = base64.b64encode(png_path.read_bytes()).decode("ascii")
        mime = "image/png"
    else:
        return

    style_parts = ["display:block;", "border-radius:8px;", "margin:0 auto;"]
    if full_width:
        style_parts.append(f"width:100%; height:auto; max-height:{max_height_px or 650}px; object-fit:contain;")
    elif max_height_px is not None:
        style_parts.append(f"max-height:{max_height_px}px; width:auto; max-width:100%;")
    elif max_width_px is not None:
        style_parts.append(f"width:100%; max-width:{max_width_px}px; height:auto;")
    else:
        style_parts.append("width:100%; height:auto;")
    img_style = " ".join(style_parts)

    st.markdown(
        f'<div style="background:#FFFFFF; border:1px solid #DCE2EC; border-radius:12px; '
        f'padding:0.65rem; box-shadow:0 1px 3px rgba(19,41,75,0.06); margin-bottom:0.85rem;">'
        f'<img src="data:{mime};base64,{b64}" style="{img_style}" />'
        f'</div>',
        unsafe_allow_html=True,
    )


# ==============================================================================
# 2. PAGE CONFIGURATION & GOOGLE MATERIAL DESIGN + UIUC THEME
# ==============================================================================

st.set_page_config(
    page_title="Red crown rot scouting",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Montserrat:ital,wght@0,300;0,400;0,500;0,600;0,700;0,800;1,400&display=swap');

    :root {
        --illini-blue: #13294B;
        --illini-blue-accent: #1D58A7;
        --illini-blue-light: #E8F0FE;
        --illini-orange: #FF5F05;
        --illini-orange-light: #FFF3EC;
        --md-surface: #FFFFFF;
        --md-bg: #F4F6F9;
        --md-outline: #DCE2EC;
        --md-text-primary: #13294B;
        --md-text-secondary: #4A5B75;
    }

    html, body, .stApp, .stMarkdown, p, div, span, label, h1, h2, h3, h4, h5, h6,
    button, input, select, textarea, table, th, td,
    [data-testid="stMetricValue"], [data-testid="stMetricLabel"], [data-testid="stMetricDelta"] {
        font-family: 'Montserrat', -apple-system, BlinkMacSystemFont, sans-serif !important;
    }

    /* Preserve Streamlit's native Material Icon font for UI controls */
    [data-testid="stIconMaterial"], .material-icons {
        font-family: 'Material Symbols Rounded' !important;
    }

    .stApp {
        background-color: var(--md-bg);
        color: var(--md-text-primary);
    }

    .block-container {
        padding-top: 1.5rem !important;
        padding-bottom: 2.5rem !important;
        max-width: 1360px;
    }

    /* Material Design Hero App Bar — Solid Fill */
    .md-hero-card {
        background-color: #13294B;
        color: #FFFFFF;
        border-radius: 16px;
        padding: 1.6rem 2rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 4px 14px rgba(19, 41, 75, 0.14), 0 1px 4px rgba(19, 41, 75, 0.08);
        border-bottom: 5px solid #FF5F05;
        display: flex;
        flex-wrap: wrap;
        justify-content: space-between;
        align-items: center;
        gap: 1rem;
    }

    .md-hero-title {
        font-size: 1.85rem;
        font-weight: 700;
        letter-spacing: -0.01em;
        margin: 0 0 0.35rem 0;
        color: #FFFFFF;
    }

    .md-hero-subtitle {
        font-size: 0.96rem;
        font-weight: 400;
        color: #E2E8F0;
        margin: 0;
        line-height: 1.45;
    }

    .md-lab-badge {
        background-color: #1B3661;
        border: 1px solid rgba(255, 255, 255, 0.22);
        border-left: 4px solid #FF5F05;
        border-radius: 12px;
        padding: 0.75rem 1.1rem;
        font-size: 0.85rem;
        line-height: 1.5;
        color: #FFFFFF;
        min-width: 295px;
    }

    .md-lab-badge a {
        color: #FFB38A !important;
        text-decoration: none;
        font-weight: 500;
    }

    .md-lab-badge a:hover {
        text-decoration: underline;
    }

    /* Material Design Step Header Card (Primary Section Heading: 1.25rem) */
    .md-step-header {
        background-color: var(--md-surface);
        border-radius: 12px;
        padding: 1.10rem 1.45rem;
        margin-top: 1.6rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 1px 4px rgba(19, 41, 75, 0.08), 0 1px 2px rgba(19, 41, 75, 0.04);
        border: 1px solid var(--md-outline);
        border-left: 6px solid var(--illini-blue);
        display: flex;
        align-items: center;
        gap: 0.95rem;
    }

    /* Generous spacing divider between platform steps */
    .md-step-divider {
        height: 1px;
        background: linear-gradient(90deg, rgba(19, 41, 75, 0.02) 0%, rgba(19, 41, 75, 0.16) 50%, rgba(19, 41, 75, 0.02) 100%);
        margin: 4.2rem 0 2.2rem 0;
        border: none;
    }

    .md-step-chip {
        background-color: var(--illini-orange);
        color: #FFFFFF;
        font-weight: 700;
        font-size: 0.80rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        padding: 0.30rem 0.75rem;
        border-radius: 999px;
        white-space: nowrap;
        box-shadow: 0 2px 5px rgba(255, 95, 5, 0.28);
    }

    .md-step-title {
        color: var(--illini-blue);
        font-weight: 700;
        font-size: 1.25rem;
        line-height: 1.3;
        margin: 0;
    }

    /* Subordinate Output Sub-Section Titles inside a Step (0.96rem < 1.25rem Step Title) */
    .md-output-title,
    .stMarkdown h3,
    .stMarkdown h4 {
        color: var(--illini-blue) !important;
        font-weight: 600 !important;
        font-size: 0.96rem !important;
        line-height: 1.35 !important;
        margin: 0.9rem 0 0.45rem 0 !important;
        padding: 0 !important;
    }

    .md-sidebar-title {
        color: var(--illini-blue);
        font-weight: 700;
        font-size: 0.98rem;
        margin: 0.2rem 0 0.65rem 0;
    }

    /* Material Elevated Cards for Date Recommendations */
    .md-date-card-blue {
        background-color: #FFFFFF;
        border: 1px solid #D3E1F8;
        border-left: 5px solid #1D58A7;
        border-radius: 10px;
        padding: 0.8rem 1rem;
        margin-bottom: 0.65rem;
        box-shadow: 0 1px 3px rgba(19, 41, 75, 0.06);
    }

    .md-date-card-orange {
        background-color: #FFF9F5;
        border: 1px solid #FFD6BF;
        border-left: 5px solid #FF5F05;
        border-radius: 10px;
        padding: 0.85rem 1rem;
        margin-bottom: 0.65rem;
        box-shadow: 0 2px 6px rgba(255, 95, 5, 0.10);
    }

    .md-date-card-title {
        font-size: 0.86rem;
        font-weight: 700;
        color: var(--illini-blue);
        margin-bottom: 0.2rem;
    }

    .md-date-pill-blue {
        display: inline-block;
        background-color: #E8F0FE;
        color: #13294B;
        font-weight: 700;
        font-size: 0.86rem;
        padding: 0.14rem 0.52rem;
        border-radius: 6px;
        border: 1px solid #BBD1F8;
    }

    .md-date-pill-orange {
        display: inline-block;
        background-color: #FF5F05;
        color: #FFFFFF;
        font-weight: 700;
        font-size: 0.86rem;
        padding: 0.14rem 0.52rem;
        border-radius: 6px;
    }

    /* Material Buttons & Subordinate Metrics */
    div.stButton > button[kind="primary"] {
        background-color: #FF5F05 !important;
        color: #FFFFFF !important;
        border: none !important;
        border-radius: 999px !important;
        padding: 0.52rem 1.35rem !important;
        font-size: 0.90rem !important;
        font-weight: 600 !important;
        letter-spacing: 0.01em !important;
        box-shadow: 0 2px 6px rgba(255, 95, 5, 0.30) !important;
        transition: all 0.2s ease !important;
    }

    div.stButton > button[kind="primary"]:hover {
        background-color: #E05200 !important;
        box-shadow: 0 4px 12px rgba(255, 95, 5, 0.42) !important;
    }

    div.stButton > button[kind="secondary"] {
        border-radius: 999px !important;
        border: 1.5px solid #13294B !important;
        color: #13294B !important;
        font-size: 0.88rem !important;
        font-weight: 600 !important;
        background-color: #FFFFFF !important;
    }

    div[data-testid="stMetric"] {
        background-color: #FFFFFF;
        border: 1px solid var(--md-outline);
        border-top: 3px solid var(--illini-blue);
        border-radius: 12px;
        padding: 0.65rem 0.95rem;
        box-shadow: 0 1px 3px rgba(19, 41, 75, 0.06);
    }

    div[data-testid="stMetricLabel"] p,
    div[data-testid="stMetricLabel"] {
        font-size: 0.80rem !important;
        font-weight: 600 !important;
        color: var(--md-text-secondary) !important;
    }

    div[data-testid="stMetricValue"],
    div[data-testid="stMetricValue"] > div {
        font-size: 1.08rem !important;
        font-weight: 700 !important;
        color: var(--illini-blue) !important;
    }

    div[data-testid="stMetricDelta"],
    div[data-testid="stMetricDelta"] > div {
        font-size: 0.78rem !important;
        font-weight: 500 !important;
    }

    /* Centered Institutional Footer */
    .md-footer {
        background-color: #FFFFFF;
        border: 1px solid var(--md-outline);
        border-top: 4px solid #13294B;
        border-radius: 12px;
        padding: 1.1rem 1.5rem;
        margin-top: 4.5rem;
        margin-bottom: 2rem;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        text-align: center;
        gap: 0.35rem;
        font-size: 0.86rem;
        color: var(--md-text-secondary);
        box-shadow: 0 1px 3px rgba(19, 41, 75, 0.06);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Material Hero Header (Solid Illini Blue Fill)
st.markdown(
    """
    <div class="md-hero-card">
        <div>
            <div class="md-hero-title">Red crown rot scouting</div>
            <p class="md-hero-subtitle">
                Upload field boundary &rarr; Select soybean season &amp; Sentinel-2 trajectory &rarr;
                Stage PlanetScope imagery &rarr; Generate RCR probability &amp; classification maps
            </p>
        </div>
        <div class="md-lab-badge">
            <div style="font-weight:700; font-size:0.92rem; color:#FFFFFF; margin-bottom:2px;">
                🏛️ Smart Plant Pathology Lab
            </div>
            <div style="color:#CBD5E1; font-weight:500; margin-bottom:6px;">
                University of Illinois Urbana-Champaign
            </div>
            <div>
                📧 <b>Dr. Boris X. Camiletti:</b> <a href="mailto:bxc@illinois.edu">bxc@illinois.edu</a><br/>
                📧 <b>Dr. Bruno D. Pugliese:</b> <a href="mailto:brunodp@illinois.edu">brunodp@illinois.edu</a>
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

for key, default in [
    ("poly_info", None),
    ("poly_sig", ""),
    ("pheno_res", None),
    ("planet_search_res", None),
    ("api_downloaded_files", []),
    ("run_res", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default

with st.sidebar:
    st.markdown(
        """
        <div style="background-color:#13294B; color:white; padding:0.9rem 1rem; border-radius:10px; border-bottom:4px solid #FF5F05; margin-bottom:1rem;">
            <div style="font-weight:700; font-size:1.0rem;">Smart Plant Pathology Lab</div>
            <div style="font-size:0.8rem; color:#CBD5E1;">University of Illinois Urbana-Champaign</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    gee_project_raw = st.text_input(
        "Google Earth Engine project ID",
        value=st.session_state.get("gee_project", DEFAULT_GEE_PROJECT),
        placeholder="e.g. ee-yourusername or gcp-project-id",
        help="Required for Step 2 Sentinel-2 phenology curve. Enter your personal GEE Project ID (e.g. 'ee-username'). Can be left blank if you already configured a default project via 'earthengine set_project' or if you skip Step 2.",
    )
    st.session_state["gee_project"] = gee_project_raw.strip()
    gee_project = st.session_state["gee_project"]

    with st.expander("🔑 Earth Engine Access & Credentials", expanded=False):
        st.markdown(
            "Step 2 connects to Google Earth Engine to extract public Sentinel-2 satellite data.\n\n"
            "**Access options:**\n"
            "- **Platform Default**: If the lab configured access, you can run Step 2 directly.\n"
            "- **Personal Account**: Upload a Service Account key (`.json`) or paste your credentials token below.\n"
            "- **No Account**: Skip Step 2! You can proceed directly to **Step 3 (Stage Imagery)**."
        )

        with st.popover("📖 5-Minute Guide for Beginners (Click to read)", use_container_width=True):
            st.markdown(
                """
                ### 🚀 5-Minute Guide to Getting Your GEE Key

                #### 💡 Important: Do you actually need this?
                If you already downloaded your PlanetScope imagery, **you can completely SKIP Step 2!** You do not need Google Earth Engine to detect or map Red Crown Rot. Proceed directly to **Step 3 (Stage Imagery)**.

                ---

                #### How to get your free Service Account Key (.json):

                ##### Step 1: Sign up for Earth Engine (Free)
                - Go to [code.earthengine.google.com](https://code.earthengine.google.com) and log in with any Google account.
                - Select **Unpaid / Academic / Research** use.
                - Follow the prompt to create a Google Cloud Project (e.g. `ee-yourname`).

                ##### Step 2: Find your Project ID
                - Look at the top-right corner of the Earth Engine screen for your project ID (e.g. `ee-yourname`).
                - Paste that into the **Google Earth Engine project ID** field above.

                ##### Step 3: Generate your free Service Account Key (`.json`)
                Because this web platform runs in the cloud, Google uses a Service Account key to authenticate your cloud requests:
                - Go to the [Google Cloud Service Accounts Console](https://console.cloud.google.com/iam-admin/serviceaccounts).
                - Confirm your project is selected in the top dropdown.
                - Click **+ CREATE SERVICE ACCOUNT** (name it e.g. `rcr-web` and click *Create and Continue*).
                - In the **Role** dropdown, select **Earth Engine** → **Earth Engine User** (or *Resource Viewer*), then click *Done*.
                - Click on the newly created service account email.
                - Click the **KEYS** tab at the top → **ADD KEY** → **Create new key** → select **JSON** → click **Create**.
                - A `.json` file will automatically download to your computer!

                ##### Step 4: Upload the file
                - Drag & drop that downloaded `.json` file into the box below.
                - You are now ready to run Step 2!
                """
            )

        custom_cred_file = st.file_uploader(
            "Upload Service Account key (.json):",
            type=["json"],
            help="Upload a Google Cloud Service Account JSON key with Earth Engine permissions.",
            key="custom_ee_file",
        )
        custom_cred_text = st.text_area(
            "Or paste credentials JSON / Token:",
            value=st.session_state.get("custom_ee_credentials", ""),
            placeholder='{"redirect_uri": ...} or {"type": "service_account", ...}',
            help="Paste your Earth Engine token or service account JSON string here.",
            key="custom_ee_text",
            height=90,
        )
        resolved_custom_cred = ""
        if custom_cred_file is not None:
            try:
                resolved_custom_cred = custom_cred_file.getvalue().decode("utf-8")
            except Exception:
                pass
        elif custom_cred_text.strip():
            resolved_custom_cred = custom_cred_text.strip()

        if resolved_custom_cred:
            st.session_state["custom_ee_credentials"] = resolved_custom_cred
            st.caption("✅ Custom Earth Engine credentials loaded for this session.")

        st.markdown(
            "<small style='color:#64748B;'>New to Earth Engine? Register free at "
            "<a href='https://code.earthengine.google.com' target='_blank'>code.earthengine.google.com</a></small>",
            unsafe_allow_html=True,
        )
    target_drop_pct = st.slider(
        "Target senescence cutoff (% of peak NDVI)",
        min_value=60,
        max_value=90,
        value=int(TARGET_DROP_RATIO * 100),
        step=5,
        help="80% of peak canopy NDVI along the continuous trajectory line is the optimal scouting point for red crown rot.",
    )
    mask_sigma = st.slider(
        "Early canopy mask threshold (SD below mean)",
        min_value=0.5,
        max_value=5.0,
        value=3.0,
        step=0.25,
        help="Pixels with early-season NDVI below (mean - k * SD) are masked out as non-canopy. Standard value is 3.0.",
    )
    prob_threshold = st.slider(
        "RCR classification threshold",
        min_value=0.30,
        max_value=0.70,
        value=float(OPTIMAL_THRESHOLD),
        step=0.02,
        help="Optimal probability cutoff is 0.48.",
    )
    use_cached_pheno = st.checkbox(
        "Use cached Sentinel-2 curve if available",
        value=True,
        help="Uncheck to force a live Google Earth Engine query every time.",
    )
    st.divider()
    st.markdown(
        "**Model architecture:**\n"
        "- **Model version:** `RCRC v3.1`"
    )
    st.divider()
    st.markdown(
        "**Contact information:**\n"
        "- **Dr. Boris X. Camiletti**\n"
        "  [bxc@illinois.edu](mailto:bxc@illinois.edu)\n"
        "- **Dr. Bruno D. Pugliese**\n"
        "  [brunodp@illinois.edu](mailto:brunodp@illinois.edu)"
    )

# ==============================================================================
# STEP 1: UPLOAD FIELD POLYGON
# ==============================================================================

st.markdown(
    """
    <div class="md-step-header">
        <span class="md-step-chip">Step 1</span>
        <span class="md-step-title">Upload field boundary polygon</span>
    </div>
    """,
    unsafe_allow_html=True,
)

# Container placed at the beginning of Step 1 so Field ID input appears at the top
step1_fid_container = st.container()

# Container placed ABOVE boundary source so area metrics and polygon preview appear on top
step1_preview_container = st.container()

lib_boundaries = get_library_boundary_files()

boundary_options = ["📤 Upload custom boundary files (.shp, .zip, .geojson, .gpkg, .kml)"]
if lib_boundaries:
    boundary_options.append(f"📁 Select from Field Library ({len(lib_boundaries)} pre-loaded research fields)")

boundary_mode = st.radio(
    "Field boundary source:",
    options=boundary_options,
    index=0,
    horizontal=True,
    key="boundary_source_mode",
)

target_vector_path: Optional[Path] = None
inferred_default_fid = "FIELD_1"
selected_lib_path: Optional[Path] = None
uploaded_polys = None
geom_sig = ""

if boundary_mode.startswith("📁") and lib_boundaries:
    selected_label = st.selectbox(
        "Choose field boundary polygon:",
        options=["-- Select a field boundary --"] + list(lib_boundaries.keys()),
        index=0,
        key="selected_library_field",
    )
    if selected_label and selected_label != "-- Select a field boundary --":
        selected_lib_path = lib_boundaries[selected_label]
        target_vector_path = selected_lib_path
        inferred_default_fid = (
            re.sub(r"^(boun[d]?|boundary|field)[_-]?", "", selected_lib_path.stem, flags=re.IGNORECASE).strip().upper()
            or "FIELD_1"
        )
        geom_sig = f"lib:{selected_lib_path.name}"
else:
    uploaded_polys = st.file_uploader(
        "Upload your field boundary polygon (.shp, .gpkg, .geojson, .kml, or .zip):",
        type=["gpkg", "geojson", "json", "kml", "shp", "shx", "dbf", "prj", "cpg", "zip"],
        accept_multiple_files=True,
        help="Upload a .zip containing the shapefile files, or individual .shp + .shx + .dbf + .prj files.",
    )
    if uploaded_polys:
        primary_up = next(
            (f for f in uploaded_polys if Path(f.name).suffix.lower() in (".shp", ".gpkg", ".geojson", ".json", ".kml", ".zip")),
            uploaded_polys[0],
        )
        inferred_default_fid = (
            re.sub(r"^(boun[d]?|boundary|field)[_-]?", "", Path(primary_up.name).stem, flags=re.IGNORECASE).strip().upper()
            or "FIELD_1"
        )
        # Compute signature purely based on file names, sizes, and content hash (independent of field name)
        sig_parts = []
        for uf in sorted(uploaded_polys, key=lambda x: x.name):
            raw_b = uf.getvalue()
            sig_parts.append(f"{uf.name}:{len(raw_b)}:{hashlib.md5(raw_b[:64]).hexdigest()}")
        geom_sig = f"upload:{hashlib.md5('|'.join(sig_parts).encode('utf-8')).hexdigest()}"

        upload_dir = get_session_workdir() / "uploaded_boundary"

        # If a new or changed set of files was uploaded, extract them into upload_dir
        if geom_sig != st.session_state.get("poly_sig"):
            if upload_dir.exists():
                shutil.rmtree(upload_dir, ignore_errors=True)
            upload_dir.mkdir(parents=True, exist_ok=True)

            for uf in uploaded_polys:
                raw_b = uf.getvalue()
                dest = upload_dir / Path(uf.name).name
                dest.write_bytes(raw_b)
                if dest.suffix.lower() == ".zip":
                    with zipfile.ZipFile(dest, "r") as zf:
                        zf.extractall(upload_dir)

        # Retain candidate discovery on every rerun so target_vector_path is never lost
        if upload_dir.exists():
            candidates = (
                list(upload_dir.rglob("*.gpkg"))
                + list(upload_dir.rglob("*.geojson"))
                + list(upload_dir.rglob("*.json"))
                + list(upload_dir.rglob("*.kml"))
                + list(upload_dir.rglob("*.shp"))
            )
            if candidates:
                target_vector_path = candidates[0]
            else:
                st.warning("Please include the `.shp`, `.gpkg`, `.geojson`, `.kml`, or `.zip` geometry file.")

# Determine if the geometric boundary source has changed
geom_changed = bool(geom_sig and geom_sig != st.session_state.get("poly_sig"))

# Sync or preserve the user's custom Field ID
if geom_changed or "field_id_custom" not in st.session_state:
    st.session_state["field_id_custom"] = inferred_default_fid
    st.session_state["field_id_input_widget"] = inferred_default_fid

with step1_fid_container:
    col_fid, col_fid_info = st.columns([1, 2])
    with col_fid:
        field_id_input = st.text_input(
            "Field ID / name:",
            key="field_id_input_widget",
            help="Identifier for this field boundary (used in filenames, reports, and directory paths).",
        ).strip().upper() or inferred_default_fid
    with col_fid_info:
        st.markdown(
            f"<div style='padding-top: 1.85rem; font-size: 0.88rem; color: #475569;'>"
            f"Active field identifier: <b><code>{field_id_input}</code></b> &bull; Used in filenames, reports, and directory paths."
            f"</div>",
            unsafe_allow_html=True,
        )

st.session_state["field_id_custom"] = field_id_input

if target_vector_path and geom_sig and geom_changed:
    with st.spinner("Reading field boundary polygon..."):
        try:
            res = run_worker({
                "action": "load_polygon",
                "input_path": str(target_vector_path),
                "field_id": field_id_input,
            })
            st.session_state["poly_info"] = res
            st.session_state["poly_sig"] = geom_sig
            st.session_state["pheno_res"] = None
            st.session_state["planet_search_res"] = None
            st.session_state["run_res"] = None
        except Exception as e:
            st.session_state["poly_info"] = None
            st.error(f"Could not read field boundary: {e}")
elif not geom_sig and st.session_state.get("poly_info") is not None:
    st.session_state["poly_info"] = None
    st.session_state["poly_sig"] = ""
    st.session_state["pheno_res"] = None
    st.session_state["planet_search_res"] = None
    st.session_state["run_res"] = None

# Render Field ID, area metrics, and polygon figure ABOVE the Step 1 inputs
pinfo = st.session_state.get("poly_info")
if pinfo is not None:
    # Seamlessly sync custom field_id into poly_info without resetting downstream steps
    if pinfo.get("field_id") != field_id_input:
        pinfo["field_id"] = field_id_input
        # Keep GeoJSON feature properties updated
        if pinfo.get("boundary_geojson_path") and Path(pinfo["boundary_geojson_path"]).exists():
            try:
                gj_path = Path(pinfo["boundary_geojson_path"])
                gj_data = json.loads(gj_path.read_text(encoding="utf-8"))
                for feat in gj_data.get("features", []):
                    feat.setdefault("properties", {})["field_id"] = field_id_input
                gj_path.write_text(json.dumps(gj_data), encoding="utf-8")
            except Exception:
                pass

    with step1_preview_container:
        st.markdown('<div class="md-output-title">📍 Loaded field boundary summary</div>', unsafe_allow_html=True)
        has_coord = "centroid_lat" in pinfo and "centroid_lon" in pinfo and pinfo["centroid_lat"] is not None
        if has_coord:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Field ID", pinfo["field_id"])
            m2.metric("Field area (ha)", f"{pinfo['area_ha']:.2f} ha")
            m3.metric("Field area (acres)", f"{pinfo['area_acres']:.2f} ac")
            lat_dir = "N" if pinfo["centroid_lat"] >= 0 else "S"
            lon_dir = "E" if pinfo["centroid_lon"] >= 0 else "W"
            m4.metric("Coordinates", f"{abs(pinfo['centroid_lat']):.4f}°{lat_dir}, {abs(pinfo['centroid_lon']):.4f}°{lon_dir}")
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric("Field ID", pinfo["field_id"])
            m2.metric("Field area (ha)", f"{pinfo['area_ha']:.2f} ha")
            m3.metric("Field area (acres)", f"{pinfo['area_acres']:.2f} ac")

        if Path(pinfo["preview_png_path"]).exists():
            render_crisp_figure(
                pinfo["preview_png_path"],
                use_svg_if_available=True,
                max_height_px=620,
                full_width=True,
            )

# ==============================================================================
# STEP 2: SELECT SOYBEAN SEASON & SENTINEL-2 NDVI SEASON PROGRESS
# ==============================================================================

st.markdown(
    """
    <div class="md-step-divider"></div>
    <div class="md-step-header">
        <span class="md-step-chip">Step 2</span>
        <span class="md-step-title">Select soybean season &amp; Sentinel-2 scouting date recommendations</span>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div style="background-color:#F8FAFC; border-left:4px solid #3B82F6; padding:0.8rem 1.1rem; border-radius:6px; margin-bottom:1rem; font-size:0.9rem;">
        <b>🛰️ About Soybean Season &amp; Sentinel-2 Phenology Analysis:</b><br/>
        Select the target crop season (year). This step connects to Google Earth Engine to query Sentinel-2 satellite data over your field to map continuous canopy growth and identify optimal disease scouting dates.<br/>
        <span style="color:#64748B; font-size:0.85rem;">
        • <b>Don't have Google Earth Engine?</b> Step 2 date discovery is optional! Choose your season year here, then proceed directly to <b>Step 3</b> to upload PlanetScope imagery.<br/>
        • <b>Using your own Earth Engine account?</b> Enter your Project ID and credentials in the left sidebar under <i>🔑 Earth Engine Access &amp; Credentials</i>.
        </span>
    </div>
    """,
    unsafe_allow_html=True,
)

season_options = [2026, 2025, 2024, 2023, 2022]

s_col1, s_col2 = st.columns([1, 2])
with s_col1:
    season_input = st.selectbox(
        "Soybean season (year):",
        options=season_options,
        index=1,  # Default to 2025
        key="season_select",
        help="Select the soybean growing season (year) for Sentinel-2 satellite analysis, scouting dates, and PlanetScope imagery.",
    )
with s_col2:
    st.markdown(
        f"<div style='padding-top: 1.8rem; font-size: 0.88rem; color: #475569;'>"
        f"Selected season: <b>{season_input}</b> &bull; Applies to phenology modeling, scouting dates, and imagery staging."
        f"</div>",
        unsafe_allow_html=True,
    )

if st.session_state.get("active_season") != season_input:
    if st.session_state.get("active_season") is not None:
        st.session_state["pheno_res"] = None
        st.session_state["planet_search_res"] = None
    st.session_state["active_season"] = season_input

with st.expander("❓ Beginner Guide: How to get a Google Earth Engine account & key (Click to read)", expanded=False):
    st.markdown(
        """
        ### 🚀 5-Minute Earth Engine Guide for Beginners

        #### 💡 Do you actually need this?
        If you already have your PlanetScope `.tif` imagery, **you can completely SKIP Step 2!** You do not need Google Earth Engine to detect or map Red Crown Rot. Proceed directly to **Step 3 (Stage Imagery)**.

        ---

        #### If you want to use Step 2 (Sentinel-2 Continuous Trajectory):

        ##### Step 1: Register for Free Earth Engine Access
        - Open [code.earthengine.google.com](https://code.earthengine.google.com) in your browser and log in with your Google account.
        - Choose **Unpaid / Academic / Research** use.
        - Follow the prompt to create a Google Cloud Project (e.g., named `ee-yourname`).

        ##### Step 2: Find your Project ID
        - In the Earth Engine code editor, look at the top-right corner.
        - Your project ID will look like `ee-yourname` or `rcr-scout-423101`.
        - Enter this into the **Google Earth Engine project ID** field in the left sidebar.

        ##### Step 3: Generate your free Service Account Key (`.json`)
        Because this web platform runs in the cloud, Google uses a private Service Account key file to authenticate your requests:
        - Open the [Google Cloud Service Accounts Console](https://console.cloud.google.com/iam-admin/serviceaccounts).
        - Confirm your Earth Engine project is selected in the top dropdown.
        - Click **+ CREATE SERVICE ACCOUNT** at the top.
        - Type any name (e.g. `rcr-web`) and click **Create and Continue**.
        - Under **Select a role**, choose **Earth Engine** → **Earth Engine User** (or *Resource Viewer*), then click **Done**.
        - Click on the newly created service account email.
        - Click the **KEYS** tab at the top → **ADD KEY** → **Create new key** → select **JSON** → click **Create**.
        - A `.json` file will automatically download to your computer!

        ##### Step 4: Upload the file to the platform
        - Open the left sidebar → expand **🔑 Earth Engine Access & Credentials**.
        - Drag & drop the downloaded `.json` file into the box.
        - That's it! Now click **🚀 Run Sentinel-2 analysis** below.
        """
    )

run_pheno_btn = st.button(
    f"🚀 Run Sentinel-2 NDVI season progress analysis for {season_input} (Google Earth Engine)",
    type="primary",
    disabled=(pinfo is None),
)

if pinfo is None:
    st.caption("ℹ️ Please upload your field boundary polygon in **Step 1** to enable Sentinel-2 extraction.")

if run_pheno_btn and pinfo is not None:
    with st.spinner(f"Extracting Sentinel-2 NDVI season progress trajectory for {pinfo['field_id']} ({season_input})..."):
        try:
            pheno_res = run_worker({
                "action": "run_phenology",
                "boundary_geojson_path": pinfo["boundary_geojson_path"],
                "field_id": pinfo["field_id"],
                "year": int(season_input),
                "target_drop_ratio": target_drop_pct / 100.0,
                "gee_project": gee_project,
                "custom_ee_credentials": st.session_state.get("custom_ee_credentials", ""),
                "use_cached": use_cached_pheno,
            })
            st.session_state["pheno_res"] = pheno_res
            st.session_state["planet_search_res"] = None
            st.success(f"NDVI season progress curve extracted! Source: {pheno_res['source']}")
        except Exception as e:
            err_str = str(e)
            if any(term in err_str.lower() for term in ["credentials not found", "not registered", "not signed up", "403", "connection error"]):
                st.warning(
                    "⚠️ **Google Earth Engine Setup Required for Step 2**\n\n"
                    "The platform was unable to authenticate with Google Earth Engine for this query.\n\n"
                    "**How would you like to proceed?**\n"
                    "1. **Skip Step 2 (Recommended if you have PlanetScope imagery)**: You do not need Earth Engine to map Red Crown Rot. Proceed directly to **Step 3 (Stage Imagery)** and **Step 4 (Classification)**.\n"
                    "2. **Use Your Earth Engine Account**: Open the left sidebar → **🔑 Earth Engine Access & Credentials** to enter your personal Project ID and upload your Service Account key or paste your credentials token.\n"
                    "3. **Sign Up for Free**: Register a free personal Google Earth Engine account at [code.earthengine.google.com](https://code.earthengine.google.com)."
                )
                with st.expander("Show detailed technical error"):
                    st.code(err_str, language="text")
            else:
                st.error(f"⚠️ **Sentinel-2 extraction error:**\n\n{e}")

if st.session_state["pheno_res"] is not None:
    pr = st.session_state["pheno_res"]

    # 1. Full-width high-resolution NDVI season progress plot ABOVE
    if Path(pr["pheno_png_path"]).exists():
        st.markdown('<div class="md-output-title">📈 Sentinel-2 canopy NDVI season progress trajectory</div>', unsafe_allow_html=True)
        render_crisp_figure(pr["pheno_png_path"], use_svg_if_available=True)
        png_bytes = Path(pr["pheno_png_path"]).read_bytes()
        st.download_button(
            "📥 Download NDVI season progress plot (.png)",
            data=png_bytes,
            file_name=Path(pr["pheno_png_path"]).name,
            mime="image/png",
        )

    # 2. Recommended PlanetScope dates BELOW in a 4-column horizontal grid
    st.markdown(
        '<div class="md-output-title">📅 Recommended PlanetScope dates (from continuous curve)</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Recommended dates are interpolated directly from the **continuous NDVI trajectory line**, "
        "with a **±5-day window** around the optimal target date:"
    )
    before_ndvi_str = f"Curve NDVI: <b>{pr.get('before_ndvi', 0.0):.3f}</b>" if pr.get("before_ndvi") else ""
    after_ndvi_str = f"Curve NDVI: <b>{pr.get('after_ndvi', 0.0):.3f}</b>" if pr.get("after_ndvi") else ""

    d_col1, d_col2, d_col3, d_col4 = st.columns(4, gap="medium")
    with d_col1:
        st.markdown(
            f"""
            <div class="md-date-card-blue" style="height:100%;">
                <div class="md-date-card-title">1. Early baseline mask (t<sub>peak</sub>)</div>
                <span class="md-date-pill-blue">{pr['peak_date']}</span>
                <div style="font-size:0.84rem; color:#4A5B75; margin-top:6px;">
                    Peak trajectory NDVI: <b>{pr['peak_ndvi']:.3f}</b>
                </div>
                <div style="font-size:0.78rem; color:#4A5B75; margin-top:4px;">
                    Used to build the soybean canopy mask (mean - {mask_sigma:g} SD) to exclude non-crop edges &amp; bare soil.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with d_col2:
        st.markdown(
            f"""
            <div class="md-date-card-blue" style="height:100%;">
                <div class="md-date-card-title">2. Pre-target point (-5 days)</div>
                <span class="md-date-pill-blue">{pr['before_date']}</span>
                <div style="font-size:0.84rem; color:#4A5B75; margin-top:6px;">
                    {before_ndvi_str}
                </div>
                <div style="font-size:0.78rem; color:#4A5B75; margin-top:4px;">
                    Suggested capture 5 days before the optimal target date.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with d_col3:
        st.markdown(
            f"""
            <div class="md-date-card-orange" style="height:100%;">
                <div class="md-date-card-title" style="color:#FF5F05;">
                    3. Optimal target ({target_drop_pct}% peak, t<sub>0</sub>)
                </div>
                <span class="md-date-pill-orange">{pr['target_date']}</span>
                <div style="font-size:0.84rem; color:#13294B; margin-top:6px;">
                    Curve NDVI: <b>{pr['actual_target_ndvi']:.3f}</b>
                </div>
                <div style="font-size:0.78rem; color:#13294B; margin-top:4px;">
                    Exact date where the continuous NDVI line crosses the {target_drop_pct}% threshold.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with d_col4:
        st.markdown(
            f"""
            <div class="md-date-card-blue" style="height:100%;">
                <div class="md-date-card-title">4. Post-target point (+5 days)</div>
                <span class="md-date-pill-blue">{pr['after_date']}</span>
                <div style="font-size:0.84rem; color:#4A5B75; margin-top:6px;">
                    {after_ndvi_str}
                </div>
                <div style="font-size:0.78rem; color:#4A5B75; margin-top:4px;">
                    Suggested capture 5 days after the optimal target date.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

# ==============================================================================
# STEP 3: PROVIDE PLANETSCOPE IMAGERY
# ==============================================================================

st.markdown(
    """
    <div class="md-step-divider"></div>
    <div class="md-step-header">
        <span class="md-step-chip">Step 3</span>
        <span class="md-step-title">Provide PlanetScope imagery</span>
    </div>
    """,
    unsafe_allow_html=True,
)

STEP3_TAB_OPTIONS = [
    "📂 Option A: Upload downloaded PlanetScope GeoTIFFs (.tif)",
    "🔑 Option B: Connect personal Planet API key (auto-search & clipped download)",
]

# Ensure active tab state is preserved across window defocus, tab switching, and reruns
url_params = st.query_params
if "step3_imagery_tab" not in st.session_state:
    if url_params.get("option", "").lower() in ("b", "api", "planet") or st.session_state.get("planet_search_res") is not None or st.session_state.get("last_order_id"):
        st.session_state["step3_imagery_tab"] = STEP3_TAB_OPTIONS[1]
    else:
        st.session_state["step3_imagery_tab"] = STEP3_TAB_OPTIONS[0]

tab_manual, tab_api = st.tabs(
    STEP3_TAB_OPTIONS,
    key="step3_imagery_tab",
    on_change="rerun",
)

# Synchronize clean URL query parameter for reload and focus resilience
if st.session_state.get("step3_imagery_tab") == STEP3_TAB_OPTIONS[1]:
    if st.query_params.get("option") != "b":
        st.query_params["option"] = "b"
elif "option" in st.query_params:
    del st.query_params["option"]


active_fid = pinfo["field_id"] if pinfo else field_id_input
selected_early_tif: Optional[Path] = None
selected_early_date: Optional[str] = None
available_classification_captures: List[Dict[str, str]] = []  # [{"date_str": "20250808", "date_iso": "2025-08-08", "path": "..."}]

# ------------------------------------------------------------------------------
# TAB A: UPLOAD DOWNLOADED PLANETSCOPE .TIF FILES ONLY
# ------------------------------------------------------------------------------
with tab_manual:
    st.markdown(
        "Upload the **8-band surface reflectance PlanetScope GeoTIFFs** downloaded from **Planet Explorer**."
    )

    u1, u2 = st.columns(2, gap="large")
    with u1:
        up_early = st.file_uploader(
            "1. Upload early baseline PlanetScope GeoTIFF (peak canopy mask):",
            type=["tif", "tiff"],
            key="up_early_tif",
        )
    with u2:
        up_targets = st.file_uploader(
            "2. Upload PlanetScope GeoTIFF(s) for classification dates:",
            type=["tif", "tiff"],
            accept_multiple_files=True,
            key="up_target_tifs",
        )

    if up_early and up_targets:
        save_dir = SAT_BASE_DIR / active_fid / str(season_input)
        save_dir.mkdir(parents=True, exist_ok=True)

        early_path = save_dir / Path(up_early.name).name
        early_path.write_bytes(up_early.getvalue())
        selected_early_date = parse_date_from_filename(up_early.name) or f"{season_input}0701"

        uploaded_t_paths = []
        sorted_targets = sorted(up_targets, key=lambda x: parse_date_from_filename(x.name) or x.name)
        for idx, uf in enumerate(sorted_targets):
            t_path = save_dir / Path(uf.name).name
            t_path.write_bytes(uf.getvalue())
            uploaded_t_paths.append(str(t_path))

        st.session_state["manual_uploaded_files"] = [str(early_path)] + uploaded_t_paths
        st.success(
            f"Uploaded 1 early baseline image (`{format_yyyymmdd_iso(selected_early_date)}`) and "
            f"{len(uploaded_t_paths)} classification image(s)."
        )

# ------------------------------------------------------------------------------
# TAB B: PERSONAL PLANET API KEY (AUTO-SEARCH & CLIP ORDER)
# ------------------------------------------------------------------------------
with tab_api:
    st.markdown(
        "Enter your personal **Planet API key** to search Planet's catalog for cloud-free "
        "8-band SuperDove scenes (`PSScene` / `ortho_analytic_8b_sr`) across the entire season "
        "(May 1 to October 31). You can choose any dates to download, with optimal scouting dates "
        "highlighted based on the Sentinel-2 NDVI trajectory curve."
    )
    api_c1, api_c2, api_c3 = st.columns([1.6, 1.0, 1.0])
    with api_c1:
        default_pl_key = st.session_state.get("planet_api_key", "")
        if not default_pl_key:
            default_pl_key = os.environ.get("PL_API_KEY") or os.environ.get("PLANET_API_KEY") or ""
            if not default_pl_key:
                try:
                    if hasattr(st, "secrets") and "PLANET_API_KEY" in st.secrets:
                        default_pl_key = str(st.secrets["PLANET_API_KEY"]).strip()
                except Exception:
                    pass
            if default_pl_key:
                st.session_state["planet_api_key"] = default_pl_key

        if "planet_api_key_input" not in st.session_state or (default_pl_key and not st.session_state.get("planet_api_key_input")):
            st.session_state["planet_api_key_input"] = default_pl_key

        planet_api_key_raw = st.text_input(
            "Personal Planet API key:",
            key="planet_api_key_input",
            type="password",
            placeholder="Paste your PL_API_KEY here...",
            help="Found in your Planet account settings -> API key. Never stored on disk.",
        )
        planet_api_key = str(planet_api_key_raw or "").strip().strip("'").strip('"').strip()
        if planet_api_key:
            st.session_state["planet_api_key"] = planet_api_key
    with api_c2:
        search_start_str = st.text_input(
            "Season start date (YYYY-MM-DD):",
            value=f"{season_input}-05-01",
            placeholder="YYYY-MM-DD",
            key=f"search_start_{season_input}",
            help="Start date for the Planet catalog search window in YYYY-MM-DD format (e.g. 2025-05-01).",
        ).strip()
    with api_c3:
        search_end_str = st.text_input(
            "Season end date (YYYY-MM-DD):",
            value=f"{season_input}-10-31",
            placeholder="YYYY-MM-DD",
            key=f"search_end_{season_input}",
            help="End date for the Planet catalog search window in YYYY-MM-DD format (e.g. 2025-10-31).",
        ).strip()

    date_regex = r"^\d{4}-\d{2}-\d{2}$"
    valid_dates = bool(re.match(date_regex, search_start_str) and re.match(date_regex, search_end_str))
    if not valid_dates:
        st.warning("⚠️ Invalid date format. Please format both dates as **YYYY-MM-DD** (e.g. 2025-05-01).")

    cfg_c1, cfg_c2 = st.columns([1.1, 1.1])
    with cfg_c1:
        max_cloud_pct = st.slider("Max cloud cover (%)", min_value=0, max_value=50, value=20, step=5, key="max_cloud_pct_slider")
    with cfg_c2:
        max_order_wait_mins = st.slider(
            "Max order clipping wait time (minutes):",
            min_value=15,
            max_value=45,
            value=30,
            step=5,
            key="max_order_wait_mins_slider",
            help="Planet clipping typically takes 10–25 minutes. If it exceeds this time, you can resume downloading using the Order ID tool below.",
        )

    search_btn = st.button(
        f"🔍 1. Search Planet catalog ({search_start_str} to {search_end_str})",
        disabled=(not planet_api_key or pinfo is None or not valid_dates),
    )
    if pinfo is None:
        st.caption("ℹ️ Please upload your field boundary polygon in **Step 1** to enable Planet catalog search.")
    elif st.session_state["pheno_res"] is None:
        st.caption("ℹ️ Tip: Run **Step 2 (Sentinel-2 NDVI season progress)** first to automatically highlight the optimal scouting captures in the search results.")

    if search_btn and planet_api_key and pinfo is not None and valid_dates:
        with st.spinner(f"Querying Planet Data API for cloud-free 8-band scenes from {search_start_str} to {search_end_str}..."):
            try:
                pr = st.session_state["pheno_res"]
                peak_d = pr["peak_date"] if pr else None
                target_d = pr["target_date"] if pr else None
                before_d = pr["before_date"] if pr else None
                after_d = pr["after_date"] if pr else None

                search_res = run_worker({
                    "action": "search_planet",
                    "api_key": planet_api_key,
                    "boundary_geojson_path": pinfo["boundary_geojson_path"],
                    "peak_date": peak_d,
                    "target_date": target_d,
                    "before_date": before_d,
                    "after_date": after_d,
                    "season": str(season_input),
                    "start_date": search_start_str,
                    "end_date": search_end_str,
                    "max_cloud_cover": max_cloud_pct / 100.0,
                })
                st.session_state["planet_search_res"] = search_res
                total_found = search_res.get("total_scenes_found", len(search_res.get("all_scenes", [])))
                st.success(f"Found {total_found} cloud-free PlanetScope 8-band pass(es) between {search_start_str} and {search_end_str}!")
            except Exception as e:
                st.error(f"Planet API search failed: {e}")

    if st.session_state["planet_search_res"] is not None and pinfo is not None:
        sr = st.session_state["planet_search_res"]
        all_scenes = sr.get("all_scenes", [])
        suggested_ids = sr.get("suggested_scene_ids", [])

        # Build table with an interactive Select column
        display_rows = []
        is_selected_initial = []
        for s in all_scenes:
            is_suggested = s["scene_id"] in suggested_ids
            is_selected_initial.append(is_suggested)
            display_rows.append({
                "Select": is_suggested,
                "Acquisition date": s["date_iso"],
                "Suggestion status": f"⭐ {s['suggestion']}" if s.get("suggestion") else "— Available pass",
                "Planet scene ID": s["scene_id"],
                "Cloud cover (%)": s["cloud_cover_pct"],
                "Instrument": s["instrument"],
            })

        if not any(is_selected_initial) and len(display_rows) > 0:
            display_rows[0]["Select"] = True

        catalog_df = pd.DataFrame(display_rows)

        st.markdown('<div class="md-output-title">🎯 Available PlanetScope 8-band scenes across season</div>', unsafe_allow_html=True)
        st.info(
            "💡 **Direct catalog selection:** Check or uncheck scenes directly in the **Select** column of the table below to choose which captures to download. "
            "Suggested captures based on your Sentinel-2 NDVI plot are pre-checked."
        )

        edited_catalog = st.data_editor(
            catalog_df,
            column_config={
                "Select": st.column_config.CheckboxColumn(
                    "Select",
                    help="Check to include this capture in your Planet clip order.",
                    default=False,
                ),
                "Acquisition date": st.column_config.TextColumn("Acquisition date"),
                "Suggestion status": st.column_config.TextColumn("Suggestion status"),
                "Planet scene ID": st.column_config.TextColumn("Planet scene ID"),
                "Cloud cover (%)": st.column_config.NumberColumn("Cloud cover (%)", format="%.1f%%"),
                "Instrument": st.column_config.TextColumn("Instrument"),
            },
            disabled=["Acquisition date", "Suggestion status", "Planet scene ID", "Cloud cover (%)", "Instrument"],
            hide_index=True,
            use_container_width=True,
            key="planet_catalog_table_selector",
        )

        selected_rows = edited_catalog[edited_catalog["Select"] == True]
        chosen_scene_ids = selected_rows["Planet scene ID"].tolist()
        chosen_dates = selected_rows["Acquisition date"].tolist()

        if chosen_scene_ids:
            dates_summary = ", ".join([f"`{d}`" for d in chosen_dates])
            st.markdown(f"**Selected capture(s) to order ({len(chosen_scene_ids)}):** {dates_summary}")
        else:
            st.warning("⚠️ No captures currently selected. Please check at least one scene in the table above.")

        def _render_planet_error(exc: Exception, order_id: Optional[str] = None):
            err_str = str(exc)
            oid_txt = f" for Order ID `{order_id}`" if order_id else ""
            if "401" in err_str or "unauthorized" in err_str.lower() or "authentication failed" in err_str.lower():
                st.error(
                    f"🔑 **Planet API authentication failed (401 Unauthorized)**{oid_txt}.\n\n"
                    f"**Please verify:**\n"
                    f"1. Your Planet API key in the **Personal Planet API key** field above is correct.\n"
                    f"2. Your Planet user account has active permissions to access this order.\n"
                    f"3. You can verify your key anytime in your [Planet account settings](https://www.planet.com/account/)."
                )
            elif "404" in err_str or "not found" in err_str.lower():
                st.error(
                    f"🔍 **Planet Order not found (404)**{oid_txt}. Please verify that the 36-character Order UUID was entered correctly."
                )
            else:
                st.error(f"Planet request error: {err_str}")

        if st.button(
            f"⬇️ 2. Order & download {len(chosen_scene_ids)} selected scene(s) via Planet API",
            type="primary",
            disabled=(len(chosen_scene_ids) == 0),
        ):
            with st.spinner(f"Submitting polygon-clipped order ({len(chosen_scene_ids)} scene(s)) to Planet Orders API v2 and polling progress..."):
                try:
                    order_res = run_worker({
                        "action": "order_planet",
                        "api_key": planet_api_key,
                        "boundary_geojson_path": pinfo["boundary_geojson_path"],
                        "scene_ids": chosen_scene_ids,
                        "field_id": active_fid,
                        "season": str(season_input),
                        "max_wait_minutes": max_order_wait_mins,
                    })
                    oid = order_res.get("order_id")
                    if oid:
                        st.session_state["last_order_id"] = oid

                    if order_res.get("status") == "still_processing":
                        st.info(
                            f"⏳ **Order submitted successfully (Order ID: `{oid}`)!**\n\n"
                            f"Planet is actively clipping your scenes in the background (State: `{order_res.get('state', 'RUNNING').upper()}`).\n\n"
                            f"Clipping typically takes **10–25 minutes**. Your Order ID has been recorded in the **'Resume or download existing Planet order'** card below. "
                            f"You can check its progress anytime using **'Check status'** or download the GeoTIFFs once complete."
                        )
                    else:
                        dl_files = order_res.get("downloaded_files", [])
                        st.session_state["api_downloaded_files"] = dl_files
                        st.success(f"✅ Downloaded {len(dl_files)} clipped PlanetScope GeoTIFF(s) and staged them for Step 4!")
                except Exception as e:
                    _render_planet_error(e)
                    match_oid = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", str(e))
                    if match_oid:
                        st.session_state["last_order_id"] = match_oid.group(1)

    # Resume or download existing Planet Order by Order ID (Permanent Card)
    st.divider()
    st.markdown('<div class="md-output-title">📥 Resume or download existing Planet order by Order ID</div>', unsafe_allow_html=True)
    st.markdown(
        "If an order was previously submitted or timed out while Planet was clipping scenes in the background, "
        "enter the **Planet Order ID** below to check its status or download the completed GeoTIFFs without spending additional quota:"
    )
    res_c1, res_c2, res_c3 = st.columns([2.0, 1.0, 1.2])
    with res_c1:
        saved_oid_file = SAT_BASE_DIR / active_fid / str(season_input) / "last_planet_order_id.txt"
        saved_oid = ""
        if saved_oid_file.exists():
            saved_oid = saved_oid_file.read_text(encoding="utf-8").strip()
        elif inferred_default_fid and (SAT_BASE_DIR / inferred_default_fid / str(season_input) / "last_planet_order_id.txt").exists():
            saved_oid = (SAT_BASE_DIR / inferred_default_fid / str(season_input) / "last_planet_order_id.txt").read_text(encoding="utf-8").strip()
        default_oid = st.session_state.get("last_order_id", saved_oid)
        if "planet_order_id_input" not in st.session_state or (default_oid and not st.session_state.get("planet_order_id_input")):
            st.session_state["planet_order_id_input"] = default_oid
        input_order_id = st.text_input(
            "Planet Order ID:",
            key="planet_order_id_input",
            placeholder="e.g. 191282b5-a60b-49b3-ac9d-d46fd130854e",
            help="Paste the 36-character UUID from your Planet Orders confirmation or error message.",
        ).strip().strip("'").strip('"').strip()
        if input_order_id:
            st.session_state["last_order_id"] = input_order_id
    with res_c2:
        st.write("")
        st.write("")
        check_status_btn = st.button(
            "🔎 Check status",
            help="Instantly checks whether Planet has finished clipping without waiting.",
        )
    with res_c3:
        st.write("")
        st.write("")
        resume_order_btn = st.button(
            "📥 Download order by ID",
            type="primary",
            help="Downloads clipped GeoTIFFs if ready, or polls until clipping completes.",
        )

    if check_status_btn:
        if not planet_api_key:
            st.warning("⚠️ Please enter your **Personal Planet API key** above before checking order status.")
        elif not input_order_id:
            st.warning("⚠️ Please provide a valid **Planet Order ID**.")
        else:
            with st.spinner(f"Checking status of Order ID '{input_order_id}' on Planet..."):
                try:
                    chk_res = run_worker({
                        "action": "check_planet_order",
                        "api_key": planet_api_key,
                        "order_id": input_order_id,
                    })
                    info = chk_res.get("order_info", {})
                    st.session_state["last_order_id"] = input_order_id
                    state = info.get("state", "unknown").upper()
                    if info.get("is_processing"):
                        st.info(
                            f"⏳ **Planet order is currently PROCESSING (State: `{state}`)**\n\n"
                            f"- **Order ID:** `{input_order_id}`\n"
                            f"- **Current State:** `{state}`\n"
                            f"- **Order Created:** `{info.get('created_at', 'N/A')}`\n\n"
                            f"Planet is actively clipping your scenes in the background. Clipping typically takes **10–25 minutes**. "
                            f"You do not need to re-order. Click **'Download order by ID'** once ready to retrieve your GeoTIFFs."
                        )
                    elif info.get("is_ready"):
                        st.success(
                            f"🎉 **Planet order is READY! (State: `{state}`)**\n\n"
                            f"- **Order ID:** `{input_order_id}`\n"
                            f"- **Available clipped GeoTIFF(s):** {info.get('raster_count', 0)}\n\n"
                            f"Click **'Download order by ID'** to download the rasters to your workspace and stage them for Step 4."
                        )
                    elif info.get("is_failed"):
                        st.error(f"❌ **Planet order ended with state `{state}`:** {info.get('last_message', 'No error details returned.')}")
                    else:
                        st.write(f"Order state: `{state}`")
                except Exception as e:
                    _render_planet_error(e, input_order_id)

    if resume_order_btn:
        if not planet_api_key:
            st.warning("⚠️ Please enter your **Personal Planet API key** above before downloading.")
        elif not input_order_id:
            st.warning("⚠️ Please provide a valid **Planet Order ID**.")
        else:
            with st.spinner(f"Connecting to Planet Orders API v2 to check and download Order ID '{input_order_id}' (up to {max_order_wait_mins} min)..."):
                try:
                    resume_res = run_worker({
                        "action": "resume_planet_order",
                        "api_key": planet_api_key,
                        "order_id": input_order_id,
                        "field_id": active_fid,
                        "season": str(season_input),
                        "boundary_geojson_path": pinfo["boundary_geojson_path"] if pinfo else None,
                        "max_wait_minutes": max_order_wait_mins,
                    })
                    st.session_state["last_order_id"] = input_order_id
                    if resume_res.get("status") == "still_processing":
                        st.info(
                            f"⏳ **Planet order is still processing (State: `{resume_res.get('state', 'RUNNING').upper()}`)**\n\n"
                            f"{resume_res.get('message', '')}"
                        )
                    else:
                        dl_files = resume_res.get("downloaded_files", [])
                        st.session_state["api_downloaded_files"] = dl_files
                        st.success(f"✅ Successfully downloaded {len(dl_files)} clipped PlanetScope GeoTIFF(s) from Order ID '{input_order_id}' and staged them for Step 4!")
                        if resume_res.get("spatial_warning"):
                            st.warning(f"⚠️ **Spatial Mismatch Warning:**\n\n{resume_res['spatial_warning']}")
                        if pinfo is None:
                            st.info("💡 **Next step:** Please upload your field boundary polygon in **Step 1** to proceed with RCR classification in **Step 4**.")
                except Exception as e:
                    _render_planet_error(e, input_order_id)

# ------------------------------------------------------------------------------
# DISCOVER ALL AVAILABLE PLANETSCOPE RASTERS (DOWNLOADED, UPLOADED, OR LOCAL)
# ------------------------------------------------------------------------------
def _format_raster_display(p: Path) -> str:
    d_str = parse_date_from_filename(p.name)
    iso = format_yyyymmdd_iso(d_str) if d_str else "Unknown date"
    return f"📅 {iso} · {p.name}"

discovered_rasters_map: Dict[str, Path] = {}

# 1. Any rasters from API downloads or manual uploads in session
for p_str in st.session_state.get("api_downloaded_files", []) + st.session_state.get("manual_uploaded_files", []):
    p = Path(p_str)
    if p.exists() and p.name not in discovered_rasters_map:
        discovered_rasters_map[p.name] = p

# 2. Any rasters in routine_inspection/raw_satellite/
candidate_dirs = [
    SAT_BASE_DIR / active_fid / str(season_input),
    SAT_BASE_DIR / active_fid,
]
if inferred_default_fid and inferred_default_fid != active_fid:
    candidate_dirs.extend([
        SAT_BASE_DIR / inferred_default_fid / str(season_input),
        SAT_BASE_DIR / inferred_default_fid,
    ])
if active_fid in ("FIELD_1", "PC3") or inferred_default_fid in ("FIELD_1", "PC3"):
    candidate_dirs.extend([
        SAT_BASE_DIR / "FIELD_1" / str(season_input),
        SAT_BASE_DIR / "FIELD_1",
        SAT_BASE_DIR / "FIELD_1" / "2025",
        SAT_BASE_DIR / "FIELD_1" / "2024",
        SAT_BASE_DIR / "PC3" / str(season_input),
        SAT_BASE_DIR / "PC3",
    ])
for cdir in candidate_dirs:
    if cdir.exists():
        for f in cdir.rglob("*.tif*"):
            if f.is_file() and "udm" not in f.name.lower() and f.name not in discovered_rasters_map:
                if "sr" in f.name.lower() or "analytic" in f.name.lower() or "clip" in f.name.lower():
                    discovered_rasters_map[f.name] = f

# Sort rasters chronologically
all_available_rasters = sorted(
    list(discovered_rasters_map.values()),
    key=lambda p: (parse_date_from_filename(p.name) or "99999999", p.name),
)

# Visual summary of staged imagery in Step 3
if all_available_rasters:
    st.markdown('<div class="md-output-title">📋 Staged PlanetScope imagery for analysis</div>', unsafe_allow_html=True)
    summary_chips = [f"`{format_yyyymmdd_iso(parse_date_from_filename(p.name) or '')}`" for p in all_available_rasters]
    col_staged_info, col_clear = st.columns([3.5, 1.2])
    with col_staged_info:
        st.info(f"🛰️ **{len(all_available_rasters)} PlanetScope 8-band GeoTIFF raster(s) ready:** {', '.join(summary_chips)}")
    with col_clear:
        if st.button("🗑️ Clear staged imagery", help="Removes staged rasters for this field so you can download or upload fresh imagery."):
            st.session_state["api_downloaded_files"] = []
            st.session_state["manual_uploaded_files"] = []
            target_fdir = SAT_BASE_DIR / active_fid / str(season_input)
            if target_fdir.exists():
                shutil.rmtree(target_fdir, ignore_errors=True)
            if inferred_default_fid and inferred_default_fid != active_fid:
                alt_fdir = SAT_BASE_DIR / inferred_default_fid / str(season_input)
                if alt_fdir.exists() and inferred_default_fid not in ("FIELD_1", "PC3"):
                    shutil.rmtree(alt_fdir, ignore_errors=True)
            st.rerun()


# ==============================================================================
# STEP 4: CHOOSE CLASSIFICATION DATES & RUN RCR CLASSIFICATION
# ==============================================================================

st.markdown(
    """
    <div class="md-step-divider"></div>
    <div class="md-step-header">
        <span class="md-step-chip">Step 4</span>
        <span class="md-step-title">Select capture dates &amp; run red crown rot classification</span>
    </div>
    """,
    unsafe_allow_html=True,
)

selected_peak_path: Optional[Path] = None
selected_target_paths: List[Path] = []

if not all_available_rasters:
    st.info("ℹ️ No PlanetScope GeoTIFF rasters available yet. Please upload files in **Option A** or download them via **Option B** in **Step 3** above.")
else:
    st.markdown('<div class="md-output-title">🎯 Specify PlanetScope imagery roles for analysis</div>', unsafe_allow_html=True)

    # 1. Determine optimal default peak index (matching Sentinel-2 phenology peak if available)
    default_peak_idx = 0
    pr = st.session_state.get("pheno_res")
    if pr and pr.get("peak_date"):
        try:
            peak_dt = datetime.strptime(pr["peak_date"], "%Y-%m-%d")
            diffs = []
            for p in all_available_rasters:
                ds = parse_date_from_filename(p.name)
                if ds:
                    p_dt = datetime.strptime(ds, "%Y%m%d")
                    diffs.append(abs((p_dt - peak_dt).days))
                else:
                    diffs.append(9999)
            default_peak_idx = int(np.argmin(diffs))
        except Exception:
            default_peak_idx = 0

    col_peak, col_targets = st.columns([1.3, 1.7], gap="medium")
    with col_peak:
        selected_peak_path = st.selectbox(
            "1. Peak canopy mask image (early baseline):",
            options=all_available_rasters,
            index=default_peak_idx,
            format_func=_format_raster_display,
            help="Select the PlanetScope capture representing peak vegetative growth (typically mid-to-late July) to compute the field's early canopy mask (mean - k * SD).",
            key="peak_canopy_select",
        )

    with col_targets:
        remaining_rasters = [p for p in all_available_rasters if p != selected_peak_path]
        selected_target_paths = st.multiselect(
            "2. Classification capture date(s) to evaluate:",
            options=remaining_rasters,
            default=remaining_rasters,
            format_func=_format_raster_display,
            help="Select one or multiple capture dates to run through the 13-feature RCR machine learning classifier.",
            key="classification_targets_select",
        )

c_poly_ok = pinfo is not None
c_peak_ok = selected_peak_path is not None and Path(selected_peak_path).exists()
c_target_ok = len(selected_target_paths) > 0
ready_to_classify = c_poly_ok and c_peak_ok and c_target_ok

if ready_to_classify:
    peak_dstr = parse_date_from_filename(selected_peak_path.name) or ""
    target_summary = ", ".join([f"`{format_yyyymmdd_iso(parse_date_from_filename(p.name) or '')}`" for p in selected_target_paths])
    st.success(
        f"✅ **Ready for classification:** Field `{active_fid}` ({season_input}) &nbsp;|&nbsp; "
        f"**Peak canopy mask:** `{format_yyyymmdd_iso(peak_dstr)}` (`mean - {mask_sigma:g} SD`) &nbsp;|&nbsp; "
        f"**Classification date(s):** {target_summary}"
    )
else:
    st.markdown('<div class="md-output-title">📋 Classification requirements checklist</div>', unsafe_allow_html=True)
    chk1, chk2, chk3 = st.columns(3)
    with chk1:
        if c_poly_ok:
            st.success(f"✔️ **Field boundary:** Loaded (`{active_fid}`)")
        else:
            st.warning("⚠️ **Field boundary:** Not loaded.<br>Please upload in **Step 1**.")
    with chk2:
        if c_peak_ok:
            peak_dstr = parse_date_from_filename(selected_peak_path.name) or ""
            st.success(f"✔️ **Peak mask image:** `{format_yyyymmdd_iso(peak_dstr)}`")
        else:
            st.warning("⚠️ **Peak mask image:** Not specified.<br>Please select above.")
    with chk3:
        if c_target_ok:
            st.success(f"✔️ **Classification dates:** {len(selected_target_paths)} selected")
        else:
            st.warning("⚠️ **Classification dates:** None selected.<br>Please select above.")

run_model_btn = st.button(
    "🔬 Run RCR classification on selected date(s)",
    type="primary",
    disabled=(not ready_to_classify),
)

if run_model_btn and ready_to_classify and pinfo is not None and selected_peak_path is not None:
    with st.spinner(
        f"Running early canopy masking (mean - {mask_sigma:g} SD), 13-feature normalization, and generating RCR maps..."
    ):
        try:
            pheno_csv = st.session_state["pheno_res"]["csv_path"] if st.session_state["pheno_res"] else None
            early_d_str = parse_date_from_filename(selected_peak_path.name) or f"{season_input}0701"

            target_captures_payload = []
            for tp in selected_target_paths:
                t_dstr = parse_date_from_filename(tp.name) or "target"
                t_iso = format_yyyymmdd_iso(t_dstr)
                role_lbl = f"Target ({t_iso})"
                target_captures_payload.append([role_lbl, t_dstr, str(tp)])

            run_res = run_worker({
                "action": "run_classification",
                "field_id": active_fid,
                "season": str(season_input),
                "boundary_geojson_path": pinfo["boundary_geojson_path"],
                "early_tif_path": str(selected_peak_path),
                "early_date_str": early_d_str,
                "target_captures": target_captures_payload,
                "threshold": prob_threshold,
                "mask_sigma": mask_sigma,
                "pheno_csv_path": pheno_csv,
            })
            st.session_state["run_res"] = run_res
            st.success("RCR probability & classification maps generated successfully!")
        except Exception as e:
            err_msg = str(e)
            if "Spatial overlap error" in err_msg or "does not overlap" in err_msg.lower():
                # Extract first clean explanation line
                clean_lines = [l.strip() for l in err_msg.splitlines() if l.strip() and not l.strip().startswith("Traceback") and not l.strip().startswith("File ") and not l.strip().startswith("raise ")]
                headline = clean_lines[0] if clean_lines else "Field polygon does not overlap raster."
                st.error(
                    f"❌ **Spatial Mismatch Error:** {headline}\n\n"
                    f"**Why this happened:**\n"
                    f"- The PlanetScope GeoTIFF raster you selected was clipped to a **different field location** than your currently loaded field boundary (`{active_fid}`).\n"
                    f"- In Planet's Orders API, Order IDs are permanently bound to the specific field geometry that was provided when placing the order.\n\n"
                    f"**How to correct this:**\n"
                    f"1. If you previously entered a Planet Order ID from another field, please click **'🗑️ Clear staged imagery'** in Step 3.\n"
                    f"2. Under **Step 3 (Option B)**, enter your Planet API key and click **'🔍 1. Search Planet catalog'**.\n"
                    f"3. Select the dates you want, then click **'⬇️ 2. Order & download selected scene(s)'** to place a new clip order customized for field `{active_fid}`."
                )
            else:
                st.error(f"Classification error: {e}")

if st.session_state["run_res"] is not None:
    rr = st.session_state["run_res"]
    d_results = rr["date_results"]

    st.markdown('<div class="md-step-divider"></div>', unsafe_allow_html=True)
    # 1. KPI Summary Cards across the chosen capture dates
    st.markdown(
        '<div class="md-output-title">📊 RCR affected area summary by capture date</div>',
        unsafe_allow_html=True,
    )
    kpi_cols = st.columns(len(d_results) + 1)
    kpi_cols[0].metric(
        "Total analyzed canopy",
        f"{d_results[0]['total_canopy_ha']:.2f} ha",
        f"{d_results[0]['total_canopy_acres']:.2f} acres",
    )
    for idx, item in enumerate(d_results):
        delta_str = None
        aff_pct = item.get("rcr_affected_area_pct", item.get("rcr_incidence_pct", 0.0))
        if idx > 0:
            prev_aff = d_results[idx - 1].get("rcr_affected_area_pct", d_results[idx - 1].get("rcr_incidence_pct", 0.0))
            diff_pct = aff_pct - prev_aff
            delta_str = f"{diff_pct:+.2f}% vs previous"
        kpi_cols[idx + 1].metric(
            f"Capture date: {item['capture_date_iso']}",
            f"{aff_pct:.2f}% affected",
            delta_str
            if delta_str
            else f"{item['rcr_affected_ha']:.2f} ha ({item['rcr_affected_acres']:.2f} ac) affected",
            delta_color="inverse" if delta_str else "off",
        )

    # 2. RCR Probability & Classification Maps
    st.markdown(
        '<div class="md-output-title">🗺️ RCR probability &amp; classification maps</div>',
        unsafe_allow_html=True,
    )
    if Path(rr["grid_png_path"]).exists():
        n_dates = len(d_results)
        grid_max_w = 640 if n_dates == 1 else (1040 if n_dates == 2 else None)
        render_crisp_figure(rr["grid_png_path"], use_svg_if_available=True, max_width_px=grid_max_w)

    # 3. Quantitative Summary Table & Downloads
    st.markdown(
        '<div class="md-output-title">📋 Quantitative field scouting report &amp; downloads</div>',
        unsafe_allow_html=True,
    )
    summary_df = pd.DataFrame(d_results)
    display_cols = [
        "capture_date_iso",
        "early_mask_date",
        "mask_sigma_used",
        "total_canopy_ha",
        "total_canopy_acres",
        "rcr_affected_ha",
        "rcr_affected_acres",
        "rcr_affected_area_pct",
        "threshold_used",
        "probability_raster",
        "classified_raster",
    ]
    avail_display_cols = [c for c in display_cols if c in summary_df.columns]
    st.dataframe(summary_df[avail_display_cols], use_container_width=True, hide_index=True)

    dl_col1, dl_col2, dl_col3 = st.columns(3)
    with dl_col1:
        if Path(rr["zip_path"]).exists():
            with open(rr["zip_path"], "rb") as fz:
                st.download_button(
                    label="📦 Download all GeoTIFF rasters & reports (.zip)",
                    data=fz.read(),
                    file_name=Path(rr["zip_path"]).name,
                    mime="application/zip",
                    type="primary",
                )
    with dl_col2:
        if Path(rr["grid_png_path"]).exists():
            with open(rr["grid_png_path"], "rb") as fg:
                st.download_button(
                    label="🖼️ Download RCR maps figure (.png)",
                    data=fg.read(),
                    file_name=Path(rr["grid_png_path"]).name,
                    mime="image/png",
                )
    with dl_col3:
        if Path(rr["summary_csv_path"]).exists():
            with open(rr["summary_csv_path"], "rb") as fc:
                st.download_button(
                    label="📄 Download summary table (.csv)",
                    data=fc.read(),
                    file_name=Path(rr["summary_csv_path"]).name,
                    mime="text/csv",
                )

# Institutional Footer
st.markdown(
    """
    <div class="md-footer">
        <div>
            <b style="color:#13294B;">Red crown rot scouting</b> &mdash;
            Smart Plant Pathology Lab, Department of Crop Sciences, University of Illinois Urbana-Champaign
        </div>
        <div>
            <b>Contacts:</b>
            Dr. Boris X. Camiletti (<a href="mailto:bxc@illinois.edu" style="color:#1D58A7;">bxc@illinois.edu</a>) &bull;
            Dr. Bruno D. Pugliese (<a href="mailto:brunodp@illinois.edu" style="color:#1D58A7;">brunodp@illinois.edu</a>)
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
