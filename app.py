import streamlit as st
import osmnx as ox
import pandas as pd
import re
import requests
from urllib.parse import urlparse, parse_qs

# -------------------------------------------
# 1. ページ設定
# -------------------------------------------
st.set_page_config(
    page_title="Scooter Port Visibility Scorer", 
    layout="centered",
    initial_sidebar_state="collapsed"
)

# -------------------------------------------
# 2. 座標抽出ロジック (URL対応)
# -------------------------------------------
def extract_coords_from_input(user_input):
    """
    入力文字列（座標またはGoogleMap URL）から緯度経度を抽出する
    """
    user_input = user_input.strip()

    # パターンA: 直接座標入力 "35.6117, 140.1132"
    try:
        if ',' in user_input and 'http' not in user_input:
            lat_str, lon_str = user_input.split(',')
            return float(lat_str), float(lon_str)
    except:
        pass

    # パターンB: URL入力
    if 'http' in user_input:
        try:
            # 短縮URLの展開 (maps.app.goo.glなど)
            response = requests.get(user_input, allow_redirects=True, timeout=5)
            final_url = response.url
            
            # 正規表現で @lat,lon,z パターンを探す
            # 例: .../maps/place/.../@35.611781,140.11325,17z/...
            match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', final_url)
            if match:
                return float(match.group(1)), float(match.group(2))
            
            # クエリパラメータ ?q=lat,lon パターンを探す
            parsed = urlparse(final_url)
            qs = parse_qs(parsed.query)
            if 'q' in qs:
                # q=35.6117,140.1132 の形式
                coords = qs['q'][0].split(',')
                if len(coords) >= 2:
                    return float(coords[0]), float(coords[1])
                    
            # 3dパラメータ !3d35.6117!4d140.1132 パターンを探す
            lat_match = re.search(r'!3d(-?\d+\.\d+)', final_url)
            lon_match = re.search(r'!4d(-?\d+\.\d+)', final_url)
            if lat_match and lon_match:
                return float(lat_match.group(1)), float(lon_match.group(1))

        except Exception as e:
            st.warning(f"URL解析中にエラーが発生しました: {e}")
            return None

    return None

# -------------------------------------------
# 3. 分析ロジック (AI判定エンジン)
# -------------------------------------------
@st.cache_data
def assess_visibility_rank_v2(lat, lon):
    ox.settings.log_console = False
    score = 0
    details = []

    # Check 1: 駅チカ判定 (徒歩3分/240m)
    tags_station = {'railway': ['station', 'subway_entrance'], 'public_transport': 'station'}
    try:
        stations = ox.features.features_from_point((lat, lon), tags_station, dist=240)
        if not stations.empty:
            score += 3
            details.append("✅ **駅徒歩3分圏内** (+3.0点) - ラストワンマイル需要あり")
        else:
            details.append("・ 駅遠 (0点)")
    except:
        pass

    # Check 2: 道路の種類 (範囲100m)
    try:
        G = ox.graph_from_point((lat, lon), dist=100, network_type='all')
        nearest_edge = ox.distance.nearest_edges(G, lon, lat)
        edge_data = G.get_edge_data(nearest_edge[0], nearest_edge[1])[0]
        
        highway = edge_data.get('highway', 'unknown')
        if isinstance(highway, list): highway = highway[0]

        major_roads = ['motorway', 'trunk', 'primary', 'secondary']
        medium_roads = ['tertiary']
        living_roads = ['residential', 'unclassified', 'living_street']
        private_roads = ['service']
        non_vehicle = ['pedestrian', 'footway', 'path', 'steps', 'cycleway']

        if highway in major_roads:
            score += 2
            details.append(f"✅ **幹線道路沿い** (種別: {highway}) (+2.0点) - 視認性「高」")
        elif highway in medium_roads:
            score += 1
            details.append(f"✅ **一般道・バス通り** (種別: {highway}) (+1.0点) - 視認性「中」")
        elif highway in living_roads:
            score += 0.5
            details.append(f"🏠 **住宅街・生活道路** (種別: {highway}) (+0.5点) - 視認性「低(住民のみ)」")
        elif highway in private_roads:
            service_detail = edge_data.get('service', '')
            details.append(f"⚠️ **敷地内通路・私道** (種別: {highway}/{service_detail}) (0点) - 発見困難")
        elif highway in non_vehicle:
            details.append(f"⛔️ **車両進入困難の可能性** (種別: {highway}) (判定外) - 要現地確認")
        else:
            details.append(f"・ その他細街路 (種別: {highway}) (0点)")
    except Exception as e:
        details.append(f"⚠️ 道路データ取得失敗: {str(e)}")

    # Check 3: 交差点判定 (範囲50m)
    try:
        G_simple = ox.graph_from_point((lat, lon), dist=50, network_type='drive', simplify=True)
        nearest_node = ox.distance.nearest_nodes(G_simple, lon, lat)
        degree = G_simple.degree[nearest_node]
        if degree >= 3:
            score += 1
            details.append(f"✅ **交差点/角地** (接続数:{degree}) (+1.0点) - 信号待ち等の注目あり")
        else:
            details.append("・ 単路 (交差点ではない) (0点)")
    except:
        pass

    # 総合ランク判定
    if score >= 4:
        rank = "S (極めて高い)"
        color = "green"
        comment = "駅前の大通りなど、最強の立地です。"
    elif score >= 3:
        rank = "A (高い)"
        color = "blue"
        comment = "駅近の裏道、または大通りの交差点など、優良物件です。"
    elif score >= 1.5:
        rank = "B (普通)"
        color = "orange"
        comment = "大通り沿い、または生活道路の角地など。一定の需要は見込めます。"
    elif score > 0:
        rank = "C (低い - 生活道路)"
        color = "orange"
        comment = "住宅街の中など。アプリ検索経由の利用がメインになります。"
    else:
        rank = "D (極めて低い - 敷地内/孤立)"
        color = "red"
        comment = "駅から遠く、かつ私道や奥まった場所。発見される可能性は低いです。"

    return rank, score, details, color, comment

# -------------------------------------------
# 4. UI部分 (Streamlit)
# -------------------------------------------
st.title("🛴 ポート視認性・需要判定AI")
st.markdown("""
Googleマップの **URL** または **座標** を貼り付けるだけで、その場所のポテンシャルを診断します。
""")

# 入力フォーム
user_input = st.text_input(
    "場所の情報を入力", 
    placeholder="https://maps.app.goo.gl/... または 35.611, 140.113"
)

if st.button("判定開始", type="primary"):
    if not user_input:
        st.error("URLまたは座標を入力してください")
    else:
        # 1. 入力値の解析
        coords = extract_coords_from_input(user_input)
        
        if coords:
            lat, lon = coords
            
            # 2. 地図表示
            st.markdown("### 📍 判定場所")
            df_map = pd.DataFrame({'lat': [lat], 'lon': [lon]})
            st.map(df_map, zoom=15)

            # 3. 解析実行
            with st.spinner('地図データを解析中...（10〜20秒ほどかかります）'):
                rank, score, details, color, comment = assess_visibility_rank_v2(lat, lon)

            # 4. 結果表示
            st.divider()
            st.subheader("診断結果")
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"総合ランク: :{color}[**{rank}**]")
            with col2:
                st.metric("視認性スコア", f"{score} / 6.0")
            
            st.info(comment)
            with st.expander("詳細な理由を見る（内訳）", expanded=True):
                for item in details:
                    st.markdown(item)
        else:
            st.error("座標を読み取れませんでした。正しいGoogleマップのURLか、座標を入力してください。")
