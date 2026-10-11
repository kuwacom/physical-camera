# AGENTS.md

## Commands

```sh
uv sync                            # install dev dependencies (ruff, pillow, pytest)
uv run ruff check .                # lint
uv run ruff format .               # format
uv run pytest                      # bpy-free tests (lens validation, paraxial f-numbers, codegen, light-aim MIS model)
uv run scripts/build_diagrams.py   # regenerate src/previews/*.png
blender --command extension validate
```

## Overview

Physical Camera — a Blender extension that replaces Blender's built-in camera with a physically-based lens simulation using OSL. Rays are traced backwards through real lens prescriptions (spherical surfaces, aperture stops, Fresnel transmission, chromatic dispersion) to produce realistic depth of field, bokeh, and aberrations. Requires Blender 4.5+ with Cycles and OSL enabled.

Accuracy and realism take priority in design decisions. Features with significant per-ray cost should be toggleable so users can pick the accuracy/speed tradeoff — consider approximations or importance sampling before implementing an expensive feature. Features that don't fit the existing architecture should prompt a refactor before implementation, not after.

## Project Layout

- Root `__init__.py` — entry point that only delegates to `src/`; `blender_manifest.toml` sits at the repo root, so the installable zip is built from the root
- `src/` — the extension package itself; `scripts/build_diagrams.py` (dev-only, excluded from the zip) regenerates the lens diagram PNGs in `src/previews/`
- `.github/workflows/release-latest.yml` — publishes a `latest` release zip on every push to `main`; bumping the manifest `version` also creates a `vX.Y.Z` release

## Architecture

### OSL Shader (`src/lens_camera.osl.template`)

- `// {{LENS_DATA}}`, `// {{SCENE_LIGHTS}}`, `// {{SCENE_LIGHT_PARAMS}}` and `// {{LOAD_SCENE_LIGHTS}}` placeholders are replaced per camera with generated code (on lens selection and file load; light changes do not regenerate the source). `codegen.py` indents each generated line like its placeholder
- Static `SURFACE_*` type defines live in the template; `MAX_SURFACES` / `N_EXTRA` / `MAX_EXTRA` are emitted by `codegen.py` from `lenses.MAX_SURFACES` (single source; OSL array sizes must be integer literals)
- Backward ray tracing (sensor → scene) through a multi-surface lens system, using ABCD matrix optics for focus computation and exit pupil aiming
- Surfaces are listed front-to-back but traced rear-to-front (sensor side first)
- `MAX_SURFACES` is 36 (defined in `src/lenses.py`) — any new lens must fit within this limit

### Extension package (`src/`)

