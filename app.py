import streamlit as st
import osmnx as ox
import pandas as pd
import re
import requests
import time
import concurrent.futures
from urllib.parse import urlparse, parse_qs
from geopy.geocoders import Nominatim

# -------------------------------------------
# 1. ページ設定
# -------------------------------------------
st.set_page_config(
    page_title="Scooter Port Visibility Scorer", 
    layout="wide",
    initial_sidebar_state="collapsed"
)

# -------------------------------------------
# 2. 座標抽出ロジック
# -------------------------------------------
def extract_coords_from_input(user_input):
    """
    入力文字列（座標、URL、住所）から緯度経度を抽出する
    """
    if not isinstance(user_input, str):
        return None
        
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
        except:
            return None

    # パターンC: 日本語住所入力
    try:
        geolocator = Nominatim(user_agent="scooter_port_scorer_app")
        location = geolocator.geocode(user_input)
        if location:
            return location.latitude, location.longitude
    except:
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
            details.append("✅ 駅徒歩3分圏内 (+3.0)")
        else:
            details.append("・ 駅遠 (0)")
    except:
        pass

    # --- Check 2: 道路の種類 (改良版) ---
    try:
        G_all = ox.graph_from_point((lat, lon), dist=100, network_type='all')
        u, v, key = ox.distance.nearest_edges(G_all, lon, lat)
        edge_data = G_all.get_edge_data(u, v)[key]
        
        highway = edge_data.get('highway', 'unknown')
        if isinstance(highway, list): highway = highway[0]

        major_roads = ['motorway', 'trunk', 'primary', 'secondary']
        medium_roads = ['tertiary']
        living_roads = ['residential', 'unclassified', 'living_street']
        non_vehicle = ['pedestrian', 'footway', 'path', 'steps', 'cycleway']

        # 歩道救済ロジック
        final_highway = highway
        is_sidewalk_of_major = False

        if highway in non_vehicle:
            try:
                G_drive = ox.graph_from_point((lat, lon), dist=50, network_type='drive')
                u_d, v_d, key_d = ox.distance.nearest_edges(G_drive, lon, lat)
                edge_data_drive = G_drive.get_edge_data(u_d, v_d)[key_d]
                highway_drive = edge_data_drive.get('highway', 'unknown')
                if isinstance(highway_drive, list): highway_drive = highway_drive[0]

                if highway_drive in major_roads or highway_drive in medium_roads:
                    final_highway = highway_drive
                    is_sidewalk_of_major = True
                    details.append(f"ℹ️ 歩道上ですが横に{final_highway}を検知")
            except:
                pass

        if final_highway in major_roads:
            score += 2
            details.append(f"✅ 幹線道路沿い({final_highway}) (+2.0)")
        elif final_highway in medium_roads:
            score += 1
            details.append(f"✅ バス通り({final_highway}) (+1.0)")
        elif final_highway in living_roads:
            score += 0.5
            details.append(f"🏠 生活道路({final_highway}) (+0.5)")
        elif highway in ['service']:
            details.append(f"⚠️ 敷地内/私道 (0)")
        elif highway in non_vehicle and not is_sidewalk_of_major:
            details.append(f"⛔️ 車両不可エリア({highway})")
        else:
            details.append(f"・ 細街路 (0)")

    except Exception as e:
        details.append(f"⚠️ 道路データエラー")

    # --- Check 3: 交差点判定 ---
    try:
        G_simple = ox.graph_from_point((lat, lon), dist=50, network_type='drive', simplify=True)
        nearest_node = ox.distance.nearest_nodes(G_simple, lon, lat)
        degree = G_simple.degree[nearest_node]
        if degree >= 3:
            score += 1
            details.append(f"✅ 交差点/角地 (+1.0)")
    except:
        pass

    # ランク判定
    if score >= 4:
        rank = "S"
        color = "green"
    elif score >= 3:
        rank = "A"
        color = "blue"
    elif score >= 1.5:
        rank = "B"
        color = "orange"
    elif score > 0:
        rank = "C"
        color = "orange"
    else:
        rank = "D"
        color = "red"

    detail_str = " / ".join(details)
    return rank, score, detail_str, color

