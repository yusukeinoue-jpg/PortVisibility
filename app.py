import streamlit as st
import osmnx as ox
import pandas as pd
# ↑↑↑ ngrok関連のimportは不要なので削除します ↑↑↑

# -------------------------------------------
# 1. ページ設定
# -------------------------------------------
st.set_page_config(
    page_title="Scooter Port Visibility Scorer", 
    layout="centered",
    initial_sidebar_state="collapsed"
)

# -------------------------------------------
# 2. 分析ロジック (AI判定エンジン)
# -------------------------------------------
# @st.cache_data は必須。一度計算した結果を保存し、高速化します。
@st.cache_data
def assess_visibility_rank_v2(lat, lon):
    """
    指定座標のポテンシャルを判定する関数
    """
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

    # --- Check 2: 道路の種類 (範囲100m) ---
    try:
        G = ox.graph_from_point((lat, lon), dist=100, network_type='all')
        nearest_edge = ox.distance.nearest_edges(G, lon, lat)
        edge_data = G.get_edge_data(nearest_edge[0], nearest_edge[1])[0]
        
        highway = edge_data.get('highway', 'unknown')
        if isinstance(highway, list): 
            highway = highway[0]

        # --- 道路詳細分類定義 ---
        major_roads = ['motorway', 'trunk', 'primary', 'secondary'] # 幹線道路
        medium_roads = ['tertiary'] # 一般道
        living_roads = ['residential', 'unclassified', 'living_street'] # 生活道路(公道)
        private_roads = ['service'] # 敷地内・私道
        non_vehicle = ['pedestrian', 'footway', 'path', 'steps', 'cycleway'] # 車両不可

        # スコアリング
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

    # --- 総合ランク判定 ---
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
# 3. UI部分 (Streamlit)
# -------------------------------------------
st.title("🛴 ポート視認性・需要判定AI")
st.markdown("""
Googleマップの座標を貼り付けるだけで、その場所のポテンシャルを診断します。
**「駅からの距離」「道路の太さ」「交差点」** をAIが自動解析します。
""")

# 入力フォーム
coord_input = st.text_input(
    "座標を入力 (例: 35.611781, 140.113250)", 
    placeholder="ここにGoogle Mapの座標をペーストしてください"
)

if st.button("判定開始", type="primary"):
    if not coord_input:
        st.error("座標を入力してください")
    else:
        try:
            # 座標の整形処理
            lat_str, lon_str = coord_input.split(',')
            lat = float(lat_str.strip())
            lon = float(lon_str.strip())

            # 地図表示
            st.markdown("### 📍 判定場所")
            df_map = pd.DataFrame({'lat': [lat], 'lon': [lon]})
            st.map(df_map, zoom=15)

            # 解析実行
            with st.spinner('地図データを解析中...（10〜20秒ほどかかります）'):
                rank, score, details, color, comment = assess_visibility_rank_v2(lat, lon)

            # 結果表示
            st.divider()
            st.subheader("診断結果")
            
            # メトリクス（ランクとスコア）
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"総合ランク: :{color}[**{rank}**]")
            with col2:
                st.metric("視認性スコア", f"{score} / 6.0")
            
            # コメント
            st.info(comment)

            # 詳細リスト
            with st.expander("詳細な理由を見る（内訳）", expanded=True):
                for item in details:
                    st.markdown(item)

        except ValueError:
            st.error("入力形式が正しくありません。「35.xxxxx, 140.xxxxx」の形式で入力してください。")
        except Exception as e:
            st.error(f"エラーが発生しました: {e}")