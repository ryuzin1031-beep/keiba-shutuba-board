#!/bin/bash
# SABC評価の手動修正を全員に公開するスクリプト。
#
# 使い方:
#   1. サイトで評価バッジをクリックして修正する
#   2. 「💾 共有用に書き出す」ボタンを押す → overrides.json がダウンロードされる
#   3. このスクリプトを実行: bash publish.sh
#      (ダウンロードフォルダの最新の overrides.json を取り込んで公開する)
#
# 引数でファイルを直接指定することも可能: bash publish.sh ~/Desktop/overrides.json

set -e
cd "$(dirname "$0")"

SRC="$1"
if [ -z "$SRC" ]; then
  SRC=$(ls -t "$HOME/Downloads"/overrides*.json 2>/dev/null | head -1)
fi

if [ -z "$SRC" ] || [ ! -f "$SRC" ]; then
  echo "✗ overrides.json が見つかりません。"
  echo "  サイトの「💾 共有用に書き出す」を押してダウンロードしてから実行してください。"
  echo "  または: bash publish.sh <ファイルのパス>"
  exit 1
fi

# JSONとして妥当か確認
if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$SRC" 2>/dev/null; then
  echo "✗ $SRC は正しいJSONではありません。中止します。"
  exit 1
fi

cp "$SRC" overrides.json
echo "取り込み: $SRC → overrides.json"

if git diff --quiet overrides.json; then
  echo "変更なし(公開済みの内容と同じ)。"
  exit 0
fi

git add overrides.json
git commit -q -m "SABC評価の手動修正を更新"
git push -q
echo "✓ 公開しました。1分ほどで反映されます:"
echo "  https://ryuzin1031-beep.github.io/keiba-shutuba-board/"