- `__init__.py` — registers properties, operators, and the UI panel in Camera Properties. Each camera gets its own text datablock (`Physical Lens - {cam.name}`) holding a shader with only the selected lens. On register, loads the OSL template and lens registry; shaders are generated per camera on lens selection or file load
- `lenses.py` — reads TOML lens files from `src/lenses/` into dicts (no bpy dependency, shared by the build script). Infers a `surface_types` list per lens (`"spherical"`, `"flat"`, `"stop"`, `"aspheric"`, `"cylindrical_x"`, `"cylindrical_y"`)
- `codegen.py` — `load_osl_template()` loads the template and lens registry; `build_camera_osl()` injects one lens's data and the light-parameter plumbing into the template. Emits per-lens `#define`s (`NUM_SURFACES`, `HAS_ASPHERIC`, `COATING`, `SQUEEZE`, `FOCUS_CLOSE_DISTANCE`, `STOP_INDEX`, `TOTAL_LENGTH_INF`, `SENSOR_DISTANCE_INF`, `ENTRANCE_PUPIL_MAG_X/Y`, `FOCUS_OFFSET_DISTANCE`), a `focus_offset()` table and a `load_lens_data()` that fills the per-surface arrays
- `scene_lights.py` — collects LIGHT objects and emissive meshes in world space and maps them to camera shader parameters (`light_param_values()`); also generates the parameter declarations and the unpacking code. Positions are in world meters; sun directions are world-space "toward source" vectors. Up to `MAX_LIGHTS` (16), sorted by intensity. Lights only reach the shader when `visible_camera` is on (off by default for lights in Blender); objects in excluded or render-disabled collections, render-disabled objects, and view layers not used for rendering are skipped (`_render_objects()`). Camera-independent — camera movement does not recompile the shader
- Light passing: the values live in `cam.cycles_custom` as one `int scene_light_count` plus two `vector`s per slot (`scene_light_{i}` = world position or sun direction, `scene_light_{i}_info` = type, intensity, radius mm). The handlers (depsgraph update, frame change, render_pre, load_post) only write them when they differ (`light_params_differ()`) and only for cameras with ghosts or diffraction on, then `cam.update_tag()` — no OSL recompile. Cycles still rebuilds its camera shader group when parameter values change (values are constant-folded), but the oslc compile and text rewrite on the main thread are gone
- Multiple scenes: a camera only receives lights from a scene that uses it (`scene.camera` or a linked camera object, `_scene_cameras()`). The depsgraph / frame change handlers update the cameras of the edited scene, `render_pre` uses its `scene` argument (not `bpy.context.scene`), and load_post writes the open scene last. A camera shared by several scenes holds one light set — the last edited or rendered scene wins, and `render_pre` re-aims it at the rendered scene
- Rejected alternatives (verified in Blender 4.5 / 5.2): array shader parameters are skipped by Cycles for cameras (`arraylen > 1`), `getattribute()` in camera shaders only exposes `cam:*` and scene attributes, and `texture()` needs a file on disk whose OIIO cache does not reliably refresh
- `diagram.py` — loads the PNGs in `src/previews/` as Blender preview icons; `src/lenses/*.toml` are the lens prescriptions (see below)

## Adding a New Lens

1. Create a `.toml` file in `src/lenses/`. Files are loaded alphabetically — the filename determines the UI enum order
2. Surface data uses the PBRT convention: radii in mm, positive = center of curvature toward the sensor. `aperture` is the full clear diameter in mm (the shader uses `aperture * 0.5`). PhotonsToPhotos labels its column "semi_diameter" but the values are full diameters — use them directly. Patent "effective (ray) diameter" columns are full diameters as well; never double them (a doubled table makes `aperture / 2` exceed `|R|` and the paraxial open f-number come out at half the label)
3. Exactly one stop per lens: `type = "stop"` with `radius = 0`, `ior = 1.0`. Other surfaces infer their type from `radius` (0 = flat, nonzero = spherical)
4. Anamorphic lenses: add `squeeze = 2.0` (appropriate factor) to `[lens]` — the shader scales sensor X coordinates by it to desqueeze the output. Spherical lenses omit it (default 1.0). Optionally add a `[focus]` section for variable element spacing (below); without it, unit focusing (sensor-plane movement only)
5. Regenerate diagrams with `uv run scripts/build_diagrams.py`, then update the "Included Lenses" lists in `README.md` / `README.en.md`

### Variable Element Spacing

```toml
[focus]
close_distance = 301.0  # object distance from front vertex, in mm (patent convention)
[[focus.variable]]
surface = 16             # 0-based surface index whose thickness varies
thickness_close = 0.89   # thickness at close_distance (infinity value lives in [[surface]])
```

- Multiple `[[focus.variable]]` entries support floating-element designs; referenced surfaces must be air gaps (`ior = 1.0`)
- The shader interpolates linearly in reciprocal object-distance space between the infinity and close-focus calibration points; closer than the calibration point, close-focus thicknesses are used as-is

### Aspheric Surfaces

`type = "aspheric"` with `conic` (conic constant k, optional, default 0.0; -1 = paraboloid) and `aspheric_coeffs` = [A4, A6, A8] or [A4, A6, A8, A10] (even-polynomial coefficients). The surface must have nonzero `radius` (base radius of curvature). Non-aspheric surfaces must not have `aspheric_coeffs` or `conic`.

### Cylindrical Surfaces (Anamorphic)

