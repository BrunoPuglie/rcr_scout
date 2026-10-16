# Soybean Red Crown Rot (RCR) Scouting Platform
### User Guide for Field Agronomists & Researchers (Zero Code Required)

Welcome to the **Soybean Red Crown Rot (RCR) Automated Field Scouting Platform**, developed by the **Smart Plant Pathology Lab** at the **University of Illinois Urbana-Champaign**.

This application enables field agronomists, crop consultants, researchers, and growers to detect and map Red Crown Rot in commercial soybean fields using satellite imagery and machine learning, with **zero programming experience required**.

---

## 1. Quick Start for First-Time Users

Setting up the platform on a Windows computer takes only a few minutes and is performed once.

### Step 1.1: Install Miniforge (Free Conda Manager)
Choose any of the following options:

- **Option A (Fully Automated - Recommended):**
  Simply double-click **`setup_environment.bat`** in this folder! It will detect that Miniforge is missing and offer to install it automatically for you using Windows Package Manager.
- **Option B (Fastest via Terminal / PowerShell):**
  Open PowerShell or Command Prompt, paste this command, and press **Enter**:
  ```powershell
  winget install --id CondaForge.Miniforge3 -e --accept-source-agreements --accept-package-agreements
  ```
- **Option C (Visual Installer):**
  Download the installer from:
  👉 [Download Miniforge Windows Installer (.exe)](https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe)  
  Run the installer, click **Next** &rarr; **Install** with the standard default settings.

### Step 1.2: One-Click Environment Setup
Double-click **`setup_environment.bat`** in this folder.  
*(This automatically downloads and configures the required Python environment with all geospatial, GDAL, machine learning, and web libraries, and verifies all packages).*

---

## 2. Google Earth Engine (GEE) Setup & Project ID

### Why does the platform use Google Earth Engine?
Step 2 connects to Google Earth Engine to query free public **Sentinel-2** satellite data. It calculates a continuous daily vegetation index (NDVI) trajectory over your field across the soybean season to pinpoint:
1. **$t_{\text{peak}}$ (Peak Canopy)**: When the crop reached maximum vegetative cover (used to build an early canopy mask).
2. **$t_0$ (Optimal Target Scouting Date)**: When canopy NDVI drops to 80% of peak (optimal disease expression).
3. **$t_{-1}$ & $t_{+1}$ (±5-Day Window)**: Pre-target and post-target dates.

> 💡 **Important:** Step 2 is completely **optional**! If you already know your scouting dates or already have your PlanetScope imagery, you can skip Step 2 and proceed directly to **Step 3 & 4**.

