"""Load lens prescriptions from TOML files."""

import tomllib
from pathlib import Path

MAX_SURFACES = 36
VALID_SURFACE_TYPES = ("spherical", "flat", "stop", "aspheric", "cylindrical_x", "cylindrical_y")
VALID_COATINGS = ("none", "single", "multi")


def _number(table: dict, key: str, where: str, minimum: float | None = None, strict: bool = False) -> float:
    """Return table[key] as a number, raising ValueError when missing, non-numeric or out of range."""
    value = table.get(key)
    # bool は int のサブクラスなので明示的に弾かないと true が 1 として通ってしまう
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}: {key} must be a number, got {value!r}")
    if minimum is not None and (value <= minimum if strict else value < minimum):
        relation = ">" if strict else ">="
        raise ValueError(f"{where}: {key} {value!r} must be {relation} {minimum}")
    return value


def _resolve_surface_types(surfaces: list[dict], filename: str) -> list[str]:
    """Resolve the type for each surface from explicit or inferred values."""
    types = []
    for i, s in enumerate(surfaces):
        explicit = s.get("type")
        if explicit is not None:
            if explicit not in VALID_SURFACE_TYPES:
                raise ValueError(f"{filename}: surface {i} type {explicit!r} must be one of {VALID_SURFACE_TYPES}")
            types.append(explicit)
        elif s["radius"] == 0:
            types.append("flat")
        else:
            types.append("spherical")
    return types


def _parse_focus(data: dict, surfaces: list[dict], filename: str) -> dict | None:
    """Parse optional [focus] section from lens TOML data."""
    focus_raw = data.get("focus")
    if focus_raw is None:
        return None

    if not isinstance(focus_raw, dict):
        raise ValueError(f"{filename}: [focus] must be a table")
    close_distance = _number(focus_raw, "close_distance", f"{filename}: focus", minimum=0, strict=True)
    variables_raw = focus_raw.get("variable", [])
    if not isinstance(variables_raw, list) or not variables_raw:
        raise ValueError(f"{filename}: [focus] section has no [[focus.variable]] entries")

    variables = []
    seen_surfaces = set()
    for v in variables_raw:
        idx = v.get("surface") if isinstance(v, dict) else None
        if isinstance(idx, bool) or not isinstance(idx, int):
            raise ValueError(f"{filename}: focus.variable surface must be an integer index, got {idx!r}")
        if idx in seen_surfaces:
            raise ValueError(f"{filename}: focus.variable surface {idx} listed more than once")
        if idx < 0 or idx >= len(surfaces):
            raise ValueError(f"{filename}: focus.variable surface {idx} out of range (0..{len(surfaces) - 1})")
        if surfaces[idx]["ior"] != 1.0:
            raise ValueError(
                f"{filename}: focus.variable surface {idx} has ior {surfaces[idx]['ior']} (expected 1.0 for air gap)"
            )
        thickness_close = _number(v, "thickness_close", f"{filename}: focus.variable surface {idx}", minimum=0)
        seen_surfaces.add(idx)
        variables.append({"surface": idx, "thickness_close": thickness_close})

    return {"close_distance": close_distance, "variables": variables}


def paraxial_fstop(surfaces: list[dict], surface_types: list[str]) -> float:
    """Open-aperture f-number from a Y-axis paraxial trace (infinity focus).

    f-number = EFL / entrance pupil diameter, where the entrance pupil is
    the stop as imaged by the front group.
    """
    # 入射瞳は絞りを前群越しに見た像で、絞り径そのものとは一致しないため近軸追跡で求める
    # 合焦軸 (Y) に合わせ、Y にパワーを持たない cylindrical_x は平面として扱う
    y, nu, n = 1.0, 0.0, 1.0
    stop_height = 0.0
    stop_aperture = 0.0
    last = len(surfaces) - 1
    for i, (s, st) in enumerate(zip(surfaces, surface_types, strict=True)):
        if st == "stop":
            stop_height = y
            stop_aperture = s["aperture"]
        n_after = s["ior"]
        if st not in ("flat", "stop", "cylindrical_x"):
            nu -= y * (n_after - n) / s["radius"]
        n = n_after
        if i < last:
            y += s["thickness"] * nu / n
    if nu >= 0.0 or stop_height == 0.0:
        raise ValueError("lens has no positive focal length or the marginal ray misses the stop")
    efl = -1.0 / nu
    entrance_pupil = stop_aperture / abs(stop_height)
    return efl / entrance_pupil


def min_fstop(lens: dict) -> float:
    """Smallest f-number the lens data can actually reach (UI lower bound)."""
    # データ上の絞りが表記より小さいレンズは表記の f 値まで開けないため、実際に開ける値を下限にする
    return max(lens["max_fstop"], lens["open_fstop"])


def aperture_scale(lens: dict, fstop: float) -> float:
    """Stop diameter multiplier that makes the effective f-number equal fstop."""
    # 絞り径は入射瞳径と比例するため、データ上の開放 f 値 (近軸) を基準にすれば UI の f 値と実効 f 値が一致する
    # max_fstop 基準だと絞り径と表記 f 値がずれたレンズで明るさ・ボケ量が狂う
    return min(lens["open_fstop"] / fstop, 1.0)


