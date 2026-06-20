#!/usr/bin/env python3
"""netkeiba から重賞レースの出馬表+関連データを取得して data.json を作る。

使い方:
    python3 scraper.py [race_id]
    (省略時は 202609030411 = 2026年宝塚記念)

取得内容:
  - 出馬表(枠・馬番・馬名・性齢・斤量・騎手・調教師)
  - 各馬: 父(種牡馬)、自身の全戦績から
      通算 / 同競馬場 / 完全一致コース / 距離帯(±200m) / 芝・ダ別 の集計
  - 種牡馬: 産駒の累計・当年成績(芝/ダ別、勝馬率、平均距離)
      ※競馬場別・コース別の産駒集計は netkeiba 無料領域では非公開のため、
        「そのコースでのデータ」は出走馬自身のコース実績で補完する
  - 騎手・調教師: 通算・当年成績(勝率/連対率/複勝率、芝/ダ別)
  - SABC 初期評価(能力・距離適性・舞台適正・馬場適性)

netkeiba への負荷配慮のためリクエスト間隔 1 秒。個人利用の範囲で使うこと。
"""
import json
import re
import subprocess
import sys
import time

SLEEP = 1.0
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
VENUES = "札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉"

_cache = {}


CACHE_DIR = "/tmp/keiba_cache"


def fetch(url):
    if url in _cache:
        return _cache[url]
    import hashlib, os
    os.makedirs(CACHE_DIR, exist_ok=True)
    cf = os.path.join(CACHE_DIR, hashlib.md5(url.encode()).hexdigest())
    if os.path.exists(cf) and time.time() - os.path.getmtime(cf) < 6 * 3600:
        html = open(cf, encoding="utf-8").read()
        _cache[url] = html
        return html
    raw = subprocess.run(["curl", "-s", "--max-time", "30", "-A", UA, url],
                         capture_output=True, check=True).stdout
    html = raw.decode("euc-jp", errors="replace")
    with open(cf, "w", encoding="utf-8") as f:
        f.write(html)
    _cache[url] = html
    time.sleep(SLEEP)
    return html


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def td_list(row_html):
    return [strip_tags(m) for m in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S)]


def to_int(s):
    s = s.replace(",", "").strip()
    return int(s) if re.fullmatch(r"-?\d+", s) else None


# ---------------------------------------------------------------- 出馬表
def parse_shutuba(race_id):
    html = fetch(f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}")
    title = re.search(r"<title>([^<]*)</title>", html).group(1)
    name = re.sub(r"\s*出馬表.*", "", title)
    m = re.search(rf"(\d{{4}})年(\d+)月(\d+)日\s*({VENUES})(\d+)R", title)
    date = f"{m.group(1)}/{int(m.group(2)):02d}/{int(m.group(3)):02d}" if m else ""
    venue = m.group(4) if m else ""
    rd = re.search(r"RaceData01[^>]*>(.*?)</div>", html, re.S)
    rd_txt = strip_tags(re.sub(r"<!--.*?-->", "", rd.group(1), flags=re.S)).replace("&nbsp;", " ") if rd else ""
    cm = re.search(r"(芝|ダ|障)(\d{3,4})m", rd_txt)
    track = cm.group(1) if cm else ""
    dist = int(cm.group(2)) if cm else 0

    horses = []
    rows = re.split(r'<tr[^>]*class="HorseList"', html)[1:]
    for row in rows:
        row = row.split("</tr>")[0]
        g = lambda p: (re.search(p, row, re.S) or [None]) and re.search(p, row, re.S)
        waku = g(r'class="Waku(\d)')
        uma = g(r'class="Umaban\d[^"]*">(\d+)')
        hm = g(r'href="https://db\.netkeiba\.com/horse/(\d+)"[^>]*title="([^"]+)"')
        barei = g(r'class="Barei[^"]*">([^<]+)')
        kin = g(r'class="Barei[^"]*">[^<]+</td>\s*<td[^>]*>([\d.]+)')
        jm = g(r'jockey/result/recent/(\w+)/"[^>]*title="([^"]+)"')
        tm = g(r'trainer/result/recent/(\w+)/"[^>]*title="([^"]+)"')
        if not hm:
            continue
        horses.append({
            "waku": int(waku.group(1)) if waku else None,
            "umaban": int(uma.group(1)) if uma else None,
            "horse_id": hm.group(1), "name": hm.group(2),
            "sex_age": barei.group(1).strip() if barei else "",
            "weight_carry": kin.group(1) if kin else "",
            "jockey_id": jm.group(1) if jm else None,
            "jockey": jm.group(2) if jm else "",
            "trainer_id": tm.group(1) if tm else None,
            "trainer": tm.group(2) if tm else "",
        })
    return {"race_id": race_id, "race_name": name, "date": date, "venue": venue,
            "track": track, "distance": dist, "cond": rd_txt}, horses


