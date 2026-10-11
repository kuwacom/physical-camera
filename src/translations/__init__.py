"""UI translations registered with bpy.app.translations."""

# UI の多言語対応。原文は英語で、Blender の「インターフェイスの翻訳」設定に従って切り替わる
# 言語を増やすときは、同じ形式の辞書を持つモジュールを追加して TRANSLATIONS に登録する

import bpy

from . import ja_jp

# エクステンションとしての登録名。ルートの __init__.py が src に委譲する構成のため、
# このモジュールの親パッケージの親 (src の親) が拡張のパッケージ名になる
ADDON_PACKAGE = __name__.rsplit(".", 2)[0]

# 言語コード : {(翻訳コンテキスト, 原文): 訳文}
TRANSLATIONS = {
    "ja_JP": ja_jp.TRANSLATIONS,
}


def register():
    bpy.app.translations.register(ADDON_PACKAGE, TRANSLATIONS)


def unregister():
    bpy.app.translations.unregister(ADDON_PACKAGE)
