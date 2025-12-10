import streamlit as st
import osmnx as ox
import pandas as pd
import re
import requests
from urllib.parse import urlparse, parse_qs
from geopy.geocoders import Nominatim

# -------------------------------------------
# 1. ページ設定
# -------------------------------------------
st.set_page_config(
    page_title="Scooter Port Visibility Scorer", 
    layout="centered",
    initial_sidebar_state="collapsed"
)

# -------------------------------------------
# 2. 座標抽出ロジック (URL・住所対応)
# -------------------------------------------
def extract_coords_from_input(user_input):
    """
    入力文字列（座標、URL、住所）から緯度経度を抽出する
    """
    user_input = user_input.strip()

    # パターンA: 直接座標入力
    try:
        if ',' in user_input and 'http' not in user_input and not any(c in user_input for c in "都道府県市区町村"):
            parts = user_input.split(',')
            return float(parts[0]), float(parts[1])
    except:
        pass

    # パターンB: URL入力
    if 'http' in user_input:
        try:
            response = requests.get(user_input, allow_redirects=True, timeout=5)
            final_url = response.url
            
            match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', final_url)
            if match: return float(match.group(1)), float(match.group(2))
            
            parsed = urlparse(final_url)
            qs = parse_qs(parsed.query)
            if 'q' in qs:
                coords = qs['q'][0].split(',')
                if len(coords) >= 2: return float(coords[0]), float(coords[1])
                    
            lat_match = re.search(r'!3d(-?\d+\.\d+)', final_url)
            lon_match = re.search(r'!4d(-?\d+\.\d+)', final_url)
            if lat_match and lon_match:
                return float(lat_match.group(1)), float(lon_match.group(1))
        except Exception as e:
            st.warning(f"URL解析エラー: {e}")
            return None

    # パターンC: 日本語住所入力
    try:
        geolocator = Nominatim(user_agent="scooter_port_scorer_app")
        location = geolocator.geocode(user_input)
        if location:
            st.success(f"住所が見つかりました: {location.address}")
            return location.latitude, location.longitude
        else:
            st.warning("住所が見つかりませんでした。より詳細な住所か、座標を入力してください。")
            return None
    except Exception as e:
        st.warning(f"住所検索エラー: {e}")
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

    # --- Check 1: 駅チカ判定 (徒歩3分/240m) ---
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

    # --- Check 2: 道路の種類 (改良版) ---
    try:
        # まずは全ての種類の道で最寄りを検索（歩道含む）
        G_all = ox.graph_from_point((lat, lon), dist=100, network_type='all')
        u, v, key = ox.distance.nearest_edges(G_all, lon, lat)
        edge_data = G_all.get_edge_data(u, v)[key]
        
        highway = edge_data.get('highway', 'unknown')
        if isinstance(highway, list): highway = highway[0]

        # 判定用リスト
        major_roads = ['motorway', 'trunk', 'primary', 'secondary']
        medium_roads = ['tertiary']
        living_roads = ['residential', 'unclassified', 'living_street']
        non_vehicle = ['pedestrian', 'footway', 'path', 'steps', 'cycleway']

        # 【改良ポイント】もし最寄りが「歩道」だったら、近くに「車道」がないか再チェックする
        final_highway = highway # デフォルトはそのまま
        is_sidewalk_of_major = False

        if highway in non_vehicle:
            try:
                # 車道ネットワークだけで再検索 (範囲50m)
                G_drive = ox.graph_from_point((lat, lon), dist=50, network_type='drive')
                u_d, v_d, key_d = ox.distance.nearest_edges(G_drive, lon, lat)
                
                # 距離計算 (簡易的にノード間距離などで判定、あるいはnearest_edgesの戻り値を使う手もあるが、ここでは存在チェックのみ)
                # 車道の情報を取得
                edge_data_drive = G_drive.get_edge_data(u_d, v_d)[key_d]
                highway_drive = edge_data_drive.get('highway', 'unknown')
                if isinstance(highway_drive, list): highway_drive = highway_drive[0]

                # もし近くに幹線道路があれば、評価をそちらにアップグレード
                if highway_drive in major_roads or highway_drive in medium_roads:
                    final_highway = highway_drive
                    is_sidewalk_of_major = True
                    details.append(f"ℹ️ 歩道上ですが、すぐ横に **{final_highway}** を検知しました。")
            except:
                pass # 近くに車道がなければ歩道判定のまま

        # スコアリング (判定には final_highway を使用)
        if final_highway in major_roads:
            score += 2
            details.append(f"✅ **幹線道路沿い** (種別: {final_highway}) (+2.0点) - 視認性「高」")
        elif final_highway in medium_roads:
            score += 1
            details.append(f"✅ **一般道・バス通り** (種別: {final_highway}) (+1.0点) - 視認性「中」")
        elif final_highway in living_roads:
            score += 0.5
            details.append(f"🏠 **住宅街・生活道路** (種別: {final_highway}) (+0.5点) - 視認性「低(住民のみ)」")
        elif highway in ['service']: # 元のhighway判定を使う（敷地内は敷地内）
            service_detail = edge_data.get('service', '')
            details.append(f"⚠️ **敷地内通路・私道** (種別: {highway}/{service_detail}) (0点) - 発見困難")
        elif highway in non_vehicle and not is_sidewalk_of_major:
            details.append(f"⛔️ **車両進入困難** (種別: {highway}) (判定外) - 近くに車道なし")
        else:
            details.append(f"・ その他細街路 (種別: {highway}) (0点)")

    except Exception as e:
        details.append(f"⚠️ 道路データ取得失敗: {str(e)}")

    # --- Check 3: 交差点判定 (範囲50m) ---
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
以下のいずれかを入力して、ポート候補地のポテンシャルを診断します。
* **Google Map URL** (短縮URLも可)
* **緯度, 経度** (例: 35.611, 140.113)
* **住所** (例: 千葉県千葉市中央区...)
""")

user_input = st.text_input(
    "場所の情報を入力", 
    placeholder="https://support.google.com/maps/answer/18539?hl=ja&co=GENIE.Platform%3DDesktop2... または 住所、座標"
)

if st.button("判定開始", type="primary"):
    if not user_input:
        st.error("入力してください")
    else:
        coords = extract_coords_from_input(user_input)
        
        if coords:
            lat, lon = coords
            
            st.markdown("### 📍 判定場所")
            df_map = pd.DataFrame({'lat': [lat], 'lon': [lon]})
            st.map(df_map, zoom=15)

            with st.spinner('地図データを解析中...（10〜20秒ほどかかります）'):
                rank, score, details, color, comment = assess_visibility_rank_v2(lat, lon)

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
            st.error("場所を特定できませんでした。正しいURL、座標、または住所を入力してください。")