# ---------------------------------------------------------------- 父(種牡馬)
def get_sire(horse_id):
    try:
        html = fetch(f"https://db.netkeiba.com/horse/ped/{horse_id}/")
        m = re.search(r'class="blood_table[^"]*">.*?<a href="https://db\.netkeiba\.com/horse/([0-9a-z]+)/">\s*([^<\s][^<]*?)\s*<', html, re.S)
        if m:
            return m.group(1), m.group(2).strip()
    except Exception as e:
        print(f"  ! ped {horse_id}: {e}", file=sys.stderr)
    return None, None


def parse_year_table(html, year):
    """種牡馬/騎手/調教師の年度別テーブルから 累計(通算) と当年の行を返す。"""
    out = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = td_list(row)
        if not cells:
            continue
        if cells[0] in ("累計", "通算") and "total" not in out:
            out["total"] = cells
        elif cells[0] == str(year) and "year" not in out:
            out["year"] = cells
    return out


def get_sire_stats(sire_id, year):
    """産駒成績: 累計と当年。c[0]=年度 (年行のみ c[1]=順位)。"""
    try:
        html = fetch(f"https://db.netkeiba.com/horse/sire/{sire_id}/")
    except Exception as e:
        print(f"  ! sire {sire_id}: {e}", file=sys.stderr)
        return None

    def parse(cells, _has_rank):
        o = 2  # 年度,順位(累計行は空) の次から
        n = [to_int(c) or 0 for c in cells[o:o + 14]]
        if len(n) < 14:
            return None
        starts, wins = n[2], n[3]
        turf_s, turf_w, dirt_s, dirt_w = n[10], n[11], n[12], n[13]
        rate = cells[o + 14] if len(cells) > o + 14 else ""
        avg_t = cells[o + 17] if len(cells) > o + 17 else ""
        return {"starts": starts, "wins": wins,
                "win_rate": round(wins / starts * 100, 1) if starts else None,
                "turf_starts": turf_s, "turf_wins": turf_w,
                "turf_win_rate": round(turf_w / turf_s * 100, 1) if turf_s else None,
                "dirt_starts": dirt_s, "dirt_wins": dirt_w,
                "winner_rate": rate, "avg_dist_turf": avg_t.replace(",", "")}

    t = parse_year_table(html, year)
    res = {}
    if "total" in t:
        res["total"] = parse(t["total"], False)
    if "year" in t:
        res["year"] = parse(t["year"], True)
    return res or None


# ---------------------------------------------------------------- 騎手・調教師
def get_person_stats(kind, pid, year):
    """kind: 'jockey' | 'trainer'。通算と当年の 勝率/連対率/複勝率 等。"""
    try:
        html = fetch(f"https://db.netkeiba.com/{kind}/result.html?id={pid}")
    except Exception as e:
        print(f"  ! {kind} {pid}: {e}", file=sys.stderr)
        return None

    def parse(cells, _has_rank):
        o = 2  # 年度,順位(通算行は空) の次から
        n = [to_int(c) for c in cells[o:o + 4]]
        if len(n) < 4 or any(v is None for v in n):
            return None
        w1, w2, w3, out_ = n
        starts = w1 + w2 + w3 + out_
        nn = [to_int(c) or 0 for c in cells[o + 4:o + 14]]
        turf_s, turf_w = (nn[6], nn[7]) if len(nn) >= 10 else (0, 0)
        dirt_s, dirt_w = (nn[8], nn[9]) if len(nn) >= 10 else (0, 0)
        rates = [c for c in cells[o + 14:o + 18] if c.endswith("%")]
        return {"starts": starts, "win1": w1, "win2": w2, "win3": w3,
                "win_rate": rates[0] if len(rates) > 0 else "",
                "quinella_rate": rates[1] if len(rates) > 1 else "",
                "show_rate": rates[2] if len(rates) > 2 else "",
                "turf_starts": turf_s, "turf_wins": turf_w,
                "dirt_starts": dirt_s, "dirt_wins": dirt_w}

    t = parse_year_table(html, year)
    res = {}
    if "total" in t:
        res["total"] = parse(t["total"], False)
    if "year" in t:
        res["year"] = parse(t["year"], True)
    return res or None


