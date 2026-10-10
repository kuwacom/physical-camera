from pathlib import Path

import pytest
from conftest import LENS_DIR, TEMPLATE_PATH, codegen, lenses

VALID_LENS = """
[lens]
name = "Test Singlet"
focal_length = 100.0
max_fstop = 4.0

[[surface]]
radius = 51.68
thickness = 5.0
ior = 1.5168
aperture = 30.0
abbe_v = 64.2

[[surface]]
radius = 0.0
thickness = 2.0
ior = 1.0
aperture = 30.0
abbe_v = 0.0

[[surface]]
type = "stop"
radius = 0.0
thickness = 95.0
ior = 1.0
aperture = 25.0
abbe_v = 0.0
"""

STOP = """
[[surface]]
type = "stop"
radius = 0.0
thickness = 1.0
ior = 1.0
aperture = 20.0
abbe_v = 0.0
"""

INVALID_LENSES = {
    "missing_max_fstop": VALID_LENS.replace("max_fstop = 4.0\n", ""),
    "missing_surface_key": VALID_LENS.replace("abbe_v = 64.2\n", ""),
    "string_radius": VALID_LENS.replace("radius = 51.68", 'radius = "51.68"'),
    "two_stops": VALID_LENS + STOP,
    "no_stop": VALID_LENS.replace('type = "stop"\n', ""),
    "too_many_surfaces": VALID_LENS + STOP.replace('type = "stop"\n', "") * lenses.MAX_SURFACES,
    "stop_with_glass": VALID_LENS.replace("thickness = 95.0\nior = 1.0", "thickness = 95.0\nior = 1.5"),
    "aspheric_without_coeffs": VALID_LENS.replace("radius = 51.68", 'type = "aspheric"\nradius = 51.68'),
    "conic_on_spherical": VALID_LENS.replace("radius = 51.68", "radius = 51.68\nconic = -1.0"),
    "cylindrical_flat": VALID_LENS.replace("radius = 51.68", 'type = "cylindrical_x"\nradius = 0.0'),
    "focus_on_glass": VALID_LENS
    + "\n[focus]\nclose_distance = 500.0\n[[focus.variable]]\nsurface = 0\nthickness_close = 6.0\n",
    "broken_toml": "[lens\nname = ",
}


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _paraxial_system(lens: dict, stop_scale: float) -> tuple[float, float]:
    """Return (EFL, entrance pupil diameter) via 2x2 ABCD matrices on the Y axis.

    Written independently of lenses.paraxial_fstop so the f-number check does
    not just compare the implementation with itself.
    """

    def mul(a, b):
        return (
            a[0] * b[0] + a[1] * b[2],
            a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2],
            a[2] * b[1] + a[3] * b[3],
        )

    # 光線ベクトルは (高さ y, 換算角 n·u)。転送は t/n、屈折はパワー (n2 - n1) / r
    m = (1.0, 0.0, 0.0, 1.0)
    n_before = 1.0
    stop_height = None
    stop_diameter = 0.0
    surfaces = lens["surfaces"]
    for i, (s, st) in enumerate(zip(surfaces, lens["surface_types"], strict=True)):
        if st == "stop":
            # 平行光 (y=1, nu=0) の絞り面での高さは行列の A 成分
            stop_height = m[0]
            stop_diameter = s["aperture"] * stop_scale
        if st in ("spherical", "aspheric", "cylindrical_y"):
            power = (s["ior"] - n_before) / s["radius"]
            m = mul((1.0, 0.0, -power, 1.0), m)
        n_before = s["ior"]
        if i < len(surfaces) - 1:
            m = mul((1.0, s["thickness"] / s["ior"], 0.0, 1.0), m)
    efl = -1.0 / m[2]
    return efl, stop_diameter / abs(stop_height)


def test_bundled_lenses_all_valid(bundled_lenses, capsys):
    capsys.readouterr()
    loaded = lenses.load_lenses(LENS_DIR)
    assert "skipped" not in capsys.readouterr().out
    assert [lens["filename_stem"] for lens in loaded] == sorted(p.stem for p in LENS_DIR.glob("*.toml"))
    assert len(bundled_lenses) == 15


@pytest.mark.parametrize("case", sorted(INVALID_LENSES))
def test_invalid_lens_is_skipped(tmp_path, capsys, case):
    _write(tmp_path / "a_valid.toml", VALID_LENS)
    _write(tmp_path / f"b_{case}.toml", INVALID_LENSES[case])
    _write(tmp_path / "c_valid.toml", VALID_LENS)

    loaded = lenses.load_lenses(tmp_path)

    assert [lens["filename_stem"] for lens in loaded] == ["a_valid", "c_valid"]
    assert f"skipped lens b_{case}.toml" in capsys.readouterr().out


def test_all_invalid_lenses_fail_registration_explicitly(tmp_path):
    _write(tmp_path / "broken.toml", INVALID_LENSES["two_stops"])
    assert lenses.load_lenses(tmp_path) == []
    with pytest.raises(ValueError, match="No valid"):
        codegen.load_osl_template(TEMPLATE_PATH, tmp_path)


def test_paraxial_fstop_of_simple_lens(tmp_path):
    # 薄肉に近い平凸単レンズで、絞りが最後にあるため入射瞳は前群 (レンズ) による絞りの像になる
    lens = lenses.load_lens(_write(tmp_path / "singlet.toml", VALID_LENS))
    efl, pupil = _paraxial_system(lens, 1.0)
    assert efl == pytest.approx(100.0, rel=0.01)
    assert lens["open_fstop"] == pytest.approx(efl / pupil, rel=1e-9)


# データの絞り径が表記の開放 f 値まで開かないため、UI 下限が近軸の実効値に引き上げられるレンズ
_UNREACHABLE_MAX_FSTOP = {
    "navarro_anamorphic_2x": "絞り径 28.289mm では近軸実効 f/3.94 までしか開かず、特許の根拠なしに絞り径を書き換えられない",
}


def _lens_params():
    params = []
    for path in sorted(LENS_DIR.glob("*.toml")):
        marks = []
        if path.stem in _UNREACHABLE_MAX_FSTOP:
            marks.append(pytest.mark.xfail(reason=_UNREACHABLE_MAX_FSTOP[path.stem], strict=True))
        params.append(pytest.param(path.stem, marks=marks, id=path.stem))
    return params


@pytest.mark.parametrize("stem", _lens_params())
def test_effective_fstop_matches_max_fstop(bundled_lenses, stem):
    lens = next(lens for lens in bundled_lenses if lens["filename_stem"] == stem)
    fstop = lens["max_fstop"]
    efl, pupil = _paraxial_system(lens, lenses.aperture_scale(lens, fstop))
    assert efl / pupil == pytest.approx(fstop, rel=0.02)


@pytest.mark.parametrize("fstop", [5.6, 8.0, 16.0])
def test_effective_fstop_matches_ui_when_stopped_down(bundled_lenses, fstop):
    for lens in bundled_lenses:
        efl, pupil = _paraxial_system(lens, lenses.aperture_scale(lens, fstop))
        assert efl / pupil == pytest.approx(fstop, rel=1e-6), lens["filename_stem"]


def test_min_fstop_is_reachable(bundled_lenses):
    for lens in bundled_lenses:
        assert lenses.aperture_scale(lens, lenses.min_fstop(lens)) <= 1.0
        efl, pupil = _paraxial_system(lens, lenses.aperture_scale(lens, lenses.min_fstop(lens)))
        assert efl / pupil == pytest.approx(lenses.min_fstop(lens), rel=1e-6), lens["filename_stem"]
