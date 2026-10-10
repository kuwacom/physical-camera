"""Load pre-rendered lens diagram PNGs as Blender preview icons."""

from pathlib import Path

import bpy.utils.previews

_preview_collection = None


def load_previews(lenses):
    """Load pre-rendered PNG diagrams into a preview collection."""
    global _preview_collection
    cleanup()
    _preview_collection = bpy.utils.previews.new()
    previews_dir = Path(__file__).parent / "previews"
    for lens in lenses:
        stem = lens['filename_stem']
        png_path = previews_dir / f"{stem}.png"
        if png_path.exists():
            _preview_collection.load(stem, str(png_path), 'IMAGE')


def has_previews():
    """Return whether any diagram previews are loaded."""
    return _preview_collection is not None and len(_preview_collection) > 0


def get_icon_id(filename_stem):
    """Return the preview icon_id for a given lens filename stem, or 0."""
    if _preview_collection is None:
        return 0
    if filename_stem in _preview_collection:
        return _preview_collection[filename_stem].icon_id
    return 0


def cleanup():
    """Remove the preview collection."""
    global _preview_collection
    if _preview_collection is not None:
        bpy.utils.previews.remove(_preview_collection)
        _preview_collection = None
