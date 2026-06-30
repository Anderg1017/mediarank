# mediarank.py
# 内部自媒体排位赛系统（Google Sheets 云端存储版）
# 运行方式：streamlit run mediarank.py
# 依赖：streamlit, pandas, gspread, google-auth

import json
import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# 全局配置
# ============================================================
USERS = ["至宸", "明龙", "欢哥", "铭姐", "斌斌", "子烁"]
PLATFORMS = ["小红书", "抖音", "视频号", "B站"]

RATING_MAP = {"好": 10, "很好": 30, "非常好": 80, "无敌": 150}
RATING_OPTIONS = list(RATING_MAP.keys())

# 客观分各指标权重（合计为 1）
METRIC_WEIGHTS = {"views": 0.30, "interactions": 0.30, "fans": 0.40}

# 表格里使用的固定表头
HEADER = ["user", "platform", "account", "views", "interactions", "fans", "votes"]

# 你的 Google 表格名称（需与实际创建的表格名一致）
SHEET_NAME = "competition_data"


# ============================================================
# Google Sheets 连接
# ============================================================
@st.cache_resource
def get_worksheet():
    """
    用服务账号凭据连接 Google Sheets，返回第一个工作表对象。
    凭据从 st.secrets["gcp_service_account"] 读取（部署时在后台配置）。
    """
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME)
    ws = sheet.sheet1
    # 若表格为空，写入表头
    if ws.row_count == 0 or not ws.get_all_values():
        ws.update("A1", [HEADER])
    return ws


# ============================================================
# 数据初始化与读写
# ============================================================
def init_platform_dict():
    """单个平台的初始化数据结构。"""
    return {"account": "", "views": 0, "interactions": 0, "fans": 0, "votes": []}


def init_user_dict():
    """单个用户的初始化数据结构：包含所有平台。"""
    return {"platforms": {p: init_platform_dict() for p in PLATFORMS}}


def _empty_data():
    return {u: init_user_dict() for u in USERS}


def _to_int(v):
    """稳健地把任意值转为非负整数。"""
    try:
        return max(0, int(float(v)))
    except (ValueError, TypeError):
        return 0


def load_data():
    """
    从 Google Sheets 读取所有行并还原为嵌套结构。
    自动兼容：缺失的用户/平台/字段会被补齐，绝不抛 KeyError。
    """
    data = _empty_data()
    try:
        ws = get_worksheet()
        records = ws.get_all_records()  # 依据表头返回 [{列名: 值}, ...]
    except Exception:
        records = []

    for row in records:
        user = str(row.get("user", "")).strip()
        platform = str(row.get("platform", "")).strip()
        if user not in USERS or platform not in PLATFORMS:
            continue  # 跳过非法/历史脏数据

        # votes 以 JSON 字符串保存，安全解析
        votes_raw = row.get("votes", "")
        try:
            votes = json.loads(votes_raw) if votes_raw not in ("", None) else []
            if not isinstance(votes, list):
                votes = []
        except (json.JSONDecodeError, TypeError):
            votes = []

        data[user]["platforms"][platform] = {
            "account": str(row.get("account", "") or ""),
            "views": _to_int(row.get("views", 0)),
            "interactions": _to_int(row.get("interactions", 0)),
            "fans": _to_int(row.get("fans", 0)),
            "votes": [_to_int(v) for v in votes],
        }
    return data


def save_data(data):
    """
    将嵌套结构整体覆盖写回 Google Sheets（先清空再写入）。
    24 行规模很小，整表覆盖最简单可靠。
    """
    rows = [HEADER]
    for user in USERS:
        for p in PLATFORMS:
            pdata = data[user]["platforms"][p]
            rows.append([
                user,
                p,
                pdata.get("account", ""),
                pdata.get("views", 0),
                pdata.get("interactions", 0),
                pdata.get("fans", 0),
                json.dumps(pdata.get("votes", []), ensure_ascii=False),
            ])
    ws = get_worksheet()
    ws.clear()
    ws.update("A1", rows)


# ============================================================
# 计分逻辑
# ============================================================
def get_platform_maxes(data):
    maxes = {p: {"views": 0, "interactions": 0, "fans": 0} for p in PLATFORMS}
    for user in USERS:
        for p in PLATFORMS:
            pdata = data[user]["platforms"][p]
            for metric in ("views", "interactions", "fans"):
                maxes[p][metric] = max(maxes[p][metric], pdata.get(metric, 0))
    return maxes


def calc_platform_objective(pdata, plat_max):
    score = 0.0
    for metric, weight in METRIC_WEIGHTS.items():
        max_val = plat_max.get(metric, 0)
        if max_val > 0:
            normalized = (pdata.get(metric, 0) / max_val) * 100
            score += normalized * weight
    return score


def calc_user_scores(user_data, maxes):
    obj = 0.0
    sub = 0.0
    for p in PLATFORMS:
        pdata = user_data["platforms"][p]
        obj += calc_platform_objective(pdata, maxes[p])
        sub += sum(pdata.get("votes", []))
    return obj, sub, obj + sub