def _validate_surfaces(surfaces: list[dict], surface_types: list[str], filename: str) -> None:
    for i, (s, st) in enumerate(zip(surfaces, surface_types, strict=True)):
        where = f"{filename}: surface {i}"
        _number(s, "radius", where)
        _number(s, "thickness", where, minimum=0)
        _number(s, "ior", where, minimum=1.0)
        _number(s, "aperture", where, minimum=0, strict=True)
        _number(s, "abbe_v", where, minimum=0)
        if st == "stop":
            if s["radius"] != 0 or s["ior"] != 1.0:
                raise ValueError(f"{where}: stop must have radius = 0 and ior = 1.0")
        elif st == "aspheric":
            if s["radius"] == 0:
                raise ValueError(f"{filename}: aspheric surface {i} must have nonzero radius")
            coeffs = s.get("aspheric_coeffs")
            if (
                not isinstance(coeffs, list)
                or len(coeffs) not in (3, 4)
                or any(isinstance(c, bool) or not isinstance(c, (int, float)) for c in coeffs)
            ):
                raise ValueError(
                    f"{filename}: aspheric surface {i} must have aspheric_coeffs as a list of 3 or 4 floats"
                )
            if "conic" in s:
                _number(s, "conic", where)
        elif st in ("cylindrical_x", "cylindrical_y"):
            if s["radius"] == 0:
                raise ValueError(f"{filename}: cylindrical surface {i} must have nonzero radius")
            if "aspheric_coeffs" in s or "conic" in s:
                raise ValueError(f"{filename}: cylindrical surface {i} must not have aspheric_coeffs or conic")
        else:
            if st == "spherical" and s["radius"] == 0:
                raise ValueError(f"{filename}: spherical surface {i} must have nonzero radius")
            if "aspheric_coeffs" in s or "conic" in s:
                raise ValueError(f"{filename}: non-aspheric surface {i} must not have aspheric_coeffs or conic")


def load_lens(toml_path: Path) -> dict:
    """Read and validate one lens file. Raises ValueError (or a TOML decode error) when invalid."""
    filename = toml_path.name
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    lens = data.get("lens")
    if not isinstance(lens, dict):
        raise ValueError(f"{filename}: missing [lens] table")
    name = lens.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{filename}: lens.name must be a non-empty string")
    focal_length = _number(lens, "focal_length", f"{filename}: lens", minimum=0, strict=True)
    max_fstop = _number(lens, "max_fstop", f"{filename}: lens", minimum=0, strict=True)
    surfaces = data.get("surface")
    if not isinstance(surfaces, list) or not surfaces or not all(isinstance(s, dict) for s in surfaces):
        raise ValueError(f"{filename}: missing [[surface]] entries")
    if len(surfaces) > MAX_SURFACES:
        raise ValueError(f"{filename}: {len(surfaces)} surfaces exceeds MAX_SURFACES ({MAX_SURFACES})")
    coating = lens.get("coating", "none")
    if coating not in VALID_COATINGS:
        raise ValueError(f"{filename}: coating {coating!r} must be one of {VALID_COATINGS}")
    squeeze = lens.get("squeeze", 1.0)
    if isinstance(squeeze, bool) or not isinstance(squeeze, (int, float)) or squeeze <= 0:
        raise ValueError(f"{filename}: squeeze {squeeze!r} must be a positive number")
    # 型推論で radius を参照するため、面ごとの数値検証より先に radius の有無だけ確認する
    for i, s in enumerate(surfaces):
        _number(s, "radius", f"{filename}: surface {i}")
    surface_types = _resolve_surface_types(surfaces, filename)
    _validate_surfaces(surfaces, surface_types, filename)
    stop_count = surface_types.count("stop")
    if stop_count != 1:
        raise ValueError(f"{filename}: expected exactly 1 stop surface, found {stop_count}")
    stop_index = surface_types.index("stop")
    focus = _parse_focus(data, surfaces, filename)
    try:
        open_fstop = paraxial_fstop(surfaces, surface_types)
    except ValueError as e:
        raise ValueError(f"{filename}: {e}") from e
    return {
        "name": name,
        "filename_stem": toml_path.stem,
        "focal_length": focal_length,
        "max_fstop": max_fstop,
        "open_fstop": open_fstop,
        "stop_index": stop_index,
        "coating": coating,
        "squeeze": squeeze,
        "surfaces": surfaces,
        "surface_types": surface_types,
        "focus": focus,
    }


def load_lenses(lens_dir: Path) -> list[dict]:
    """Read all .toml lens files from lens_dir, sorted by filename.

    Invalid files are skipped with a message so one broken lens does not
    prevent the others from loading.
    """
    lenses = []
    for toml_path in sorted(lens_dir.glob("*.toml")):
        try:
            lenses.append(load_lens(toml_path))
        # 検証で拾いきれない想定外の構造 (KeyError/TypeError) でもアドオン全体を落とさないため広めに捕捉する
        except (OSError, tomllib.TOMLDecodeError, ValueError, KeyError, TypeError) as e:
            detail = str(e).removeprefix(f"{toml_path.name}: ")
            print(f"Physical Camera: skipped lens {toml_path.name}: {detail}")
    return lenses