# ---------------------------------------------------------------- 馬の戦績
def get_horse_results(horse_id):
    try:
        html = fetch(f"https://db.netkeiba.com/horse/result/{horse_id}/")
    except Exception as e:
        print(f"  ! result {horse_id}: {e}", file=sys.stderr)
        return []
    results = []
    body = re.search(r'class="db_h_race_results[^"]*".*?</table>', html, re.S)
    if not body:
        return []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(0), re.S):
        cells_html = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if not cells_html:
            continue
        cells = [strip_tags(c) for c in cells_html]
        date = cells[0] if re.match(r"\d{4}/\d{2}/\d{2}", cells[0]) else None
        if not date:
            continue
        vm = re.search(rf"({VENUES})", cells[1])
        # 距離セル (芝2200 / ダ1800 / 障3000)
        track, dist = None, None
        for c in cells:
            dm = re.fullmatch(r"(芝|ダ|障)(\d{3,4})", c.replace(" ", ""))
            if dm:
                track, dist = dm.group(1), int(dm.group(2))
                break
        # 着順 = 騎手リンクセルの直前
        finish = None
        for i, ch in enumerate(cells_html):
            if "/jockey/" in ch and i > 0:
                finish = to_int(cells[i - 1])
                break
        race_name = cells[4] if len(cells) > 4 else ""
        heads = to_int(cells[6]) if len(cells) > 6 else None
        results.append({"date": date, "venue": vm.group(1) if vm else "",
                        "track": track, "dist": dist, "finish": finish,
                        "race": race_name, "heads": heads})
    return results


def agg(results):
    n = len(results)
    fin = [r["finish"] for r in results if r["finish"]]
    w1 = sum(1 for f in fin if f == 1)
    w2 = sum(1 for f in fin if f == 2)
    w3 = sum(1 for f in fin if f == 3)
    return {"starts": n, "win1": w1, "win2": w2, "win3": w3,
            "win_rate": round(w1 / n * 100, 1) if n else None,
            "quinella_rate": round((w1 + w2) / n * 100, 1) if n else None,
            "show_rate": round((w1 + w2 + w3) / n * 100, 1) if n else None}


# ---------------------------------------------------------------- SABC評価
def quantize(show_rate, n, lo_data_default="B"):
    if n == 0:
        return lo_data_default, True
    if show_rate >= 50: g = "S"
    elif show_rate >= 35: g = "A"
    elif show_rate >= 20: g = "B"
    else: g = "C"
    if n < 3 and g == "S":
        g = "A"  # サンプル不足時はS評価を保留
    return g, n < 3


def rate_ability(results):
    recent = [r["finish"] for r in results[:5] if r["finish"]]
    if not recent:
        return "B", True
    avg = sum(recent) / len(recent)
    g1_win = any(r["finish"] == 1 and re.search(r"G(I|1)\b|\(GI\)|\(G1\)", r["race"]) for r in results)
    grade_show = sum(1 for r in results if r["finish"] and r["finish"] <= 3 and re.search(r"\(G", r["race"]))
    score = 0
    if avg <= 3: score += 2
    elif avg <= 5: score += 1
    if g1_win: score += 2
    elif grade_show >= 2: score += 1
    return ["C", "B", "A", "S", "S"][min(score, 4)], False


