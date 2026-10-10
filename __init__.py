# Blender がエクステンションとして読み込む入口。実装は src にあり、情報は blender_manifest.toml にある


def register():
    from . import src

    src.register()


def unregister():
    from . import src

    src.unregister()
