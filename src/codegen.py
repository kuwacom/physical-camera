"""Generate OSL shader source from template and lens prescriptions."""

import re
from pathlib import Path

from .lenses import (
    FOCUS_OFFSET_STEPS,
    MAX_SURFACES,
    focus_offset_table,
    load_lenses,
    paraxial_back_focal_distance,
    paraxial_entrance_pupil_magnification,
)
from .scene_lights import generate_light_params, generate_load_scene_lights, generate_scene_light_defines

N_EXTRA = 8

_COATING_VALUES = {"none": 0.0, "single": 1.0, "multi": 2.0}
_TYPE_VALUES = {"spherical": 0, "flat": 1, "stop": 2, "aspheric": 3, "cylindrical_x": 4, "cylindrical_y": 5}


def _has_aspheric_data(surface: dict) -> bool:
    if surface.get("conic", 0.0) != 0.0:
        return True
    coeffs = surface.get("aspheric_coeffs", [])
    return any(c != 0.0 for c in coeffs)


def _format_surface_assignments(surfaces: list[dict], surface_types: list[str], focus: dict | None) -> str:
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
        lines.append(f"    surface_types[{i}] = {_TYPE_VALUES[st]};")

    # Only emit extra[] entries for surfaces with aspheric data;
    # OSL zero-initializes local arrays so non-aspheric surfaces
    # already have the correct default values.
    aspheric_surfaces = [(i, s) for i, s in enumerate(surfaces) if _has_aspheric_data(s)]
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
    # 無限遠間隔での前面頂点 -> 後面頂点の距離。レンズだけで決まるのでシェーダーで光線ごとに足し直さない
    total_length_inf = sum(s["thickness"] for s in lens["surfaces"][:-1])
    surfaces, types = lens["surfaces"], lens["surface_types"]
    offset_distance, offsets = focus_offset_table(lens)
    offset_values = ", ".join(f"{v:.5f}" for v in offsets)

    lines = [
        # 配列サイズは lenses.MAX_SURFACES を唯一の情報源とする
        # OSL の配列サイズは整数リテラル必須のため MAX_EXTRA は計算済みの値を出力する
        f"#define MAX_SURFACES {MAX_SURFACES}",
        f"#define N_EXTRA {N_EXTRA}",
        f"#define MAX_EXTRA {MAX_SURFACES * N_EXTRA}",
        "",
        f"#define NUM_SURFACES {len(lens['surfaces'])}",
        # 使わない面の型の処理をシェーダーから除いて OptiX のコンパイルを軽くする
        f"#define HAS_ASPHERIC {int('aspheric' in types)}",
        f"#define COATING {coating_val}",
        f"#define SQUEEZE {float(lens['squeeze'])}",
        f"#define FOCUS_CLOSE_DISTANCE {close_dist}",
        f"#define STOP_INDEX {lens['stop_index']}",
        f"#define TOTAL_LENGTH_INF {float(total_length_inf)!r}",
        # 合焦計算が解けないときの退避先 (無限遠ピントのセンサー位置)。シェーダー内では C≈0 で求められないため事前に計算する
        f"#define SENSOR_DISTANCE_INF {paraxial_back_focal_distance(lens['surfaces'], lens['surface_types'])!r}",
        # 回折角は被写体側から見た開口 (入射瞳) で決まるため、絞り径をこの倍率で入射瞳径に換算する
        f"#define ENTRANCE_PUPIL_MAG_X {paraxial_entrance_pupil_magnification(surfaces, types, 'x')!r}",
        f"#define ENTRANCE_PUPIL_MAG_Y {paraxial_entrance_pupil_magnification(surfaces, types, 'y')!r}",
        # 近軸のセンサー位置と実光線の最良ピントのずれ (開放)。光線追跡が重いのでレンズごとに事前計算して表で持つ
        f"#define FOCUS_OFFSET_DISTANCE {float(offset_distance)!r}",
        "",
        "float focus_offset(float alpha)",
        "{",
        f"    float table[{FOCUS_OFFSET_STEPS + 1}] = {{ {offset_values} }};",
        f"    float x = clamp(alpha, 0.0, 1.0) * {float(FOCUS_OFFSET_STEPS)};",
        f"    int i = min(int(x), {FOCUS_OFFSET_STEPS - 1});",
        "    return mix(table[i], table[i + 1], x - float(i));",
        "}",
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
        _format_surface_assignments(lens["surfaces"], lens["surface_types"], focus),
        "}",
    ]
    return "\n".join(lines)


def load_osl_template(template_path: Path, lens_dir: Path) -> tuple[str, list[dict]]:
    """Load the OSL template and lens registry.

    Returns (template_text, lenses) where template_text has both
    {{LENS_DATA}} and {{SCENE_LIGHTS}} placeholders intact.
    """
    lenses = load_lenses(lens_dir)
    # 全レンズが不正だと EnumProperty の選択肢が空になり UI が壊れるため、登録自体を明示的に失敗させる
    if not lenses:
        raise ValueError(f"No valid .toml lens files found in {lens_dir}")

    template = template_path.read_text()
    return template, lenses


def _replace_placeholder(osl: str, name: str, block: str) -> str:
    """Replace a ``// {{NAME}}`` line, indenting every block line like the placeholder."""
    pattern = re.compile(rf"^([ \t]*)// \{{\{{{name}\}}\}}[ \t]*$", re.MULTILINE)
    match = pattern.search(osl)
    if match is None:
        raise ValueError(f"Placeholder {{{{{name}}}}} not found in OSL template")
    indent = match.group(1)
    indented = "\n".join(f"{indent}{line}" if line else "" for line in block.split("\n"))
    return osl[: match.start()] + indented + osl[match.end() :]


def build_camera_osl(template: str, lens: dict) -> str:
    """Build final OSL source for a single camera.

    Injects single-lens data into {{LENS_DATA}} and the scene light
    parameter plumbing into {{SCENE_LIGHTS}} / {{SCENE_LIGHT_PARAMS}} /
    {{LOAD_SCENE_LIGHTS}}. Light values themselves are shader parameters.
    """
    osl = _replace_placeholder(template, "LENS_DATA", _generate_load_lens_data(lens))
    osl = _replace_placeholder(osl, "SCENE_LIGHTS", generate_scene_light_defines())
    osl = _replace_placeholder(osl, "SCENE_LIGHT_PARAMS", generate_light_params())
    osl = _replace_placeholder(osl, "LOAD_SCENE_LIGHTS", generate_load_scene_lights())
    return osl