def build_ratings(results, race, sire_stats):
    track, dist, venue = race["track"], race["distance"], race["venue"]
    same_track = [r for r in results if r["track"] == track]
    dist_band = [r for r in same_track if r["dist"] and abs(r["dist"] - dist) <= 200]
    venue_track = [r for r in same_track if r["venue"] == venue]
    exact = [r for r in venue_track if r["dist"] == dist]

    ability, ab_low = rate_ability(results)
    a = agg(dist_band)
    distance, d_low = quantize(a["show_rate"] or 0, a["starts"])
    base = exact if exact else venue_track
    a = agg(base)
    stage, s_low = quantize(a["show_rate"] or 0, a["starts"])
    # 馬場適性: 自身の同馬場複勝率と父の同馬場勝率のブレンド
    a = agg(same_track)
    own = a["show_rate"]
    sire_tr = None
    if sire_stats and sire_stats.get("total"):
        st = sire_stats["total"]
        sire_tr = st["turf_win_rate"] if track == "芝" else (
            round(st["dirt_wins"] / st["dirt_starts"] * 100, 1) if st["dirt_starts"] else None)
    if own is not None and a["starts"] >= 3:
        # 自身の同馬場複勝率を主、父産駒の同馬場勝率(平均約8%)を従として補正
        score = own * 0.8 + (min(sire_tr, 15) / 15 * 20 if sire_tr else 8)
        going, g_low = quantize(score, a["starts"])
    elif sire_tr is not None:
        going = "A" if sire_tr >= 10 else ("B" if sire_tr >= 6 else "C")
        g_low = True
    else:
        going, g_low = "B", True
    return {
        "ability": {"grade": ability, "low_data": ab_low},
        "distance": {"grade": distance, "low_data": d_low},
        "stage": {"grade": stage, "low_data": s_low},
        "going": {"grade": going, "low_data": g_low},
    }


# ---------------------------------------------------------------- main
def main():
    race_id = sys.argv[1] if len(sys.argv) > 1 else "202609030411"
    year = int(race_id[:4])
    print(f"race_id={race_id} の出馬表を取得中...")
    race, horses = parse_shutuba(race_id)
    print(f"  {race['race_name']} {race['venue']}{race['track']}{race['distance']}m {len(horses)}頭")

    sire_cache, jockey_cache, trainer_cache = {}, {}, {}
    for h in horses:
        print(f"  [{h['umaban']:>2}] {h['name']} ...")
        sid, sname = get_sire(h["horse_id"])
        h["sire_id"], h["sire"] = sid, sname or "不明"
        if sid and re.fullmatch(r"\d+", sid):
            if sid not in sire_cache:
                sire_cache[sid] = get_sire_stats(sid, year)
            h["sire_stats"] = sire_cache[sid]
        else:
            h["sire_stats"] = None
        if h["jockey_id"]:
            if h["jockey_id"] not in jockey_cache:
                jockey_cache[h["jockey_id"]] = get_person_stats("jockey", h["jockey_id"], year)
            h["jockey_stats"] = jockey_cache[h["jockey_id"]]
        if h["trainer_id"]:
            if h["trainer_id"] not in trainer_cache:
                trainer_cache[h["trainer_id"]] = get_person_stats("trainer", h["trainer_id"], year)
            h["trainer_stats"] = trainer_cache[h["trainer_id"]]

        results = get_horse_results(h["horse_id"])
        track, dist, venue = race["track"], race["distance"], race["venue"]
        same_track = [r for r in results if r["track"] == track]
        h["record"] = {
            "career": agg(results),
            "same_track": agg(same_track),
            "venue": agg([r for r in same_track if r["venue"] == venue]),
            "exact_course": agg([r for r in same_track if r["venue"] == venue and r["dist"] == dist]),
            "dist_band": agg([r for r in same_track if r["dist"] and abs(r["dist"] - dist) <= 200]),
        }
        h["recent"] = [{"date": r["date"], "race": r["race"], "venue": r["venue"],
                        "track": r["track"], "dist": r["dist"], "finish": r["finish"]}
                       for r in results[:5]]
        h["ratings"] = build_ratings(results, race, h.get("sire_stats"))

    out = {"race": race, "horses": horses,
           "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "note": "種牡馬の競馬場別・コース別産駒集計はnetkeiba無料領域では非公開のため、"
                   "産駒の芝/ダ別成績と各出走馬自身のコース実績を表示しています。"}
    path = __file__.rsplit("/", 1)[0] + "/data.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"完了: {path} ({len(horses)}頭)")


if __name__ == "__main__":
    main()
