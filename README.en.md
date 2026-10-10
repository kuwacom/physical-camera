# Physical Camera

[日本語](README.md) | English

A Blender extension that replaces Blender's built-in camera with a physically-based lens simulation. Rays are traced through real lens prescriptions to produce realistic depth of field, bokeh shapes, chromatic aberration, and vignetting.

## Features

- Sequential ray tracing through multi-element lens designs
- Spherical, aspheric, and cylindrical surface types
- Anamorphic lens support with dual-axis ABCD optics and elliptical bokeh
- Fresnel transmission loss with single/multi coating models and natural vignetting
- Spectral chromatic aberration (400-700nm continuous sampling)
- Lens ghosts from internal reflections and aperture diffraction spikes
- Variable element spacing for internal focusing (when patent data is available)
- Polygonal aperture blades with adjustable rotation
- Focus distance driven by Blender's camera settings
- Debug visualization modes for diagnosing ray behavior

## Requirements

- Blender 4.5 or later
- Cycles render engine with OSL enabled
- CPU or OptiX rendering (OSL cameras are not supported on CUDA, HIP, or Metal)

## Installation

### Option 1: Install the zip from Preferences

1. Download the zip from [Releases](https://github.com/kuwacom/physical-camera/releases)
2. In Blender, open **Edit > Preferences > Add-ons**
3. From the `⌄` menu at the top right, choose **Install from Disk...** and select the zip
4. Enable **Physical Camera** in the list

### Option 2: Build the zip from the repository

Run the following from the repository root to create an installable zip:

```sh
blender --command extension build
```

## Usage

1. Select a camera in your scene
2. Open the Camera Properties panel
3. In the "Physical Lens" panel, click "Enable Physical Lens"
4. Choose a lens and adjust settings:
   - **Lens** — select from available lens prescriptions
   - **f-stop** — aperture size (the minimum is the lens's open f-number, computed paraxially from the lens data and shown in the panel)
   - **Aperture Blades** — 0 for circular, 3+ for polygonal bokeh
   - **Blade Rotation** — rotate the aperture shape
   - **Chromatic Aberration** — toggle spectral dispersion
   - **Lens Ghosts** — toggle ghost images from internal lens reflections
   - **Ghost Intensity** — brightness multiplier for ghosts (shown when Lens Ghosts is on)
   - **Diffraction** — simulate aperture diffraction starbursts (effective only with 3 or more aperture blades)
   - **Debug Mode** — Normal, Pinhole, Diagnostic (failure visualization), Exit Direction, Ghosts Only (renders only the ghosts), or Ghost Aim (ghost aiming diagnostic)

Focus distance is controlled through Blender's standard camera Depth of Field settings.

### Ghosts and Diffraction

Lens Ghosts and Diffraction sample the scene's lights as sources. Enable **Object Properties > Visibility > Ray Visibility > Camera** on the lights you want to use (off by default in Blender). Emissive meshes are also treated as light sources.

The panel shows warnings for problems that prevent rendering (Cycles disabled, a GPU device other than OptiX, no lights visible to camera rays, etc.). To go back to a standard camera, click **Disable Physical Lens** in the panel.

The values under **Parameters** in Cycles' Custom Camera (e.g. aperture_blades) are managed by the extension automatically — do not edit them by hand.

## Included Lenses

- Canon RF 85mm f/1.2L USM (aspheric, internal focusing)
- Cooke Triplet 50mm f/4.5
- Double Gauss 50mm f/2
- Double Gauss 100mm f/1.7
- Mamiya 55mm f/2.8 N (medium format)
- Mamiya 150mm f/4 Soft (medium format, soft focus)
- Navarro Anamorphic 2x Cinema (anamorphic, aspheric)
- Neil Front Anamorphic 2x Cinema (anamorphic, internal focusing)
- Nikkor AI AF Fisheye 16mm f/2.8D (internal focusing)
- Petzval 85mm f/2.4
- Sonnar 50mm f/1.5
- Tessar 50mm f/2.8
- Zeiss Distagon 35mm f/4
- Zeiss Planar 80mm f/2.8 (medium format)
- Zuiko 18mm f/3.5 (internal focusing)

## Adding a Lens

Create a `.toml` file in `src/lenses/` with the following format:

```toml
[lens]
name = "Lens Name"
focal_length = 50.0
max_fstop = 2.0
coating = "multi"  # "none", "single", or "multi"

[[surface]]
radius = 29.475
thickness = 3.76
ior = 1.67
aperture = 25.2
abbe_v = 57.0

[[surface]]
type = "stop"
radius = 0
thickness = 2.5
ior = 1.0
aperture = 20.0
abbe_v = 0.0

# ... more surfaces
```

Surfaces are listed front-to-back (scene-side to sensor-side). All dimensions are in millimeters. The aperture stop must have `type = "stop"` with `radius = 0` and `ior = 1.0`. Aspheric surfaces use `type = "aspheric"` with `conic` and `aspheric_coeffs` fields. Cylindrical surfaces for anamorphic lenses use `type = "cylindrical_x"` or `type = "cylindrical_y"`.

After adding a lens, regenerate diagram previews: `uv run scripts/build_diagrams.py`

See [AGENTS.md](AGENTS.md) for details on variable element spacing, aspheric coefficients, and cylindrical surface conventions.

## Development

Development dependencies are managed with [uv](https://docs.astral.sh/uv/):

```sh
# Install dev dependencies (ruff, pillow, pytest)
uv sync

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Run tests
uv run pytest
```

## License

GPL-3.0-or-later
