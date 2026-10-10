"""Generate OSL shader source from template and lens prescriptions."""

from pathlib import Path

from .lenses import load_lenses
from .scene_lights import generate_load_scene_lights

N_EXTRA = 8

_COATING_VALUES = {"none": 0.0, "single": 1.0, "multi": 2.0}
_TYPE_VALUES = {"spherical": 0, "flat": 1, "stop": 2, "aspheric": 3, "cylindrical_x": 4, "cylindrical_y": 5}


def _has_aspheric_data(surface: dict) -> bool:
    if surface.get("conic", 0.0) != 0.0:
        return True
    coeffs = surface.get("aspheric_coeffs", [])
    return any(c != 0.0 for c in coeffs)


def _format_surface_assignments(
    surfaces: list[dict], surface_types: list[str], focus: dict | None
) -> str:
    """Generate OSL assignment lines for one lens's surface data."""
    close_thicknesses = {}
    if focus is not None:
        for v in focus["variables"]:
            close_thicknesses[v["surface"]] = v["thickness_close"]

    lines = []
    for i, s in enumerate(surfaces):
        idx = f"[{i}]"
        lines.append(
            f"    radii{idx:<4} = {s['radius']:>8};  "
            f"thicknesses{idx:<4} = {s['thickness']:>6};  "
            f"iors{idx:<4} = {s['ior']};  "
            f"apertures{idx:<4} = {s['aperture']};  "
            f"abbe_v{idx:<4} = {s['abbe_v']};"
        )
    lines.append("")
    for i, s in enumerate(surfaces):
        close_val = close_thicknesses.get(i, s["thickness"])
        lines.append(f"    thicknesses_close[{i}] = {close_val};")
    lines.append("")
    for i, st in enumerate(surface_types):
        lines.append(
            f"    surface_types[{i}] = {_TYPE_VALUES[st]};"
        )

    # Only emit extra[] entries for surfaces with aspheric data;
    # OSL zero-initializes local arrays so non-aspheric surfaces
    # already have the correct default values.
    aspheric_surfaces = [
        (i, s) for i, s in enumerate(surfaces) if _has_aspheric_data(s)
    ]
    if aspheric_surfaces:
        lines.append("")
        for i, s in aspheric_surfaces:
            base = i * N_EXTRA
            k = s.get("conic", 0.0)
            coeffs = s.get("aspheric_coeffs", [0.0, 0.0, 0.0])
            a10 = coeffs[3] if len(coeffs) >= 4 else 0.0
            lines.append(
                f"    extra[{base}] = {k};  "
                f"extra[{base + 1}] = {coeffs[0]};  "
                f"extra[{base + 2}] = {coeffs[1]};  "
                f"extra[{base + 3}] = {coeffs[2]};  "
                f"extra[{base + 4}] = {a10};"
            )

    return "\n".join(lines)


def _generate_load_lens_data(lens: dict) -> str:
    """Generate per-lens #defines and the load_lens_data() function body."""
    coating_val = _COATING_VALUES[lens["coating"]]
    focus = lens.get("focus")
    close_dist = focus["close_distance"] if focus else 0.0

    lines = [
        f"#define NUM_SURFACES {len(lens['surfaces'])}",
        f"#define COATING {coating_val}",
        f"#define SQUEEZE {float(lens['squeeze'])}",
        f"#define FOCUS_CLOSE_DISTANCE {close_dist}",
        "",
        "void load_lens_data(",
        "    output float radii[MAX_SURFACES],",
        "    output float thicknesses[MAX_SURFACES],",
        "    output float iors[MAX_SURFACES],",
        "    output float apertures[MAX_SURFACES],",
        "    output float abbe_v[MAX_SURFACES],",
        "    output int surface_types[MAX_SURFACES],",
        "    output float extra[MAX_EXTRA],",
        "    output float thicknesses_close[MAX_SURFACES])",
        "{",
        f"    // {lens['name']}",
        _format_surface_assignments(
            lens["surfaces"], lens["surface_types"], focus
        ),
        "}",
    ]
    return "\n".join(lines)


def load_osl_template(
    template_path: Path, lens_dir: Path
) -> tuple[str, list[dict]]:
    """Load the OSL template and lens registry.

    Returns (template_text, lenses) where template_text has both
    {{LENS_DATA}} and {{SCENE_LIGHTS}} placeholders intact.
    """
    lenses = load_lenses(lens_dir)
    if not lenses:
        raise ValueError(f"No .toml lens files found in {lens_dir}")

    template = template_path.read_text()
    return template, lenses


def build_camera_osl(template: str, lens: dict, lights=None) -> str:
    """Build final OSL source for a single camera.

    Injects single-lens data into {{LENS_DATA}} and scene lights into
    {{SCENE_LIGHTS}}.
    """
    lens_data_block = _generate_load_lens_data(lens)
    lights_block = generate_load_scene_lights(lights or [])
    osl = template.replace("// {{LENS_DATA}}", lens_data_block)
    osl = osl.replace("// {{SCENE_LIGHTS}}", lights_block)
    return osl
