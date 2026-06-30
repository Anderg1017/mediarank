# mediarank.py
# 内部自媒体排位赛系统（Google Sheets 云端存储 · 每月15号结算周期版）
# 周期定义：上个月15号 ~ 下个月15号 为一个统计周期
# 运行方式：streamlit run mediarank.py
# 依赖：streamlit, pandas, gspread, google-auth

import json
import datetime
from datetime import date
import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# 全局配置
# ============================================================
USERS = ["至宸", "明龙", "欢哥", "铭姐", "斌斌", "子烁"]
PLATFORMS = ["小红书", "抖音", "视频号", "B站"]

# 评级映射：新增「无作品」= 0 分（累加制下不加分，仅作记录）
RATING_MAP = {"无作品": 0, "好": 10, "很好": 30, "非常好": 80, "无敌": 150}
RATING_OPTIONS = list(RATING_MAP.keys())

# 客观分各指标权重（合计为 1）
METRIC_WEIGHTS = {"views": 0.30, "interactions": 0.30, "fans": 0.40}

# 结算日：每月 15 号
CUTOFF_DAY = 15

# 表格表头：period 列存「周期标签」，作为周期维度
HEADER = ["period", "user", "platform", "account", "views", "interactions", "fans", "votes"]

SHEET_NAME = "competition_data"


# ============================================================
# 周期工具（每月15号结算）
# ============================================================
def _add_month(y, m, delta):
    """在 (年, 月) 上加减若干个月，返回新的 (年, 月)。"""
    idx = (y * 12 + (m - 1)) + delta
    return idx // 12, idx % 12 + 1


def period_bounds_for_date(d):
    """
    返回某日期所属周期的 (起始日, 结束日)。
    规则：日 >= 15 -> 本月15号 ~ 下月15号；日 < 15 -> 上月15号 ~ 本月15号。
    """
    if d.day >= CUTOFF_DAY:
        start = date(d.year, d.month, CUTOFF_DAY)
        ny, nm = _add_month(d.year, d.month, 1)
        end = date(ny, nm, CUTOFF_DAY)
    else:
        end = date(d.year, d.month, CUTOFF_DAY)
        py, pm = _add_month(d.year, d.month, -1)
        start = date(py, pm, CUTOFF_DAY)
    return start, end


def period_label(start, end):
    """把周期渲染成可读标签，如 2026-06-15 ~ 2026-07-15。"""
    return f"{start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}"


def current_period_label():
    return period_label(*period_bounds_for_date(date.today()))


def period_options():
    """生成可选周期标签：当前周期 + 往前 11 个周期，方便补填/回看。"""
    start, end = period_bounds_for_date(date.today())
    opts = []
    for _ in range(12):
        opts.append(period_label(start, end))
        sy, sm = _add_month(start.year, start.month, -1)
        ey, em = _add_month(end.year, end.month, -1)
        start = date(sy, sm, CUTOFF_DAY)
        end = date(ey, em, CUTOFF_DAY)
    return opts


# ============================================================
# Google Sheets 连接
# ============================================================
@st.cache_resource
def get_worksheet():
    """用服务账号凭据连接 Google Sheets，返回第一个工作表对象。"""
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
    if ws.row_count == 0 or not ws.get_all_values():
        ws.update("A1", [HEADER])
    return ws


# ============================================================
# 数据初始化与读写（按周期组织）
# ============================================================
def init_platform_dict():
    return {"account": "", "views": 0, "interactions": 0, "fans": 0, "votes": []}


def _to_int(v):
    try:
        return max(0, int(float(v)))
    except (ValueError, TypeError):
        return 0


def load_data():
    """
    从 Google Sheets 读取并还原为嵌套结构：
    data[period][user]["platforms"][platform] = {...}
    自动兼容缺失字段，绝不抛 KeyError。
    """
    data = {}
    try:
        ws = get_worksheet()
        records = ws.get_all_records()
    except Exception:
        records = []

    for row in records:
        period = str(row.get("period", "")).strip()
        user = str(row.get("user", "")).strip()
        platform = str(row.get("platform", "")).strip()
        if not period or user not in USERS or platform not in PLATFORMS:
            continue  # 跳过非法/历史脏数据

        votes_raw = row.get("votes", "")
        try:
            votes = json.loads(votes_raw) if votes_raw not in ("", None) else []
            if not isinstance(votes, list):
                votes = []
        except (json.JSONDecodeError, TypeError):
            votes = []

        data.setdefault(period, {})
        data[period].setdefault(user, {"platforms": {p: init_platform_dict() for p in PLATFORMS}})
        data[period][user]["platforms"][platform] = {
            "account": str(row.get("account", "") or ""),
            "views": _to_int(row.get("views", 0)),
            "interactions": _to_int(row.get("interactions", 0)),
            "fans": _to_int(row.get("fans", 0)),
            "votes": [_to_int(v) for v in votes],
        }
    return data


def ensure_period(data, period):
    """确保某周期的完整结构存在（6 人 × 4 平台），返回该周期数据。"""
    data.setdefault(period, {})
    for u in USERS:
        data[period].setdefault(u, {"platforms": {p: init_platform_dict() for p in PLATFORMS}})
        for p in PLATFORMS:
            data[period][u]["platforms"].setdefault(p, init_platform_dict())
    return data[period]


