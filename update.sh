#!/bin/bash
# 別のレースのデータに更新して公開するスクリプト。
#
# 使い方:
#   bash update.sh <race_id>
#
# race_id は netkeiba の出馬表URLの「race_id=」の数字。
#   https://race.netkeiba.com/race/shutuba.html?race_id=202609030411
#                                                       ↑ これ
#
# 実行すると: データ取得 → data.json を更新 → GitHubにプッシュ(自動で公開)

set -e
cd "$(dirname "$0")"

RACE_ID="$1"
if [ -z "$RACE_ID" ]; then
  echo "✗ race_id を指定してください。"
  echo "  例: bash update.sh 202609030411"
  exit 1
fi

echo "▶ race_id=$RACE_ID のデータを取得します(2分ほどかかります)..."
python3 scraper.py "$RACE_ID"

# レースが変わったら、前のレースの手動修正(overrides.json)はリセットする
echo "{}" > overrides.json

if git diff --quiet data.json overrides.json; then
  echo "変更なし(同じデータ)。"
  exit 0
fi

git add data.json overrides.json
git commit -q -m "データ更新: race_id=$RACE_ID"
git push -q
echo "✓ 公開しました。1分ほどで反映されます:"
echo "  https://ryuzin1031-beep.github.io/keiba-shutuba-board/"
