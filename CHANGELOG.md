# Changelog

All notable changes to RCR Scout are documented here. Versions follow [Semantic Versioning](https://semver.org/).

## [1.1.0] — 2026-10-16

### Added
- **Planet order ZIP upload**: upload the `.zip` exactly as downloaded from Planet (or its unzipped files). The 8-band Surface Reflectance images are detected automatically and metadata, `udm2` masks and other files are ignored.
- **Planet scene previews**: field-cropped true-colour previews of selected scenes before ordering.
- **Interactive capture calendar** in Step 4 to set the peak canopy mask image and add/remove classification dates by clicking.
- **Saved accounts**: Earth Engine project/key and Planet API key are remembered between sessions on local installs (stored outside the project folder; disabled on the shared web version). Accounts can be changed or unlinked from the sidebar.
- New app icon and header images.
- Platform version shown in the sidebar.

### Changed
- Improved Step 1 (field boundary upload) layout.
- Better spacing between maps in the results view.

### Fixed
- Planet coverage calculation for the field polygon.
- Problems placing and resuming Planet orders.

## [1.0.0] — 2026-10-15

- First public release.