# -------------------------------------------
# 4. ヘルパー関数: 1行分の処理 (並列実行用)
# -------------------------------------------
def process_single_row(row_data):
    """
    DataFrameの1行(Series)を受け取り、判定結果を辞書で返す
    """
    index, row, target_col = row_data
    raw_input = str(row[target_col])
    coords = extract_coords_from_input(raw_input)

    result = {
        "index": index,
        "AIランク": "エラー",
        "AIスコア": 0,
        "AI判定理由": "座標取得失敗",
        "緯度": None,
        "経度": None
    }

    if coords:
        lat, lon = coords
        try:
            rank, score, detail, _ = assess_visibility_rank_v2(lat, lon)
            result["AIランク"] = rank
            result["AIスコア"] = score
            result["AI判定理由"] = detail
            result["緯度"] = lat
            result["経度"] = lon
        except:
            result["AI判定理由"] = "分析エラー"
    
    return result

# -------------------------------------------
# 5. UI部分
# -------------------------------------------
st.title("🛴 ポート視認性・需要判定AI")

tab1, tab2 = st.tabs(["📍 単一検索", "📂 一括判定(CSV)"])

# --- タブ1: 単一検索モード ---
with tab1:
    st.markdown("GoogleマップのURL、座標、または住所を入力してください。")
    user_input = st.text_input("場所の情報を入力", placeholder="URL / 座標 / 住所", key="single_input")

    if st.button("判定開始", type="primary", key="single_btn"):
        if not user_input:
            st.error("入力してください")
        else:
            coords = extract_coords_from_input(user_input)
            if coords:
                lat, lon = coords
                st.markdown("### 📍 判定場所")
                df_map = pd.DataFrame({'lat': [lat], 'lon': [lon]})
                st.map(df_map, zoom=15)

                with st.spinner('AI分析中...'):
                    rank, score, detail_str, color = assess_visibility_rank_v2(lat, lon)

                st.divider()
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f"総合ランク: :{color}[**{rank}**]")
                with col2:
                    st.metric("視認性スコア", f"{score} / 6.0")
                
                st.info(f"【判定理由】 {detail_str}")
            else:
                st.error("場所を特定できませんでした。")

# --- タブ2: 一括判定モード (高速化版) ---
with tab2:
    st.markdown("""
    **CSVファイルをアップロードしてください。**
    
    ✅ **推奨データ形式:**
    * **GoogleマップのURL** (短縮URL可)
    * **座標** (例: `35.611, 140.113`)
    
    ※ 最大5並列で高速処理します。
    """)
    
    uploaded_file = st.file_uploader("CSVファイルをドラッグ&ドロップ", type="csv")

    if uploaded_file:
        df = pd.read_csv(uploaded_file)
        st.dataframe(df.head(3))

        target_col = st.selectbox(
            "📍 座標またはURLが入っている列を選んでください",
            df.columns
        )

        if st.button("一括判定を実行 (高速モード)", type="primary"):
            st.info("分析を開始します。そのままお待ちください...")
            
            # 結果格納用辞書
            results = {}
            total = len(df)
            
            # プログレスバー
            progress_bar = st.progress(0)
            status_text = st.empty()

            # 並列処理の実行 (max_workers=5)
            # 5並列ならサーバー制限にかかりにくく、かつ高速
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                # タスクの作成: (index, row, target_col) のタプルを渡す
                tasks = [executor.submit(process_single_row, (i, row, target_col)) for i, row in df.iterrows()]
                
                # 完了したものから順次処理
                for i, future in enumerate(concurrent.futures.as_completed(tasks)):
                    res = future.result()
                    results[res["index"]] = res
                    
                    # 進捗更新
                    progress = (i + 1) / total
                    progress_bar.progress(progress)
                    status_text.text(f"分析中... {i+1} / {total} 件完了")

            # 結果をDataFrameに反映 (インデックス順に整列)
            results_list = [results[i] for i in range(total)]
            
            df["AIランク"] = [r["AIランク"] for r in results_list]
            df["AIスコア"] = [r["AIスコア"] for r in results_list]
            df["AI判定理由"] = [r["AI判定理由"] for r in results_list]
            df["緯度"] = [r["緯度"] for r in results_list]
            df["経度"] = [r["経度"] for r in results_list]

            st.success(f"✅ {total}件の分析が完了しました！")
            st.dataframe(df)

            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="結果CSVをダウンロード",
                data=csv,
                file_name="scooter_ai_results_fast.csv",
                mime="text/csv",
            )
