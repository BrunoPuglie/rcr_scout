# Soybean Red Crown Rot (RCR) Automated Field Scouting Platform

[![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.38+-FF4B4B.svg)](https://streamlit.io/)
[![Institution](https://img.shields.io/badge/Institution-University%20of%20Illinois%20Urbana--Champaign-13294B.svg)](https://illinois.edu/)
[![Lab](https://img.shields.io/badge/Lab-Smart%20Plant%20Pathology%20Lab-FF5F05.svg)](https://cropsciences.illinois.edu/)

Developed by the **Smart Plant Pathology Lab** at the **University of Illinois Urbana-Champaign**, this standalone platform provides an automated, end-to-end remote sensing solution to detect, quantify, and map **Red Crown Rot (*Calonectria ilicicola*)** in commercial soybean fields.

The platform is designed for **field agronomists, extension specialists, crop consultants, and growers with zero programming experience**.

---

## 🌟 Key Capabilities

- **Zero-Code Standalone Interface**: Runs locally in your web browser with simple 1-click Windows batch launchers.
- **Sentinel-2 Phenology Discovery**: Automatically tracks continuous canopy NDVI throughout the season (via Google Earth Engine) to discover optimal disease scouting dates ($t_0$ at 80% peak canopy NDVI) and the baseline peak canopy date ($t_{\text{peak}}$).
- **Flexible PlanetScope Ingestion**:
  - **Option A**: Drag-and-drop uploaded 8-band Surface Reflectance GeoTIFFs downloaded from Planet Explorer.
  - **Option B**: Automatic cloud-free scene search and polygon-clipped downloading via personal Planet API key.
- **Calibrated Machine Learning Engine (RCRC v3.1)**:
  - Early-season $3\sigma$ canopy mask to eliminate bare soil, grass waterways, and non-crop edges.
  - Within-field normalization across 13 spectral indices and reflectance bands.
  - Calibrated probability mapping ($0.0$ to $1.0$) with optimized classification thresholding ($0.48$).
- **Export & Integration**:
  - Full tabular acreage summary (`.csv`).
  - Analysis-ready GeoTIFF rasters ready for QGIS, ArcGIS, or Climate FieldView.
  - Publication-ready 500 DPI figures (`.png`).

---

## 🚀 Quick Start Guide (Windows)

No command-line expertise is needed. Follow these 3 simple steps:

### 1. Install Miniforge (One-time)
If your computer does not have Miniforge or Conda installed, you have three easy options:
- **Option A (Fully Automated):** Simply double-click **`setup_environment.bat`** directly — it will detect that Miniforge is missing and offer to install it automatically for you via Windows Package Manager.
- **Option B (PowerShell / Command Prompt):** Run:
  ```powershell
  winget install --id CondaForge.Miniforge3 -e --accept-source-agreements --accept-package-agreements
  ```
- **Option C (Visual Installer):** Download and run the official installer: [Miniforge Windows Installer (.exe)](https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe)

### 2. Configure Environment (One-time)
- Double-click **`setup_environment.bat`** in the project folder.
- This creates and verifies the `rcrc_platform` environment with all geospatial, GDAL, and ML libraries.

### 3. Launch Platform
- Double-click **`launch_platform.bat`**.
- The platform will initialize and open automatically in your browser at:
  ```
  http://localhost:8501
  ```

---

## 🌍 Google Earth Engine (GEE) Configuration

### What is GEE used for?
GEE powers **Step 2** by extracting public Sentinel-2 satellite data to calculate your field's seasonal vegetation trajectory.

> 💡 **Step 2 is completely optional!** If you already have your PlanetScope imagery or know your capture dates, you can skip Step 2 and proceed directly to **Step 3 (Stage Imagery)** and **Step 4 (Classification)**.

### Setting up your personal GEE Project ID (Free)
Because Earth Engine permissions are tied to individual Google accounts, each user should provide their own Project ID:
1. Register for free at **[https://code.earthengine.google.com](https://code.earthengine.google.com)** (choose *Academic / Research / Personal* use).
2. Note your Project ID (e.g. `ee-yourusername` or your Google Cloud Project ID).
3. Double-click **`authenticate_earth_engine.bat`** in this folder to authorize your Google account on this computer.
4. Enter your Project ID in the platform's sidebar under **Google Earth Engine project ID**.

---

## 📁 Repository Structure

```
rcr_scout/
│
├── launch_platform.bat             # 1-Click launcher for the web platform
├── setup_environment.bat          # 1-Click environment builder
├── authenticate_earth_engine.bat   # 1-Click Earth Engine Google authorization
│
├── USER_GUIDE.md                   # Comprehensive step-by-step user manual
├── README.md                       # Platform overview and quick reference
├── environment_conda.yml           # Complete Conda environment specification (for Windows setup)
├── requirements.txt                # Python package list (for Streamlit Cloud & Pip)
├── packages.txt                    # System Linux C-libraries (for Streamlit Cloud)
│
├── app/
│   ├── app.py                      # Streamlit interactive application
│   ├── rcr_engine.py               # Remote sensing, GEE, and ML analytics engine
│   ├── rcr_worker.py               # Subprocess worker ensuring GDAL/Conda isolation
│   └── fonts/                      # Montserrat typography assets
│
├── model/
│   ├── rcrc_3_1_probability_model.joblib          # Calibrated RCR probability model
│   ├── rcrc_3_1_threshold_044_classifier.joblib  # Calibrated discrete classifier
│   └── rcrc_3_1_model_metadata.json               # Model training metadata & features
│
└── sample_data/
    └── sample_field.geojson        # Test field boundary for immediate trial
```

---

## 🔬 Scientific Background & Calibrated Model

The **RCRC v3.1** detection model was trained and validated across commercial soybean production fields in Illinois under spatial Leave-One-Field-Out cross-validation. 

- **Vegetation Indices**: Normalized Difference Vegetation Index (NDVI), Green NDVI (GNDVI), Normalized Difference Red Edge (NDRE), Enhanced Vegetation Index (EVI), Chlorophyll Index Green (CI), Chlorophyll Index Red Edge (CIRe), and Crop Senescence Index (CSI).
- **Surface Reflectance Bands**: Green (b2), Red (b4), Red Edge (b5, b6, b7), and Near-Infrared (b8).
- **Within-Field Normalization**: Cancels out illumination variances and sensor calibration drift across satellite acquisitions.
- **Canopy Masking ($3\sigma$)**: Filters out non-canopy background pixels using peak-season baseline reflectance.
- **Optimal Probability Cutoff**: $0.48$ ($F_1 = 0.948$).

---

## 📖 Detailed Instructions

For an in-depth walkthrough of field boundary preparation, PlanetScope acquisition options, and interpreting disease probability maps, refer to **[USER_GUIDE.md](USER_GUIDE.md)**.

---

## 👥 Authors & Contact

**Smart Plant Pathology Lab**  
Department of Crop Sciences & Center for Digital Agriculture  
University of Illinois Urbana-Champaign  

- **Dr. Boris X. Camiletti**: [bxc@illinois.edu](mailto:bxc@illinois.edu)  
- **Dr. Bruno D. Pugliese**: [brunodp@illinois.edu](mailto:brunodp@illinois.edu)
