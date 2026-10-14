# Soybean Red Crown Rot (RCR) Scouting Platform
### User Guide for Field Agronomists & Researchers (Zero Code Required)

Welcome to the **Soybean Red Crown Rot (RCR) Automated Field Scouting Platform**, developed by the **Smart Plant Pathology Lab** at the **University of Illinois Urbana-Champaign**.

This application enables you to detect and map Red Crown Rot in commercial soybean fields using satellite imagery and machine learning, with **zero programming experience required**.

---

## 1. First-Time Setup (Only Needed Once)

If your Windows computer has never had Python or Conda installed, set it up in two easy steps:

### Option A: Fast Setup (Recommended)
1. Open PowerShell or Command Prompt on Windows, paste this line, and press **Enter**:
   ```cmd
   winget install conda-forge.miniforge
   ```
   *(This downloads and installs Miniforge automatically with zero configuration).*
2. Double-click **`setup_environment.bat`** in this folder.
   *(This automatically builds the complete Python environment with all required geospatial and ML libraries).*
3. You're all set! Proceed to Section 2 below.

### Option B: Web Installer Setup (3 Minutes)
1. **Download Miniforge** for Windows:  
   👉 [Download Miniforge Windows Installer (.exe)](https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe)
2. Run the downloaded installer and click **Next** &rarr; **Install** (keep standard default settings).
3. Double-click **`setup_environment.bat`** in this folder.

---

## 2. How to Launch the Platform

1. Open the `rcr_scouting_app` folder.
2. **Double-click `launch_platform.bat`**.
3. A command window will initialize the platform, and your default web browser (Chrome, Edge, etc.) will automatically open:
   ```
   http://localhost:8501
   ```
4. *Keep the command window open in the background while using the platform. When finished, simply close the browser and close the command window.*

---

## 3. Step-by-Step Workflow

The platform guides you through 4 simple steps:

```
[ Step 1: Upload Field ]  -->  [ Step 2: NDVI Progress Analysis ]  -->  [ Step 3: Imagery Staging ]  -->  [ Step 4: RCR Detection Map ]
```

### Step 1: Field Boundary & Season
1. In **Step 1**, drag and drop your soybean field boundary file.
   - Supported formats:
     - Shapefile (`.zip` containing `.shp`, `.shx`, `.dbf`, `.prj`, or upload the individual files together)
     - GeoPackage (`.gpkg`)
     - GeoJSON (`.geojson` / `.json`)
     - Google Earth / GPS KML (`.kml`)
   - *A test file (`sample_field.geojson`) is available in the `sample_data/` folder if you want to try it out first.*
2. Enter your **Field ID** (e.g., `NORTH_FIELD_40`) and select the **Soybean Season (Year)**.
3. The platform will immediately render a map preview of your field with its total area in **hectares** and **acres**.

---

### Step 2: Sentinel-2 NDVI Progress Analysis & Scouting Dates
To detect Red Crown Rot accurately, the platform needs to know when your crop reached **Peak Canopy** (to mask out waterways, bare soil, and field edges) and when it reached the **Senescence Drop Window** (optimal disease expression).

1. Click **"🚀 Run Sentinel-2 NDVI season progress analysis"**.
2. The platform connects to Google Earth Engine and plots the continuous vegetation curve for your field.
3. It automatically pinpoints 4 optimal dates:
   - **$t_{\text{peak}}$ (Peak Canopy)**: Baseline date for early non-crop masking.
   - **$t_0$ (Target Scouting Date)**: When canopy NDVI drops to 75% of peak (optimal RCR expression).
   - **$t_{-1}$ (Before Capture)**: Recommended capture ~1 week before the target date.
   - **$t_{+1}$ (After Capture)**: Recommended capture ~1 week after the target date.

> **Note on Google Earth Engine:**
> In the left sidebar, there is a field for **Google Earth Engine project ID**. If your organization has a Google Cloud / Earth Engine project, you can enter your project ID there.

---

### Step 3: Satellite Imagery Acquisition
You need 8-band PlanetScope SuperDove Surface Reflectance imagery for the dates identified in Step 2. Choose the option that fits your setup:

- **Option A (Manual Upload / Local Folder)**:
  - If you download your images directly from [Planet Explorer](https://www.planet.com/explorer/):
  - Download the 8-band Surface Reflectance `.tif` files matching the dates.
  - Drag and drop them directly into the Step 3 upload area in your browser, or place them into the `workspace/satellite_imagery/` folder.
- **Option B (Personal Planet API Key)**:
  - If you have an active Planet API key, paste it into the password box.
  - The platform will search the Planet catalog, display cloud-free candidate scenes, and submit a polygon-clipped order automatically.

---

### Step 4: Run RCR Detection & Export Results
1. Confirm the 4 selected dates ($t_{\text{peak}}, t_{-1}, t_0, t_{+1}$).
2. Click **"⚡ Run Multi-Date RCR Classification"**.
3. The platform executes the detection model:
   - Early $3\sigma$ canopy mask.
   - Within-field normalization of 13 spectral indices and bands.
   - Calibrated RCR probability prediction.
4. **Interactive Results**:
   - **Side-by-Side Visual Comparison Grid**: Normalized NDVI, continuous RCR Probability (peach color ramp), and Binary Detection maps for each date.
   - **Quantification Metrics**: Field-level affected area in hectares, acres, and disease prevalence (%).
5. **Download Exports**:
   - **Summary Table (`.csv`)**: Full tabular breakdown of affected acreage per date.
   - **GIS Bundle (`.zip`)**: GeoTIFF rasters ready for QGIS, ArcGIS, or Climate FieldView, along with publication-quality high-resolution figures.

---

## 3. Recommended Settings (Sidebar)

- **Senescence Cutoff**: Default **75%** of peak NDVI. This represents the optimal window when RCR foliar symptoms (interveinal chlorosis and necrosis) are most distinct from natural maturity.
- **Canopy Mask Threshold**: Default **3.0 SD below mean**. Effectively removes grass waterways, bare soil, and headlands without clipping diseased soybean pixels.
- **Classification Threshold**: Default **0.48**. Calibrated under spatial Leave-One-Field-Out cross-validation for maximum balanced accuracy ($F_1 = 0.948$).

---

## 4. Contact & Support

**Smart Plant Pathology Lab**  
Department of Crop Sciences  
University of Illinois Urbana-Champaign  

- **Dr. Boris X. Camiletti**: [bxc@illinois.edu](mailto:bxc@illinois.edu)
- **Dr. Bruno D. Pugliese**: [brunodp@illinois.edu](mailto:brunodp@illinois.edu)