# ============================================================
# Streamlit 主界面
# ============================================================
def main():
    st.set_page_config(page_title="内部自媒体排位赛系统", page_icon="🏆", layout="wide")
    st.title("🏆 内部自媒体排位赛系统")

    data = load_data()
    tab1, tab2, tab3 = st.tabs(["📝 数据提报", "🤝 代表作互评", "📊 实时排行榜"])

    # ---------------- Tab 1: 数据提报（平铺所有平台） ----------------
    with tab1:
        st.subheader("📝 提交我的本月数据")
        current_user = st.selectbox("我是：", USERS, key="report_user")
        st.caption("下方四个平台请按实际情况填写，未运营的平台留空（保持 0）即可，最后统一提交。")

        form_inputs = {}
        for p in PLATFORMS:
            pdata = data[current_user]["platforms"][p]
            with st.expander(f"📍 {p}", expanded=True):
                account = st.text_input(f"{p} 账号名称", value=pdata.get("account", ""), key=f"acc_{p}")
                c1, c2, c3 = st.columns(3)
                with c1:
                    views = st.number_input("总播放/阅读量", min_value=0, step=10,
                                            value=int(pdata.get("views", 0)), key=f"views_{p}")
                with c2:
                    interactions = st.number_input("有效互动数", min_value=0, step=1,
                                                   value=int(pdata.get("interactions", 0)), key=f"inter_{p}")
                with c3:
                    fans = st.number_input("新增粉丝数", min_value=0, step=1,
                                           value=int(pdata.get("fans", 0)), key=f"fans_{p}")
                form_inputs[p] = {"account": account, "views": int(views),
                                  "interactions": int(interactions), "fans": int(fans)}

        if st.button("提交我的数据", key="submit_report"):
            for p in PLATFORMS:
                data[current_user]["platforms"][p]["account"] = form_inputs[p]["account"]
                data[current_user]["platforms"][p]["views"] = form_inputs[p]["views"]
                data[current_user]["platforms"][p]["interactions"] = form_inputs[p]["interactions"]
                data[current_user]["platforms"][p]["fans"] = form_inputs[p]["fans"]
            save_data(data)
            st.success(f"✅ {current_user} 的全部平台数据已成功提交并保存到云端！")

    # ---------------- Tab 2: 代表作互评 ----------------
    with tab2:
        st.subheader("🤝 给同事的账号打分")
        voter = st.selectbox("当前打分人：", USERS, key="vote_voter")
        candidates = [u for u in USERS if u != voter]
        target = st.selectbox("我要评价的作品所属人：", candidates, key="vote_target")
        platform = st.selectbox("评价哪个平台的账号：", PLATFORMS, key="vote_platform")

        target_account = data[target]["platforms"][platform].get("account", "")
        if target_account:
            st.info(f"📌 {target} 的【{platform}】账号（请自行搜索查看作品）：{target_account}")
        else:
            st.info(f"该同事暂未提交【{platform}】账号信息")

        rating = st.radio("选择评级：", RATING_OPTIONS, horizontal=True, key="vote_rating")
        if st.button("提交评价", key="submit_vote"):
            score = RATING_MAP[rating]
            data[target]["platforms"][platform].setdefault("votes", []).append(score)
            save_data(data)
            st.success(f"✅ 已为 {target} 的【{platform}】提交评价：{rating}（+{score} 分，累加计入）！")

    # ---------------- Tab 3: 实时排行榜 + 详细数据 ----------------
    with tab3:
        st.subheader("📊 实时排行榜")
        st.caption("客观分采用「平台内归一化」算法（各指标除以全队该平台最高值），消除平台量级差异，更客观。")

        maxes = get_platform_maxes(data)
        rows = []
        for user in USERS:
            obj, sub, total = calc_user_scores(data[user], maxes)
            rows.append({"姓名": user, "客观数据分": round(obj, 2),
                         "主观质量分": round(sub, 2), "总成绩": round(total, 2)})
        df = pd.DataFrame(rows).sort_values(by="总成绩", ascending=False).reset_index(drop=True)
        df.insert(0, "排名", range(1, len(df) + 1))
        df = df[["排名", "姓名", "客观数据分", "主观质量分", "总成绩"]]
        st.dataframe(df, use_container_width=True)

        st.divider()
        st.subheader("🔍 查看详细数据")
        detail_user = st.selectbox("选择要查看的成员：", USERS, key="detail_user")
        detail_rows = []
        for p in PLATFORMS:
            pdata = data[detail_user]["platforms"][p]
            votes = pdata.get("votes", [])
            detail_rows.append({
                "平台": p,
                "账号名称": pdata.get("account", "") or "—",
                "播放/阅读量": pdata.get("views", 0),
                "有效互动数": pdata.get("interactions", 0),
                "新增粉丝数": pdata.get("fans", 0),
                "获评次数": len(votes),
                "主观分(累加)": sum(votes),
                "客观分(归一)": round(calc_platform_objective(pdata, maxes[p]), 2),
            })
        st.dataframe(pd.DataFrame(detail_rows), use_container_width=True)

        if st.button("🔄 刷新最新数据", key="refresh"):
            st.rerun()


if __name__ == "__main__":
    main()