def save_data(data):
    """将整个嵌套结构（所有周期）覆盖写回 Google Sheets。"""
    rows = [HEADER]
    for period in sorted(data.keys()):
        for user in USERS:
            if user not in data[period]:
                continue
            for p in PLATFORMS:
                pdata = data[period][user]["platforms"].get(p, init_platform_dict())
                rows.append([
                    period,
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
# 计分逻辑（针对某一个周期的数据）
# ============================================================
def get_platform_maxes(period_data):
    maxes = {p: {"views": 0, "interactions": 0, "fans": 0} for p in PLATFORMS}
    for user in USERS:
        if user not in period_data:
            continue
        for p in PLATFORMS:
            pdata = period_data[user]["platforms"][p]
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
    st.caption(f"📅 当前结算周期：{current_period_label()}（每月15号结算，上个15号到下个15号为一期）")

    data = load_data()
    periods = period_options()
    tab1, tab2, tab3 = st.tabs(["📝 数据提报", "🤝 代表作互评", "📊 实时排行榜"])

    # ---------------- Tab 1: 数据提报（按周期） ----------------
    with tab1:
        st.subheader("📝 提交我的本期数据")
        report_period = st.selectbox("结算周期：", periods, index=0, key="report_period")
        current_user = st.selectbox("我是：", USERS, key="report_user")
        st.caption("请填写【本周期内】发布内容的数据；不同周期分开保存，互不覆盖。未运营的平台保持 0 即可。")

        period_data = ensure_period(data, report_period)

        form_inputs = {}
        for p in PLATFORMS:
            pdata = period_data[current_user]["platforms"][p]
            with st.expander(f"📍 {p}", expanded=True):
                account = st.text_input(f"{p} 账号名称", value=pdata.get("account", ""),
                                        key=f"acc_{report_period}_{p}")
                c1, c2, c3 = st.columns(3)
                with c1:
                    views = st.number_input("总播放/阅读量", min_value=0, step=10,
                                            value=int(pdata.get("views", 0)), key=f"views_{report_period}_{p}")
                with c2:
                    interactions = st.number_input("有效互动数", min_value=0, step=1,
                                                   value=int(pdata.get("interactions", 0)), key=f"inter_{report_period}_{p}")
                with c3:
                    fans = st.number_input("新增粉丝数", min_value=0, step=1,
                                           value=int(pdata.get("fans", 0)), key=f"fans_{report_period}_{p}")
                form_inputs[p] = {"account": account, "views": int(views),
                                  "interactions": int(interactions), "fans": int(fans)}

        if st.button("提交我的数据", key="submit_report"):
            for p in PLATFORMS:
                tgt = data[report_period][current_user]["platforms"][p]
                tgt["account"] = form_inputs[p]["account"]
                tgt["views"] = form_inputs[p]["views"]
                tgt["interactions"] = form_inputs[p]["interactions"]
                tgt["fans"] = form_inputs[p]["fans"]
            save_data(data)
            st.success(f"✅ {current_user} 的【{report_period}】数据已成功保存到云端！")

    # ---------------- Tab 2: 代表作互评（按周期） ----------------
    with tab2:
        st.subheader("🤝 给同事的账号打分")
        vote_period = st.selectbox("评价周期：", periods, index=0, key="vote_period")
        period_data = ensure_period(data, vote_period)

        voter = st.selectbox("当前打分人：", USERS, key="vote_voter")
        candidates = [u for u in USERS if u != voter]
        target = st.selectbox("我要评价的作品所属人：", candidates, key="vote_target")
        platform = st.selectbox("评价哪个平台的账号：", PLATFORMS, key="vote_platform")

        target_account = period_data[target]["platforms"][platform].get("account", "")
        if target_account:
            st.info(f"📌 {target} 的【{platform}】账号（请自行搜索查看作品）：{target_account}")
        else:
            st.info(f"该同事暂未提交【{vote_period}】【{platform}】账号信息")

        st.caption("若该同事本周期在此平台没有发布作品，请选「无作品」（计 0 分）。")
        rating = st.radio("选择评级：", RATING_OPTIONS, horizontal=True, key="vote_rating")
        if st.button("提交评价", key="submit_vote"):
            score = RATING_MAP[rating]
            data[vote_period][target]["platforms"][platform].setdefault("votes", []).append(score)
            save_data(data)
            if rating == "无作品":
                st.success(f"✅ 已记录：{target} 的【{vote_period}】【{platform}】无作品（+0 分）。")
            else:
                st.success(f"✅ 已为 {target} 的【{vote_period}】【{platform}】提交评价：{rating}（+{score} 分，累加计入）！")

    # ---------------- Tab 3: 实时排行榜（按周期） ----------------
    with tab3:
        st.subheader("📊 实时排行榜")
        rank_period = st.selectbox("查看周期：", periods, index=0, key="rank_period")
        period_data = ensure_period(data, rank_period)
        st.caption("客观分采用「平台内归一化」算法（各指标除以全队该平台最高值），消除平台量级差异，更客观。")

        maxes = get_platform_maxes(period_data)
        rows = []
        for user in USERS:
            obj, sub, total = calc_user_scores(period_data[user], maxes)
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
            pdata = period_data[detail_user]["platforms"][p]
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
