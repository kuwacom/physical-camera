import importlib
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
LENS_DIR = SRC_DIR / "lenses"
TEMPLATE_PATH = SRC_DIR / "lens_camera.osl.template"

# src/__init__.py は bpy を import するため、__init__ を実行しない空のパッケージとして src を登録し
# codegen の相対 import (.lenses / .scene_lights) を bpy なしで解決させる
_PACKAGE = "physical_camera_src"
if _PACKAGE not in sys.modules:
    _spec = importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
    _pkg = importlib.util.module_from_spec(_spec)
    _pkg.__path__ = [str(SRC_DIR)]
    sys.modules[_PACKAGE] = _pkg

lenses = importlib.import_module(f"{_PACKAGE}.lenses")
codegen = importlib.import_module(f"{_PACKAGE}.codegen")


@pytest.fixture(scope="session")
def bundled_lenses() -> list[dict]:
    return lenses.load_lenses(LENS_DIR)
