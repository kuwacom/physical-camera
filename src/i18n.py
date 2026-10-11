"""Translate strings Blender does not translate automatically (formatted values, etc.)."""

# Blender が自動で訳さない文字列(値を埋め込む文字列など)を訳すための関数
# 静的なラベルやプロパティ名は Blender が描画時に訳すため、ここを通さなくて良い

from bpy.app import translations

iface_ = translations.pgettext_iface
tip_ = translations.pgettext_tip
# pgettext_rpt が無い古いバージョンでは、ツールチップと同じ設定で訳す
rpt_ = getattr(translations, "pgettext_rpt", translations.pgettext_tip)
