"""生成した OSL ソースを Blender 同梱の oslc でコンパイルできることのテスト。

oslc.exe が見つからない環境 (Blender は oslcomp.dll しか同梱しない) では skip し、
環境変数 OSLC で明示的に指定できる。
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import LENS_DIR, TEMPLATE_PATH, codegen

_BLENDORE_ROOT = Path(r"C:\Program Files\Blender Foundation")


def _find_oslc() -> Path | None:
    """Resolve the OSL compiler: OSLC env var, then Blender installs, then PATH."""
    env = os.environ.get("OSLC")
    if env:
        return Path(env)
    for root in sorted(_BLENDORE_ROOT.glob("Blender *")):
        found = next(root.rglob("oslc.exe"), None)
        if found:
            return found
    which = shutil.which("oslc")
    return Path(which) if which else None


def _find_stdosl_dir() -> Path | None:
    """Directory containing Blender's bundled stdosl.h, if any.

    Recent oslc embeds stdosl.h, so a missing include dir is not an error.
    """
    for found in _BLENDORE_ROOT.glob("Blender */*/scripts/addons_core/cycles/shader/stdosl.h"):
        return found.parent
    return None


@pytest.fixture(scope="module")
def oslc() -> Path:
    path = _find_oslc()
    if path is None:
        pytest.skip("oslc.exe not found (set OSLC to the OSL compiler path)")
    return path


def test_generated_osl_compiles_for_every_lens(oslc, bundled_lenses, tmp_path):
    # oslc は軽いが確実に動かすため全レンズを順に実行する (並列化しない)
    # oslc の -I は -Ipath (スペースなし) 形式のみ対応 — スペースありだと
    # 翌引数がコンパイル対象と解釈されるため join する
    stdosl_dir = _find_stdosl_dir()
    include_args = [f"-I{stdosl_dir}"] if stdosl_dir else []
    template, _ = codegen.load_osl_template(TEMPLATE_PATH, LENS_DIR)

    failures = []
    for lens in bundled_lenses:
        stem = lens["filename_stem"]
        source = tmp_path / f"{stem}.osl"
        source.write_text(codegen.build_camera_osl(template, lens), encoding="utf-8")
        result = subprocess.run(
            [
                str(oslc),
                *include_args,
                "-o",
                str(tmp_path / f"{stem}.oso"),
                str(source),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            failures.append(f"{stem}: {result.stdout}{result.stderr}")

    assert not failures, f"oslc failed for {len(failures)} lens(es):\n" + "\n".join(failures)