- `type = "cylindrical_x"` — curvature in the X-Z plane only (X sees a curved surface, Y flat), for horizontal squeeze elements; `type = "cylindrical_y"` — curvature in the Y-Z plane only, for vertical stretch elements. Both must have nonzero `radius` (the curved axis's radius) and must not have `aspheric_coeffs` or `conic`
- The ABCD paraxial traces carry separate X and Y matrices; sensor distance is derived from the Y axis (spherical meridian), matching cinematographic focus convention. The exit pupil has separate X/Y magnifications, producing elliptical bokeh sampling

## Shader Function Pipeline

1. `load_lens_data()` — generated per camera with a single lens's data; outputs `surface_types[]` (`SURFACE_SPHERICAL=0`, `SURFACE_FLAT=1`, `SURFACE_STOP=2`, `SURFACE_ASPHERIC=3`, `SURFACE_CYLINDRICAL_X=4`, `SURFACE_CYLINDRICAL_Y=5`), `extra[]` (8 floats per surface: k, A4, A6, A8, A10, reserved×3; only non-zero entries emitted), `thicknesses_close[]`. Per-lens scalars are compile-time `#define`s: `NUM_SURFACES`, `COATING`, `SQUEEZE`, `FOCUS_CLOSE_DISTANCE`, `STOP_INDEX`, `TOTAL_LENGTH_INF` (sum of infinity-spacing thicknesses), `SENSOR_DISTANCE_INF` (Y-axis infinity-focus back focal distance from `lenses.paraxial_back_focal_distance()`, the fallback when the focus solve has no optical power)
   - Precomputation policy: values that depend only on the lens are baked by codegen. Values that depend on the focus distance (interpolated thicknesses, `vertex_z`, sensor distance, exit pupil) stay in the shader because `cam:focal_distance` is read at render time and may be animated — baking would force a recompile per focus change. Wavelength-dependent values (`dispersed_iors`, per-surface normal-incidence `surface_R`, `onaxis_T`) are computed once per ray and shared by every trace function instead of being recomputed per surface
2. `unpack_scene_light()` / `transform_lights_to_camera()` — the generated `{{LOAD_SCENE_LIGHTS}}` block unpacks the light shader parameters into arrays, then world-space light data is converted to camera space (mm) at render time via `transform("world", "camera", ...)`. Only ghost rays and diffraction spike rays (decided from the random sample before tracing) load/transform lights, so direct and diffraction-core rays do not pay for the transform
3. `rng()` — Jenkins one-at-a-time hash deriving decorrelated uniform [0,1] variates from the two camera random samples (one per salt)
4. Thickness interpolation — `alpha = clamp(close_distance / d_obj, 0, 1)` in reciprocal object-distance space
5. `compute_sensor_distance()` — Y-axis ABCD paraxial trace to find the sensor plane position (cylindrical_x surfaces skipped, cylindrical_y contribute; object distances closer than 1:1 magnification, `d_o = 2f`, are clamped to it so focus never jumps back to infinity). The paraxial plane is then shifted to the real-ray best focus by `focus_offset(alpha) * aperture_scale`: codegen (`lenses.focus_offset_table()`, ~5–90 ms per lens) traces the open-aperture on-axis beam (equal-area rings over the stop, 450/550/650 nm, X and Y combined) and picks the plane that maximizes the light within `_FOCUS_BLUR_RADIUS` (0.025 mm; RMS spot size is pulled by the halo and made triplets and soft lenses worse than paraxial) at `FOCUS_OFFSET_STEPS + 1` points of `alpha = clamp(FOCUS_OFFSET_DISTANCE / d_obj, 0, 1)` (`[focus]` close distance, else 10 × focal length), interpolated piecewise linearly because the shift is not linear in alpha. The shift is scaled linearly by the aperture (closest to the stopped-down best focus over all lenses, vs. none or squared); `compute_exit_pupil()` / `compute_field_exit_pupil()` — dual X/Y ABCD matrices for the rear subsystem give exit pupil position/magnification; the latter paraxially traces the rear group from the sensor point to project each rear aperture and the stop onto the aim plane (X/Y normalized so the elliptical pupil becomes a circle) and returns a disk that *contains* their intersection (`bound_disk_intersection()`, 1.1× margin for aberrations). The disk must bound the passable region from outside — the weight is proportional to its area, so cutting inside it loses light
6. `compute_ghost_aim_radius()` / `compute_light_aim_points()` — ABCD trace/inversion through the full ghost path for light-aimed ghost sampling (also used by debug mode 5); each of the three ghost phases steps through `ghost_abcd_surface()` (transfer → aperture constraint → reflect/refract), and all X/Y power application goes through `apply_abcd_power()`. `compute_light_aim_points()` and `diffraction_light_targets()` take the light direction in tan space from `light_tan_direction()` (ghosts view from the front vertex on the axis, diffraction from the exit point). `ghost_light_targets()` / `diffraction_light_targets()` return each aimable light's jitter ellipse (center + semi-axes) and selection weight; `sample_light_target()` picks a light (`select_light()`) and samples its ellipse uniformly, and `light_target_pdf()` evaluates the matching mixture density. The shader body combines aiming and uniform sampling by one-sample MIS (see Ghosts)
7. `trace_lens_system()` — sequential rear-to-front trace calling `refract_at_surface()` per element; includes sphere-overlap retry logic for thin flat surfaces adjacent to curved ones (both the rollback and the overshoot case use `pass_flat_surface()`; the rollback retries the curved surface in the next loop iteration so `refract_at_surface()` and `pass_flat_surface()` each have one call site). Stop scaling of semi-apertures is centralized in `surface_semi_aperture()`
   - Ghost and direct rays share `cos4_falloff()` and `write_camera_ray()` (lens space mm → Blender camera space m, spectral weight)
8. `find_surface_intersection()` — geometry-only hit test returning intersection point + normal for any surface type (1=hit, 0=miss, -1=clipped); spheres and cylinders share `intersect_curved()`, which zeroes the flat axis via `curved_axis_mask()` (also used for the normal); called by `refract_at_surface()` and `trace_ghost_path()`, which apply Snell's law + Fresnel (`refract_with_fresnel()` / `reflect_with_fresnel()`; stops pass through without refraction; reflect is for ghost bounces, never on stops). The aspheric Newton solve is compiled in only when the lens has aspheric surfaces (`HAS_ASPHERIC`)
9. `trace_ghost_path()` — a ghost path is three ranges: rear→a (reflect), a+1→b (reflect), b-1→front (refract), walked by one loop whose step flips at each reflection (`compute_ghost_aim_radius()` does the same for the ABCD trace)

### Compile time

OSL inlines every function call, and with OptiX each camera shader group becomes its own module compiled from PTX (cached per PTX in the OptiX cache). Cycles constant-folds all camera parameters, so any parameter change (f-stop, toggles, light values) produces a new module and an OptiX compile; the module size therefore sets the latency of every such change. Keep heavy code (surface intersection, refraction) at a single call site inside loops rather than calling it from several places. Measured on RTX 2080 Ti: 177–397 KB PTX, ~0.6–1.7 s OptiX compile per change. Enabling Cycles' "Open Shading Language" is not needed for the camera and triggers a one-time compile of Cycles' full OSL kernel (~25 MB PTX, minutes)
10. `check_aperture_at_plane()` / `refract_with_fresnel()` — circular/n-gon aperture clip at a z-plane; Snell's law + Fresnel for a given normal, shared by flat planes (`pass_flat_surface()`, N = -Z) and `refract_at_surface()`. The circle / n-gon rule (n-gon only on the stop with ≥ 3 blades) lives in `inside_aperture()`, shared with `find_surface_intersection()`

Throughput weighting applies cos^4 radiometric falloff and normalizes Fresnel loss against on-axis transmission. When chromatic aberration is enabled, wavelength is sampled uniformly over 400–700nm with golden-ratio decorrelation for the aperture radius sample.

### Ghosts

- When `lens_ghosts` is enabled, a fraction of samples trace ghost paths instead of direct paths. The fraction is adaptive: `clamp(sqrt(ghost_total_weight) * ghost_intensity * 2.0, 0.02, 0.5)`, where `ghost_total_weight` is Σ `R_a × R_b` over all valid Fresnel pairs (non-stop, IOR-changing) — ~37% for uncoated lenses, ~6% for multicoated. Pairs are selected with probability ∝ `R_a × R_b`. `R` depends on wavelength, so it cannot be baked; the sum and the selection use suffix sums of `R` (O(N) per ray instead of scanning up to 630 pairs)
- Ghost throughput: `cos^4 * (ghost_T / onaxis_T) * (ghost_total_weight / ghost_pair_weight) / adaptive_frac * ghost_intensity * area_ratio` — the weight ratio is the inverse pdf of pair selection, `area_ratio = inv_pdf / (ngon_area · aim_semi_ap_x · aim_semi_ap_y)` is the inverse aim-point pdf over the on-axis direct sampling area (n-gon of circumradius 1, `ngon_area = n/2·sin(2π/n)`, 128-gon for circular). With uniform sampling only, `inv_pdf = π·ghost_semi_ap²` (the ghost disk area)
- Direct throughput: `cos^4 * (T / onaxis_T) * sampling_area_ratio`, divided by `(1 - adaptive_frac)` when ghosts are active. `sampling_area_ratio = (field_semi_ap_x · field_semi_ap_y) / (aim_semi_ap_x · aim_semi_ap_y)` is the per-pixel sampling area over the on-axis area: uniform sampling has pdf = 1/area, so dividing by the pdf and normalizing by the on-axis area keeps the estimator unbiased (rear-group vignetting survives) and the exposure independent of the f-stop. When the field disk is clipped by the rear group and blades ≥ 3, the n-gon is enlarged by `1/cos(π/n)` so it contains the disk
- Light aiming (ghosts and diffraction spikes): when at least one light can be aimed at, 70% (`LIGHT_AIM_FRACTION`) of samples are aimed (ghosts: ABCD matrix inversion to the aim plane; diffraction: light direction in tan space relative to the exit ray), lights selected ∝ intensity and sampled uniformly in a jitter ellipse for their angular size (sun: `SUN_ANGULAR_RADIUS`). The other 30% use uniform sampling (ghosts: the ghost disk; diffraction: per-spike Cauchy × Cauchy). Both are combined by one-sample MIS (balance heuristic): weight = `f / (0.7·p_aim + 0.3·p_uniform)`, where `p_aim` is the intensity-weighted sum of every aimed light's ellipse density evaluated at the sample. Pixels with no aimable light use `f / p_uniform`. Radius-0 lights (delta) and pixels whose ABCD inversion is degenerate on X or Y are not aimed. The ghost integrand lives on the uniform disk, so aimed samples landing outside it contribute 0 (no clamping, which would put a singular density on the rim). `tests/test_light_aim_mis.py` checks both estimators against uniform-only sampling in a 2D model
- Diffraction PSF (spike part, `spike_energy = (1 - cos(π/n))/2` of the samples): the mean over the n spikes of `sinc²(u/θ0)·exp(-π(v/θ0⊥)²)/(θ0·θ0⊥)` (u along the spike = edge normal, v across; a sinc² across would be a rectangle's PSF with false spikes parallel to the edges, θ0 = λ/(D·cos(π/n)), θ0⊥ = λ/(D·sin(π/n))), which integrates to 1 over the plane; `diffraction_psf_pdf()` returns it with the uniform sampling density. The perturbation is added to the exit direction in tan space (x/-z, y/-z). D is the physical stop diameter, so the PSF and both sampling densities are evaluated in stop-scaled coordinates (perturbation × `ENTRANCE_PUPIL_MAG_X/Y`, the per-axis infinity-focus entrance pupil / stop ratio from `lenses.paraxial_entrance_pupil_magnification()`) and divided back before perturbing — the object-side angle follows the entrance pupil, and anamorphic X/Y pupils differ
- Debug mode 4 "Ghosts Only" traces ghosts for all samples; debug mode 5 "Ghost Aim" shows the aiming pipeline (R=light count, G=aiming valid, B=ghost pair selected)

## Coordinate System

- Lens space: rear element vertex at z=0, front element at z=-total_length, sensor at z=+sensor_distance; -z toward the scene, while Blender camera space has +z toward the scene (output conversion negates z and converts mm to meters, ×0.001)
- Sphere centers sit at `vertex_z + radius` along the optical axis; ray-sphere intersection picks the hit closest to the vertex (not the far side)

## Key Conventions

- All lens dimensions are in millimeters within the shader; Blender's camera space uses meters
- `iors[i]` is the medium *after* surface i (toward sensor), not before it
- The stop clips rays but does not refract. All type branching uses `surface_types[]` constants — never check `radius == 0` or compare against a stop index. `is_curved_surface()` is true for SPHERICAL / ASPHERIC / CYLINDRICAL_X / CYLINDRICAL_Y (used for ABCD power and sphere-overlap logic)
- `extra[i*N_EXTRA + 0..7]`: slots 0–4 are conic k, A4, A6, A8, A10; slots 5–7 reserved; non-aspheric surfaces are all zeros
- f-stop → stop size: `lenses.aperture_scale()` = `min(open_fstop / fstop, 1)`, where `open_fstop` is the paraxial (Y-axis, infinity) f-number of the data's full stop computed by `lenses.paraxial_fstop()` (EFL / entrance pupil diameter). `max_fstop` is the nominal label only; the UI minimum is `lenses.min_fstop()` = `max(max_fstop, open_fstop)`
- `lenses.load_lens()` validates each TOML against the rules above; an invalid file is skipped with a `Physical Camera: skipped lens ...` message instead of breaking registration
- Focusing uses variable element spacing when `FOCUS_CLOSE_DISTANCE > 0`, otherwise unit focusing; the ABCD solve computes the correct sensor position for either case
- Fresnel transmission is tracked per-surface and normalized against on-axis transmission for exposure compensation
- Coating reflectance uses the Airy thin-film interference formula (quarter-wave MgF₂, nc=1.38, design λ=550nm) with wavelength and angle dependence. Single coating returns the film reflectance directly; multicoating applies a power law (ratio³) clamped to bare Fresnel

## OSL Language Constraints (Blender/Cycles)

- **CPU and OptiX only** — no CUDA, HIP, or Metal
- No structs, no `#include`, no file I/O — use parallel arrays or multiple variables, everything lives in one `.osl` file, and lens data must be hardcoded or generated into the source
- Fixed-size arrays only — size must be a compile-time constant (`#define` works)
- Array shader parameters have a UI bug — values set in the UI may not reach the shader; hardcode arrays inside functions instead. For custom cameras Cycles skips array parameters entirely (`Camera::set_osl_camera`); scalar, `point`/`vector` and `color` parameters do reach the shader
- `refract(I, N, eta)` returns a zero vector on TIR; `I` must be normalized, `N` must face the incoming ray, `eta = n1/n2`
- Functions must be declared before use (no forward declarations)

## Blender OSL Camera API

The shader entry point must output `position`, `direction`, and `throughput` (set throughput to 0 to kill a ray). Key built-ins:

- `camera_shader_raster_position()` — normalized sensor position (0–1); `camera_shader_random_sample()` — two uniform random values in x/y
- `getattribute("cam:sensor_size", ...)` — physical sensor dimensions in mm; `getattribute("cam:focal_distance", ...)` — focus distance in meters
- Parameter metadata: Cycles' `osl.py` copies only `help` (as the description), `min` / `max`, `slidermin` / `slidermax`, `sensitivity` and `digits` into `cam.cycles_custom`'s ID property UI (the Parameters panel); `description` is dropped. Use `[[ string help = ..., min, max ]]` with the ranges of the matching `PhysicalCameraProperties`; light parameters (`scene_lights.generate_light_params()`) say they are set automatically. Do not add `widget = "boolean"` — it turns the ID property into a bool and resets the stored values