### Getting Your Free GEE Project ID (1–2 minutes)
Google Earth Engine requires each user to have their own Google Cloud Project ID:
1. Visit the Earth Engine registration page: **[https://code.earthengine.google.com](https://code.earthengine.google.com)**
2. Sign in with your standard Google account.
3. If prompted to select a Cloud Project, choose **"Create a new Google Cloud Project"** (select "Unpaid / Academic / Research / Personal use").
4. Google will assign you a Project ID (typically in the format `ee-yourusername` or `your-project-name-12345`).
5. **How to authorize**:
   - **For Local Windows Users**: Double-click `authenticate_earth_engine.bat` in this folder to authorize via browser.
   - **For Cloud Web App Users**: 
     1. Go to the [Google Cloud Service Accounts Console](https://console.cloud.google.com/iam-admin/serviceaccounts).
     2. Click **+ CREATE SERVICE ACCOUNT** (name it `rcr-web`), assign role **Earth Engine User**, click **Done**.
     3. Click on the email &rarr; **KEYS** tab &rarr; **ADD KEY** &rarr; **Create new key (JSON)**.
     4. Upload that `.json` file in the platform's sidebar under *🔑 Earth Engine Access & Credentials*.
6. In the platform's left sidebar, enter your Project ID in the **Google Earth Engine project ID** field.

> **Saved accounts:** When running the platform on your own computer, your Project ID, Earth Engine key and Planet API key are remembered between sessions (stored in `%USERPROFILE%\.rcr_scout\linked_accounts.json`, outside the project folder). Use **🔄 Change** to link a different account or **🗑️ Unlink** to remove the saved one. Untick **Remember on this computer** to use a key for the current session only. Keys are never saved on the shared web version.

---

## 3. How to Launch the Platform

1. In the platform folder, **double-click `launch_platform.bat`**.
2. A command window will initialize the environment, and your default web browser will automatically open:
   ```
   http://localhost:8501
   ```
3. *Keep the command window open in the background while using the platform. When finished, simply close the browser window and close the command terminal.*

---

## 4. Step-by-Step Workflow (Zero Programming)

```
[ Step 1: Upload Field Boundary ]  -->  [ Step 2: NDVI Progress Analysis ]  -->  [ Step 3: Stage Imagery ]  -->  [ Step 4: RCR Detection & Maps ]
```

### Step 1: Upload Field Boundary Polygon
1. In **Step 1**, verify or enter your **Field ID** (e.g., `FIELD_1` or your custom field name).
2. Choose your field boundary source and upload your vector boundary file:
   - Supported vector formats:
     - **Shapefile** (either a `.zip` containing `.shp, .shx, .dbf, .prj`, or upload the individual files together)
     - **GeoPackage** (`.gpkg`)
     - **GeoJSON** (`.geojson` / `.json`)
     - **KML** (`.kml`)
   - Or select from the pre-loaded research field library.
3. The platform immediately calculates the field's area in **hectares** and **acres** and renders a high-resolution boundary preview.

---

### Step 2: Select Soybean Season & Sentinel-2 Scouting Recommendations
1. Select your target **Soybean Season (Year)** (e.g., `2025`). This season applies to Step 2 phenology tracking, Step 3 satellite staging, and Step 4 classification.
2. (Optional) Ensure your **Google Earth Engine project ID** is filled in the left sidebar (or skip to Step 3 if you already have imagery).
3. Click **"🚀 Run Sentinel-2 NDVI season progress analysis"**.
3. The platform extracts cloud-masked Sentinel-2 NDVI observations and computes a daily continuous trajectory.
4. It displays 4 recommended scouting dates:
   - **1. Early Baseline Mask ($t_{\text{peak}}$)**: Date of peak canopy growth.
   - **2. Pre-Target Point ($t_{-1}$)**: 5 days before optimal target.
   - **3. Optimal Target Date ($t_0$)**: Point where the continuous curve crosses 80% of peak NDVI.
   - **4. Post-Target Point ($t_{+1}$)**: 5 days after optimal target.
5. You can download the high-resolution season progress plot (`.png`) for field reports.

---

### Step 3: Provide PlanetScope Imagery
You need 8-band Surface Reflectance PlanetScope SuperDove GeoTIFFs (`PSScene` / `ortho_analytic_8b_sr`) covering the field:

- **Option A: Upload Downloaded GeoTIFFs (.tif, .tiff) or Order ZIP (.zip)**
  - If you download your images directly from [Planet Explorer](https://www.planet.com/explorer/):
  - Drag and drop one or multiple `.tif` / `.tiff` files, or upload the complete `.zip` order archive directly. The platform will automatically unpack and stage all valid GeoTIFFs.
  - You can upload a single image (which will be evaluated using its own canopy mask) or a multi-date time series.
- **Option B: Personal Planet API Key (Auto-Search & Order)**
  - Enter your Planet API key (found in [Planet account settings](https://www.planet.com/account/)).
  - Specify the season search window (default May 1 to October 31) and maximum cloud cover.
  - Click **"🔍 Search Planet Catalog"**. The platform displays available cloud-free scenes and highlights the dates closest to your optimal scouting recommendations.
  - Select the scenes you want and click **"⬇️ Order & download selected scenes"**.
  - Planet will clip the scenes to your exact field polygon and download them automatically.
  - *Order in progress or downloaded via web?* If an order takes time to clip, copy the 36-character **Order ID** and use the **"Resume or download existing Planet order by Order ID"** card. If the order was packaged as a `.zip` or direct rasters, it will be automatically retrieved and staged. If download links have expired (after 24–48 hours), you can upload the downloaded `.zip` or `.tif` files in Option A.

---

### Step 4: Run RCR Classification & Export GIS Maps
1. In **Step 4**, the platform discovers all staged rasters and assigns:
   - **Peak canopy mask image** (used for early $3\sigma$ canopy masking).
   - **Target classification dates** (one or multiple dates).
2. Click **"🔬 Run RCR classification on selected date(s)"**.
3. The platform executes the calibrated RCRC v3.1 model:
   - Calculates 13 normalized within-field spectral indices and reflectance bands.
   - Applies the early canopy mask ($3\sigma$ below mean NDVI) to eliminate grass waterways, roads, and bare soil.
   - Generates calibrated continuous disease probability maps ($0.0$ to $1.0$).
   - Applies the optimal threshold ($0.48$) to produce binary RCR detection masks.
4. **Interactive Outputs**:
   - High-resolution visual comparison: Normalized NDVI, continuous RCR Probability (peach color ramp), and binary disease maps.
   - Quantified disease metrics: Affected area in **hectares**, **acres**, and field-level disease incidence (**%**).
5. **One-Click Export**:
   - **Summary Table (`.csv`)**: Full tabular breakdown of acreage and percentages per date.
   - **Complete GIS Bundle (`.zip`)**: Ready-to-use GeoTIFF rasters for QGIS, ArcGIS, or Climate FieldView, plus publication-quality high-resolution figures (`.png`).

---

## 5. Sidebar Platform Settings Reference

| Setting | Default | Description |
| :--- | :---: | :--- |
| **Google Earth Engine project ID** | User-specific | Your personal Google Cloud/GEE Project ID (e.g. `ee-username`). |
| **Target senescence cutoff** | **80%** | Percentage of peak canopy NDVI defining the optimal scouting window ($t_0$). |
| **Early canopy mask threshold** | **3.0 SD** | Pixels with early baseline NDVI below $(\mu - 3.0\sigma)$ are masked out as non-canopy. |
| **RCR classification threshold** | **0.48** | Optimal probability cutoff calibrated under spatial cross-validation ($F_1 = 0.948$). |

---

## 6. Troubleshooting & FAQs

### Q: When I click Step 2, I get "Not signed up for Earth Engine or project is not registered".
- **Solution:** You need your own Earth Engine Project ID. Register for free at [https://code.earthengine.google.com](https://code.earthengine.google.com), run **`authenticate_earth_engine.bat`**, and enter your Project ID in the left sidebar.
- Alternatively, you can skip Step 2 entirely if you already have your PlanetScope imagery!

### Q: When ordering imagery in Step 3, I get "401 Unauthorized".
- **Solution:** Verify that your Planet API key was pasted correctly without extra spaces or quotes. Check your active Planet quota at [https://www.planet.com/account/](https://www.planet.com/account/).

### Q: Planet says my order is still processing.
- **Solution:** Planet's clipping servers typically take 10–20 minutes to process. Copy the **Order ID** shown on screen. You can close the app and resume anytime by entering the Order ID in the **"Resume or download existing Planet order"** box.

---

## 7. Contact & Support

**Smart Plant Pathology Lab**  
Department of Crop Sciences & Center for Digital Agriculture  
University of Illinois Urbana-Champaign  

- **Dr. Boris X. Camiletti**: [bxc@illinois.edu](mailto:bxc@illinois.edu)  
- **Dr. Bruno D. Pugliese**: [brunodp@illinois.edu](mailto:brunodp@illinois.edu)
