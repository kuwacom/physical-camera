"""Load lens prescriptions from TOML files."""

import tomllib
from pathlib import Path

MAX_SURFACES = 36
VALID_SURFACE_TYPES = ("spherical", "flat", "stop", "aspheric", "cylindrical_x", "cylindrical_y")


def _resolve_surface_types(surfaces: list[dict], filename: str) -> list[str]:
    """Resolve the type for each surface from explicit or inferred values."""
    types = []
    for i, s in enumerate(surfaces):
        explicit = s.get("type")
        if explicit is not None:
            if explicit not in VALID_SURFACE_TYPES:
                raise ValueError(
                    f"{filename}: surface {i} type {explicit!r} must be one "
                    f"of {VALID_SURFACE_TYPES}"
                )
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

    close_distance = focus_raw["close_distance"]
    if not isinstance(close_distance, (int, float)) or close_distance <= 0:
        raise ValueError(
            f"{filename}: focus.close_distance {close_distance!r} "
            f"must be a positive number"
        )
    variables_raw = focus_raw.get("variable", [])
    if not variables_raw:
        raise ValueError(f"{filename}: [focus] section has no [[focus.variable]] entries")

    variables = []
    seen_surfaces = set()
    for v in variables_raw:
        idx = v["surface"]
        if idx in seen_surfaces:
            raise ValueError(
                f"{filename}: focus.variable surface {idx} listed more than once"
            )
        if idx < 0 or idx >= len(surfaces):
            raise ValueError(
                f"{filename}: focus.variable surface {idx} out of range "
                f"(0..{len(surfaces) - 1})"
            )
        if surfaces[idx]["ior"] != 1.0:
            raise ValueError(
                f"{filename}: focus.variable surface {idx} has ior "
                f"{surfaces[idx]['ior']} (expected 1.0 for air gap)"
            )
        thickness_close = v["thickness_close"]
        if thickness_close < 0:
            raise ValueError(
                f"{filename}: focus.variable surface {idx} has negative "
                f"thickness_close {thickness_close}"
            )
        seen_surfaces.add(idx)
        variables.append({"surface": idx, "thickness_close": thickness_close})

    return {"close_distance": close_distance, "variables": variables}


def load_lenses(lens_dir: Path) -> list[dict]:
    """Read all .toml lens files from lens_dir, sorted by filename."""
    lenses = []
    for toml_path in sorted(lens_dir.glob("*.toml")):
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        lens = data["lens"]
        surfaces = data["surface"]
        if len(surfaces) > MAX_SURFACES:
            raise ValueError(
                f"{toml_path.name}: {len(surfaces)} surfaces exceeds "
                f"MAX_SURFACES ({MAX_SURFACES})"
            )
        coating = lens.get("coating", "none")
        valid_coatings = ("none", "single", "multi")
        if coating not in valid_coatings:
            raise ValueError(
                f"{toml_path.name}: coating {coating!r} must be one of "
                f"{valid_coatings}"
            )
        squeeze = lens.get("squeeze", 1.0)
        if not isinstance(squeeze, (int, float)) or squeeze <= 0:
            raise ValueError(
                f"{toml_path.name}: squeeze {squeeze!r} must be a positive number"
            )
        surface_types = _resolve_surface_types(surfaces, toml_path.name)
        for i, (s, st) in enumerate(zip(surfaces, surface_types)):
            if st == "aspheric":
                if s["radius"] == 0:
                    raise ValueError(
                        f"{toml_path.name}: aspheric surface {i} must have "
                        f"nonzero radius"
                    )
                coeffs = s.get("aspheric_coeffs")
                if not isinstance(coeffs, list) or len(coeffs) not in (3, 4):
                    raise ValueError(
                        f"{toml_path.name}: aspheric surface {i} must have "
                        f"aspheric_coeffs as a list of 3 or 4 floats"
                    )
            elif st in ("cylindrical_x", "cylindrical_y"):
                if s["radius"] == 0:
                    raise ValueError(
                        f"{toml_path.name}: cylindrical surface {i} must have "
                        f"nonzero radius"
                    )
                if "aspheric_coeffs" in s or "conic" in s:
                    raise ValueError(
                        f"{toml_path.name}: cylindrical surface {i} must "
                        f"not have aspheric_coeffs or conic"
                    )
            else:
                if "aspheric_coeffs" in s or "conic" in s:
                    raise ValueError(
                        f"{toml_path.name}: non-aspheric surface {i} must "
                        f"not have aspheric_coeffs or conic"
                    )
        stop_count = surface_types.count("stop")
        if stop_count != 1:
            raise ValueError(
                f"{toml_path.name}: expected exactly 1 stop surface, "
                f"found {stop_count}"
            )
        stop_index = surface_types.index("stop")
        focus = _parse_focus(data, surfaces, toml_path.name)
        lenses.append({
            "name": lens["name"],
            "filename_stem": toml_path.stem,
            "focal_length": lens["focal_length"],
            "max_fstop": lens["max_fstop"],
            "stop_index": stop_index,
            "coating": coating,
            "squeeze": squeeze,
            "surfaces": surfaces,
            "surface_types": surface_types,
            "focus": focus,
        })
    return lenses
