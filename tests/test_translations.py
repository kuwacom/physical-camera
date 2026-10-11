"""Validate the ja_JP translation dictionary against the sources."""

import importlib.util
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"


def _load_ja_jp():
    # src/translations/__init__.py は bpy を import するため、ja_jp.py を単一モジュールとして直接読む
    spec = importlib.util.spec_from_file_location("physical_camera_ja_jp", SRC_DIR / "translations" / "ja_jp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ja_jp_keys_exist_in_source():
    """Every source string in ja_jp must still exist in src/ (detect dead keys)."""
    ja_jp = _load_ja_jp()
    ja_jp_path = Path(ja_jp.__file__).resolve()

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(SRC_DIR.rglob("*.py"))
        # 訳文ファイル自身にはキーの原文が並ぶため、死にキーの判定対象から外す
        if path.resolve() != ja_jp_path
    )

    dead_keys = [(context, original) for (context, original) in ja_jp.TRANSLATIONS if original not in source]
    assert dead_keys == []
