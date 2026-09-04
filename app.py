import re

import io
import html
import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="SOLV — CD L2 Ticket Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ============================================================
# STYLE
# ============================================================
st.markdown("""
<style>
    .main .block-container {max-width: 1450px; padding-top: 1.2rem;}
    .hero {
        background: linear-gradient(135deg,#173a5e 0%,#245f8f 55%,#2f75b5 100%);
        color:white; padding:28px 32px; border-radius:16px; margin-bottom:22px;
        box-shadow:0 8px 24px rgba(23,58,94,.15);
    }
    .hero h1 {margin:0 0 7px 0; font-size:32px; letter-spacing:.2px;}
    .hero p {margin:0; opacity:.9; font-size:15px;}
    .section {
        font-size:19px; font-weight:800; color:#173a5e;
        margin:28px 0 8px 0; padding-bottom:8px;
        border-bottom:2px solid #d9e2ec;
    }
    .smallnote {color:#64748b; font-size:13px; margin:5px 0 12px 0;}
    .kpi {
        color:white; border-radius:12px; padding:17px 18px;
        min-height:118px; box-shadow:0 4px 12px rgba(0,0,0,.08);
    }
    .kpi .label {font-size:12px; font-weight:800; letter-spacing:.2px;}
    .kpi .value {font-size:29px; font-weight:850; margin-top:10px;}
    .kpi .sub {font-size:11px; margin-top:6px; opacity:.9;}
    .blue {background:#2f75b5;} .green {background:#70ad47;}
    .orange {background:#ed7d31;} .red {background:#c00000;}
    .dark {background:#173a5e;}
</style>
""", unsafe_allow_html=True)

# ============================================================
# HELPERS
# ============================================================
def clean_text(s):
    return s.fillna("").astype(str).str.strip()

def parse_hms(value):
    if pd.isna(value):
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    s = str(value).strip()
    if not s or s.lower() in {"nan","nat","none","-"}:
        return np.nan
    try:
        if ":" in s:
            parts = s.split(":")
            if len(parts) == 3:
                h, m, sec = parts
                return float(h) + float(m)/60 + float(sec)/3600
            if len(parts) == 2:
                m, sec = parts
                return float(m)/60 + float(sec)/3600
        return float(s)
    except Exception:
        return np.nan

def format_hms(hours):
    if hours is None or pd.isna(hours):
        return "—"
    total = max(0, int(round(float(hours) * 3600)))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h}:{m:02d}:{s:02d}"

def format_days(hours):
    if hours is None or pd.isna(hours):
        return "—"
    return f"{float(hours)/24:.1f} days"

def unique_ticket_count(df):
    if df.empty:
        return 0
    return int(df["Ticket ID"].nunique())

def kpi(label, value, colour="blue", sub=""):
    st.markdown(
        f"""
        <div class="kpi {colour}">
          <div class="label">{html.escape(str(label))}</div>
          <div class="value">{html.escape(str(value))}</div>
          {f'<div class="sub">{html.escape(str(sub))}</div>' if sub else ''}
        </div>
        """,
        unsafe_allow_html=True
    )

def safe_pct(num, den):
    return (num / den * 100) if den else 0.0

# ============================================================
# DATA PREPARATION
# ============================================================
REQUIRED = ["Ticket ID", "Group", "Status", "Created time",
            "Closed time", "Resolution time (in hrs)"]

def prepare_data(raw):
    df = raw.copy()

    for c in ["Ticket ID","Group","Status","Created time","Closed time",
              "Resolution time (in hrs)","Reason for pendency",
              "Category","Sub-Category","Priority","Type",
              "Resolution status","First response time (in hrs)",
              "Agent","SO Helpline_L1","SO Helpline_L2"]:
        if c not in df.columns:
            df[c] = np.nan

    df["Ticket ID"] = df["Ticket ID"].astype(str).str.strip()
    df["_Group"] = clean_text(df["Group"]).replace("", "No Group")
    df["_Status"] = clean_text(df["Status"]).str.upper()

    # Treat Resolved as closed for resolution/SLA metrics because it has a
    # resolved state but may not have a Closed time.
    df["_ClosedLike"] = df["_Status"].isin(["CLOSED", "RESOLVED"])

    df["_Created"] = pd.to_datetime(df["Created time"], errors="coerce")
    df["_Closed"] = pd.to_datetime(df["Closed time"], errors="coerce")

    # Resolution time: prefer the raw export field.
    df["_ResolutionHours"] = df["Resolution time (in hrs)"].apply(parse_hms)

    # If a closed/resolved ticket has no usable raw resolution field,
    # derive elapsed Created -> Closed/Resolved where possible.
    missing_res = df["_ResolutionHours"].isna() & df["_ClosedLike"] & df["_Created"].notna() & df["_Closed"].notna()
    df.loc[missing_res, "_ResolutionHours"] = (
        (df.loc[missing_res, "_Closed"] - df.loc[missing_res, "_Created"])
        .dt.total_seconds() / 3600
    )

    df.loc[df["_ResolutionHours"] < 0, "_ResolutionHours"] = np.nan

    # Current age for tickets that are not closed/resolved.
    now = pd.Timestamp.now()
    open_like = ~df["_ClosedLike"]
    df["_CurrentAgeHours"] = np.nan
    age_mask = open_like & df["_Created"].notna()
    df.loc[age_mask, "_CurrentAgeHours"] = (
        (now - df.loc[age_mask, "_Created"]).dt.total_seconds() / 3600
    ).clip(lower=0)

    # 48h SLA buckets for closed/resolved tickets.
    df["_SLA48"] = "Open / Pending"
    valid_closed = df["_ClosedLike"] & df["_ResolutionHours"].notna()
    df.loc[valid_closed & (df["_ResolutionHours"] <= 48), "_SLA48"] = "Closed ≤48h"
    df.loc[valid_closed & (df["_ResolutionHours"] > 48), "_SLA48"] = "Closed >48h"

    # Aging buckets for current open/pending workload.
    df["_AgingBucket"] = "Closed / Resolved"
    df.loc[open_like & (df["_CurrentAgeHours"] <= 24), "_AgingBucket"] = "0–24h"
    df.loc[open_like & (df["_CurrentAgeHours"] > 24) & (df["_CurrentAgeHours"] <= 48), "_AgingBucket"] = "24–48h"
    df.loc[open_like & (df["_CurrentAgeHours"] > 48) & (df["_CurrentAgeHours"] <= 72), "_AgingBucket"] = "48–72h"
    df.loc[open_like & (df["_CurrentAgeHours"] > 72) & (df["_CurrentAgeHours"] <= 168), "_AgingBucket"] = "3–7 days"
    df.loc[open_like & (df["_CurrentAgeHours"] > 168) & (df["_CurrentAgeHours"] <= 720), "_AgingBucket"] = "7–30 days"
    df.loc[open_like & (df["_CurrentAgeHours"] > 720) & (df["_CurrentAgeHours"] <= 2160), "_AgingBucket"] = "30–90 days"
    df.loc[open_like & (df["_CurrentAgeHours"] > 2160), "_AgingBucket"] = ">90 days"

    df["_CreatedDate"] = df["_Created"].dt.date
    df["_Month"] = df["_Created"].dt.strftime("%b %Y")
    df["_MonthSort"] = df["_Created"].dt.to_period("M").astype(str)
    df["_WeekStart"] = df["_Created"].dt.to_period("W-SUN").apply(lambda p: p.start_time if not pd.isna(p) else pd.NaT)
    df["_Week"] = np.where(
        df["_WeekStart"].notna(),
        df["_WeekStart"].dt.strftime("%d %b %Y"),
        ""
    )

    return df

def metrics(data):
    raised = unique_ticket_count(data)
    closed = unique_ticket_count(data[data["_ClosedLike"]])
    open_n = unique_ticket_count(data[~data["_ClosedLike"]])

    closed_valid = data[data["_ClosedLike"] & data["_ResolutionHours"].notna()]
    res = closed_valid["_ResolutionHours"]

    closed_48 = unique_ticket_count(
        data[data["_ClosedLike"] & (data["_ResolutionHours"] <= 48)]
    )
    closed_gt48 = unique_ticket_count(
        data[data["_ClosedLike"] & (data["_ResolutionHours"] > 48)]
    )

    open_age = data.loc[~data["_ClosedLike"], "_CurrentAgeHours"].dropna()
    open_gt48 = int((open_age > 48).sum())
    open_gt90 = int((open_age > 2160).sum())

    return {
        "raised": raised,
        "closed": closed,
        "open": open_n,
        "closed48": closed_48,
        "closedgt48": closed_gt48,
        "closure48pct": safe_pct(closed_48, raised),
        "avg": res.mean() if len(res) else np.nan,
        "max_resolution": res.max() if len(res) else np.nan,
        "p90": res.quantile(0.90) if len(res) else np.nan,
        "p95": res.quantile(0.95) if len(res) else np.nan,
        "p99": res.quantile(0.99) if len(res) else np.nan,
        "open_gt48": open_gt48,
        "open_gt90": open_gt90,
        "max_open_age": open_age.max() if len(open_age) else np.nan,
        "valid_resolution": len(res),
    }

def group_aging_table(data):
    rows = []
    for group, g in data.groupby("_Group", sort=False):
        age = g.loc[~g["_ClosedLike"], "_CurrentAgeHours"].dropna()
        rows.append({
            "Group": group,
            "Tickets": unique_ticket_count(g),
            "Open/Pending": unique_ticket_count(g[~g["_ClosedLike"]]),
            "Open >48h": int((age > 48).sum()),
            "Open >90 days": int((age > 2160).sum()),
            "Max Open Age": format_hms(age.max()) if len(age) else "—",
        })
    return pd.DataFrame(rows).sort_values("Tickets", ascending=False).reset_index(drop=True)

def open_reason_table(data):
    x = data[~data["_ClosedLike"]].copy()
    if x.empty:
        return pd.DataFrame(columns=["Open/Pending Reason","Tickets","% of Open/Pending"])

    x["_Reason"] = clean_text(x["Reason for pendency"])
    x.loc[x["_Reason"].eq(""), "_Reason"] = "Reason not provided"

    out = (
        x.groupby("_Reason")["Ticket ID"].nunique()
        .reset_index(name="Tickets")
        .rename(columns={"_Reason":"Open/Pending Reason"})
        .sort_values("Tickets", ascending=False)
        .reset_index(drop=True)
    )
    total = out["Tickets"].sum()
    out["% of Open/Pending"] = (out["Tickets"] / total * 100).round(1) if total else 0
    return out

def aging_table(data):
    order = ["0–24h","24–48h","48–72h","3–7 days","7–30 days","30–90 days",">90 days"]
    x = data[~data["_ClosedLike"]]
    out = x.groupby("_AgingBucket")["Ticket ID"].nunique().reindex(order, fill_value=0).reset_index()
    out.columns = ["Current Ticket Age","Tickets"]
    return out

def resolution_by_group(data):
    rows=[]
    for group,g in data.groupby("_Group",sort=False):
        r=g.loc[g["_ClosedLike"],"_ResolutionHours"].dropna()
        rows.append({
            "Group":group,
            "Closed/Resolved":unique_ticket_count(g[g["_ClosedLike"]]),
            "Closed ≤48h":int((r<=48).sum()),
            "Closed >48h":int((r>48).sum()),
            "Avg Resolution":format_hms(r.mean()) if len(r) else "—",
            "Max Resolution":format_hms(r.max()) if len(r) else "—",
        })
    return pd.DataFrame(rows).sort_values("Closed/Resolved",ascending=False).reset_index(drop=True)


def issue_breakup_table(data, column, denominator=None):
    x = data.copy()
    if column not in x.columns:
        return pd.DataFrame(columns=[column, "Tickets", "% of Tickets"])

    x["_Issue"] = clean_text(x[column])
    x.loc[x["_Issue"].eq(""), "_Issue"] = "(blank)"

    out = (
        x.groupby("_Issue")["Ticket ID"].nunique()
        .reset_index(name="Tickets")
        .rename(columns={"_Issue": column})
        .sort_values("Tickets", ascending=False)
        .reset_index(drop=True)
    )
    den = int(denominator if denominator is not None else unique_ticket_count(data))
    out["% of Tickets"] = (
        (out["Tickets"] / den * 100).round(1) if den else 0.0
    )
    return out


def category_subcategory_table(data):
    cols = ["Category", "Sub-Category"]
    x = data.copy()
    for c in cols:
        if c not in x.columns:
            x[c] = ""
        x[c] = clean_text(x[c]).replace("", "(blank)")

    out = (
        x.groupby(cols)["Ticket ID"]
        .nunique()
        .reset_index(name="Tickets")
        .sort_values("Tickets", ascending=False)
        .reset_index(drop=True)
    )
    total = unique_ticket_count(data)
    out["% of Tickets"] = (
        (out["Tickets"] / total * 100).round(1) if total else 0.0
    )
    return out


def other_group_pending_table(data):
    """Current tickets whose raw Group is not CD L2, with aging/SLA status, after the dashboard filters are applied."""
    x = data[~data["_Group"].str.casefold().eq("cd l2")].copy()
    if x.empty:
        return pd.DataFrame(columns=[
            "Current Group", "Tickets", "Open / Pending",
            "Open >48h", "Open >90 days", "Max Open Age"
        ])

    rows = []
    for group, g in x.groupby("_Group", sort=False):
        open_g = g[~g["_ClosedLike"]]
        ages = open_g["_CurrentAgeHours"].dropna()
        rows.append({
            "Current Group": group,
            "Tickets": unique_ticket_count(g),
            "Open / Pending": unique_ticket_count(open_g),
            "Open >48h": int((ages > 48).sum()),
            "Open >90 days": int((ages > 2160).sum()),
            "Max Open Age": format_hms(ages.max()) if len(ages) else "—",
        })

    return (
        pd.DataFrame(rows)
        .sort_values(["Open / Pending", "Tickets"], ascending=False)
        .reset_index(drop=True)
    )


def week_breakup_table(data):
    rows = []
    for wk, g in data.groupby("_WeekStart", sort=True):
        mm = metrics(g)
        if pd.isna(wk):
            label = "Unknown"
        else:
            end = wk + pd.Timedelta(days=6)
            label = f"{wk.strftime('%d %b %Y')} - {end.strftime('%d %b %Y')}"
        rows.append({
            "Week": label,
            "Tickets Raised": mm["raised"],
            "Closed / Resolved": mm["closed"],
            "Open / Pending": mm["open"],
            "Closed ≤48h": mm["closed48"],
            "Closed >48h": mm["closedgt48"],
            "48H Closure %": round(mm["closure48pct"], 1),
            "Avg Resolution": format_hms(mm["avg"]),
            "Open >48h": mm["open_gt48"],
            "Open >90 days": mm["open_gt90"],
        })
    return pd.DataFrame(rows)


def excel_bytes(raw, cd):
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.worksheet.table import Table, TableStyleInfo
    from openpyxl.utils import get_column_letter

    m=metrics(cd)
    wb=load_workbook(io.BytesIO())
    # unreachable; create via pandas writer below

    buf=io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summary=pd.DataFrame([
            ["Tickets Raised",m["raised"]],
            ["Closed / Resolved",m["closed"]],
            ["Open / Pending",m["open"]],
            ["Closed ≤48h",m["closed48"]],
            ["Closed >48h",m["closedgt48"]],
            ["48H Closure %",round(m["closure48pct"],1)],
            ["Open / Pending >48h",m["open_gt48"]],
            ["Open / Pending >90 days",m["open_gt90"]],
            ["Average Resolution",format_hms(m["avg"])],
            ["Max Resolution",format_hms(m["max_resolution"])],
            ["90% Percentile Resolution",format_hms(m["p90"])],
            ["95% Percentile Resolution",format_hms(m["p95"])],
            ["99% Percentile Resolution",format_hms(m["p99"])],
            ["Max Current Open Age",format_hms(m["max_open_age"])],
        ],columns=["Metric","Value"])
        summary.to_excel(writer,index=False,sheet_name="Summary")

        cd_out=cd[[
            "Ticket ID","Subject","Status","Priority","Type","Agent","Group",
            "Created time","Closed time","Resolution time (in hrs)",
            "Resolution status","Reason for pendency","Category","Sub-Category",
            "_CurrentAgeHours","_AgingBucket","_SLA48"
        ]].copy()
        cd_out.rename(columns={
            "_CurrentAgeHours":"Current Age Hours",
            "_AgingBucket":"Current Aging Bucket",
            "_SLA48":"48H SLA Bucket"
        },inplace=True)
        cd_out.to_excel(writer,index=False,sheet_name="CD L2 Raw + Analysis")

        aging_table(cd).to_excel(writer,index=False,sheet_name="Ticket Aging")
        open_reason_table(cd).to_excel(writer,index=False,sheet_name="Open Reasons")
        issue_breakup_table(cd, "Category").to_excel(writer,index=False,sheet_name="Category Breakup")
        issue_breakup_table(cd, "Sub-Category").to_excel(writer,index=False,sheet_name="Subcategory Breakup")
        category_subcategory_table(cd).to_excel(writer,index=False,sheet_name="Category + Subcategory")
        other_group_pending_table(raw).to_excel(writer,index=False,sheet_name="Other Groups Pending")
        group_aging_table(raw).to_excel(writer,index=False,sheet_name="All Groups Aging")
        resolution_by_group(raw).to_excel(writer,index=False,sheet_name="All Groups Resolution")

        # Monthly view for CD L2
        monthly=[]
        for key,g in cd.groupby("_MonthSort",sort=True):
            mm=metrics(g)
            monthly.append({
                "Month":g["_Month"].iloc[0],
                "Tickets Raised":mm["raised"],
                "Closed/Resolved":mm["closed"],
                "Open/Pending":mm["open"],
                "Closed ≤48h":mm["closed48"],
                "Closed >48h":mm["closedgt48"],
                "48H Closure %":round(mm["closure48pct"],1),
                "Avg Resolution":format_hms(mm["avg"]),
                "90%":format_hms(mm["p90"]),
                "95%":format_hms(mm["p95"]),
                "99%":format_hms(mm["p99"]),
            })
        pd.DataFrame(monthly).to_excel(writer,index=False,sheet_name="Monthly Trend")
        week_breakup_table(cd).to_excel(writer,index=False,sheet_name="Weekly Trend")

    wb=load_workbook(buf)
    header_fill=PatternFill("solid",fgColor="173A5E")
    white_font=Font(color="FFFFFF",bold=True)
    thin=Side(style="thin",color="D9E2EC")

    for ws in wb.worksheets:
        ws.freeze_panes="A2"
        ws.auto_filter.ref=ws.dimensions
        for cell in ws[1]:
            cell.fill=header_fill
            cell.font=white_font
            cell.alignment=Alignment(horizontal="center",vertical="center")
        for row in ws.iter_rows():
            for c in row:
                c.border=Border(bottom=thin)
                c.alignment=Alignment(vertical="top")
        for col in range(1,ws.max_column+1):
            letter=get_column_letter(col)
            maxlen=0
            for cell in ws[letter][:200]:
                if cell.value is not None:
                    maxlen=max(maxlen,len(str(cell.value)))
            ws.column_dimensions[letter].width=min(max(maxlen+2,12),42)

        # Add an Excel table when there is data.
        if ws.max_row >= 2 and ws.max_column >= 1:
            ref=f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"
            name="Tbl"+re.sub(r"[^A-Za-z0-9]","",ws.title)[:20]
            if name[3:].isdigit():
                name += "X"
            tab=Table(displayName=name,ref=ref)
            tab.tableStyleInfo=TableStyleInfo(name="TableStyleMedium2",showFirstColumn=False,showLastColumn=False,rowStripes=True,rowBorders=True)
            ws.add_table(tab)

    # Conditional formatting for useful SLA/aging columns.
    from openpyxl.formatting.rule import CellIsRule
    red=PatternFill("solid",fgColor="F4CCCC")
    green=PatternFill("solid",fgColor="D9EAD3")
    orange=PatternFill("solid",fgColor="FCE5CD")
    for wsname in ["CD L2 Raw + Analysis","All Groups Aging","Monthly Trend"]:
        ws=wb[wsname]
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value,str):
                    if ">48h" in cell.value or ">90" in cell.value:
                        cell.fill=red
                    if "≤48h" in cell.value:
                        cell.fill=green
    out=io.BytesIO()
    wb.save(out)
    return out.getvalue()

# ============================================================
# HEADER
# ============================================================
st.markdown("""
<div class="hero">
  <h1>SOLV — CD L2 TICKET PERFORMANCE DASHBOARD</h1>
  <p>CD L2 bucket health, 48-hour SLA, ticket aging, resolution performance and workload drivers.</p>
</div>
""", unsafe_allow_html=True)

uploaded=st.file_uploader(
    "Upload the SOLV ticket dump (CSV / Excel)",
    type=["csv","xlsx","xls"],
    help="The dashboard analyses the raw Group field and focuses the main KPIs on Group = CD L2."
)

if uploaded is None:
    st.info("Upload the SOLV ticket dump to start the CD L2 analysis.")
    st.stop()

try:
    if uploaded.name.lower().endswith(".csv"):
        raw=pd.read_csv(uploaded,low_memory=False)
    else:
        raw=pd.read_excel(uploaded)
except Exception as e:
    st.error(f"Could not read the file: {e}")
    st.stop()

missing=[c for c in REQUIRED if c not in raw.columns]
if missing:
    st.error("The file is missing required columns: " + ", ".join(missing))
    st.stop()

df=prepare_data(raw)

# ============================================================
# FILTERS
# ============================================================
st.markdown('<div class="section">FILTERS</div>',unsafe_allow_html=True)

# Month / Week / Day are all based on Created time.
f1,f2,f3,f4,f5=st.columns(5)

months=sorted(df["_MonthSort"].dropna().unique().tolist())
month_labels={"All":"All"}
for x in months:
    month_labels[x]=pd.Period(x).strftime("%b %Y")

with f1:
    selected_month=st.selectbox(
        "Month",
        ["All"]+[month_labels[x] for x in months]
    )

# Week options from the complete raw dump.
week_options=sorted(
    df.loc[df["_WeekStart"].notna(), "_WeekStart"]
    .drop_duplicates()
    .tolist()
)
week_labels=["All"]
week_map={}
for wk in week_options:
    label=(
        f"{wk.strftime('%d %b %Y')} - "
        f"{(wk + pd.Timedelta(days=6)).strftime('%d %b %Y')}"
    )
    week_labels.append(label)
    week_map[label]=wk

with f2:
    selected_week=st.selectbox("Week",week_labels)

# Day options from Created time.
day_values=sorted(
    df.loc[df["_CreatedDate"].notna(), "_CreatedDate"]
    .drop_duplicates()
    .tolist()
)
day_labels=["All"]
day_map={}
for d in day_values:
    label=pd.Timestamp(d).strftime("%d %b %Y")
    day_labels.append(label)
    day_map[label]=d

with f3:
    selected_day=st.selectbox("Day",day_labels)

with f4:
    status_options=["All"]+sorted(
        df["_Status"].dropna().unique().tolist()
    )
    selected_status=st.selectbox("Status",status_options)

with f5:
    category_options=["All"]+sorted(
        clean_text(df["Category"])
        .replace("","(blank)")
        .unique()
        .tolist()
    )
    selected_category=st.selectbox("Category",category_options)

# Apply the date filters to the complete population first.
filtered_all=df.copy()

if selected_month!="All":
    target=[x for x,label in month_labels.items() if label==selected_month]
    if target:
        filtered_all=filtered_all[
            filtered_all["_MonthSort"].eq(target[0])
        ]

if selected_week!="All":
    filtered_all=filtered_all[
        filtered_all["_WeekStart"].eq(week_map[selected_week])
    ]

if selected_day!="All":
    filtered_all=filtered_all[
        filtered_all["_CreatedDate"].eq(day_map[selected_day])
    ]

if selected_status!="All":
    filtered_all=filtered_all[
        filtered_all["_Status"].eq(selected_status)
    ]

if selected_category!="All":
    filtered_all=filtered_all[
        clean_text(filtered_all["Category"])
        .replace("","(blank)")
        .eq(selected_category)
    ]

# Main dashboard population remains CD L2 only.
cd=filtered_all[
    filtered_all["_Group"].str.casefold().eq("cd l2")
].copy()

st.caption(
    f"Showing {unique_ticket_count(cd):,} CD L2 tickets"
    + (f" | Month: {selected_month}" if selected_month!="All" else " | All available months")
    + (f" | Week: {selected_week}" if selected_week!="All" else "")
    + (f" | Day: {selected_day}" if selected_day!="All" else "")
    + (f" | Status: {selected_status}" if selected_status!="All" else "")
    + (f" | Category: {selected_category}" if selected_category!="All" else "")
)

if cd.empty:
    st.warning("No CD L2 tickets match the selected filters.")
    st.stop()

# ============================================================
# SUMMARY
# ============================================================
m=metrics(cd)
st.markdown('<div class="section">SUMMARY — CD L2</div>',unsafe_allow_html=True)

r1=st.columns(4)
with r1[0]: kpi("TICKETS IN CD L2",f"{m['raised']:,}","blue","Total tickets raised in CD L2")
with r1[1]: kpi("CLOSED / RESOLVED",f"{m['closed']:,}","green","Closed or resolved tickets")
with r1[2]: kpi("OPEN / PENDING",f"{m['open']:,}","red","Current unresolved workload")
with r1[3]: kpi("OPEN / PENDING >48H",f"{m['open_gt48']:,}","orange","Current tickets older than 48h")

r2=st.columns(4)
with r2[0]: kpi("CLOSED ≤48H",f"{m['closed48']:,}","green","Resolved within 48 hours")
with r2[1]: kpi("CLOSED >48H",f"{m['closedgt48']:,}","orange","Resolved after 48 hours")
with r2[2]: kpi("48H CLOSURE %",f"{m['closure48pct']:.1f}%","blue","Closed ≤48h ÷ total tickets raised")
with r2[3]: kpi("OPEN / PENDING >90 DAYS",f"{m['open_gt90']:,}","red","Current age from Created time")

r3=st.columns(4)
with r3[0]: kpi("AVG RESOLUTION",format_hms(m["avg"]),"blue","Valid closed/resolved tickets")
with r3[1]: kpi("MAX RESOLUTION",format_hms(m["max_resolution"]),"orange","Highest valid resolution time")
with r3[2]: kpi("MAX CURRENT AGE",format_hms(m["max_open_age"]),"red","Oldest open/pending ticket")
with r3[3]: kpi("VALID RESOLUTION TICKETS",f"{m['valid_resolution']:,}","dark","Used for average and percentiles")

st.caption(
    "48H Closure % is calculated against total CD L2 tickets raised in the selected population. "
    "Resolution-time metrics use valid closed/resolved tickets and the raw 'Resolution time (in hrs)' field."
)

# ============================================================
# PERCENTILES
# ============================================================
st.markdown('<div class="section">RESOLUTION TIME PERCENTILES</div>',unsafe_allow_html=True)
p1,p2,p3=st.columns(3)
with p1: kpi("90% PERCENTILE RESOLUTION HRS",format_hms(m["p90"]),"blue","PERCENTILE.INC-style 90th percentile")
with p2: kpi("95% PERCENTILE RESOLUTION HRS",format_hms(m["p95"]),"orange","PERCENTILE.INC-style 95th percentile")
with p3: kpi("99% PERCENTILE RESOLUTION HRS",format_hms(m["p99"]),"red","PERCENTILE.INC-style 99th percentile")

st.caption(
    "Percentiles are calculated from valid closed/resolved resolution times. "
    "They indicate the resolution-time point below which 90%, 95% or 99% of the valid tickets fall."
)

# ============================================================
# OPEN REASONS
# ============================================================
st.markdown('<div class="section">OPEN / PENDING REASONS</div>',unsafe_allow_html=True)
orows=open_reason_table(cd)
st.dataframe(orows,use_container_width=True,hide_index=True)

# ============================================================
# CATEGORY / SUB-CATEGORY ANALYSIS
# ============================================================
st.markdown('<div class="section">CATEGORY-WISE BREAKUP</div>',unsafe_allow_html=True)
cat_tbl = issue_breakup_table(cd, "Category")
st.dataframe(cat_tbl, use_container_width=True, hide_index=True)

st.markdown('<div class="section">SUB-CATEGORY-WISE BREAKUP</div>',unsafe_allow_html=True)
sub_tbl = issue_breakup_table(cd, "Sub-Category")
st.dataframe(sub_tbl, use_container_width=True, hide_index=True)

st.markdown('<div class="section">CATEGORY + SUB-CATEGORY CONTRIBUTION</div>',unsafe_allow_html=True)
cs_tbl = category_subcategory_table(cd)
st.dataframe(cs_tbl, use_container_width=True, hide_index=True)

# ============================================================
# OTHER GROUP / PENDING VIEW
# ============================================================
st.markdown('<div class="section">CURRENT TICKETS IN OTHER GROUPS</div>',unsafe_allow_html=True)
st.caption(
    "Based only on the raw Group column. CD L2 remains the main dashboard "
    "population. This separate view shows tickets currently sitting in any "
    "group other than CD L2, including open/pending aging."
)
other_tbl = other_group_pending_table(filtered_all)
st.dataframe(other_tbl, use_container_width=True, hide_index=True)

# ============================================================
# TICKET AGING
# ============================================================
st.markdown('<div class="section">CURRENT TICKET AGING</div>',unsafe_allow_html=True)
st.caption("For open/pending tickets, aging is measured from Created time to the current dashboard refresh time.")

ag=aging_table(cd)
st.dataframe(ag,use_container_width=True,hide_index=True)

# ============================================================
# GROUP TICKETING AGING
# ============================================================
st.markdown('<div class="section">GROUP TICKETING AGING — FULL DUMP</div>',unsafe_allow_html=True)
st.caption("This view uses the raw Group field across the complete dump, so you can see where the overall workload sits. Main KPIs above remain CD L2 only.")
ga=group_aging_table(df)
st.dataframe(ga,use_container_width=True,hide_index=True)

# ============================================================
# RESOLUTION BY GROUP
# ============================================================
st.markdown('<div class="section">GROUP RESOLUTION PERFORMANCE — FULL DUMP</div>',unsafe_allow_html=True)
gr=resolution_by_group(df)
st.dataframe(gr,use_container_width=True,hide_index=True)

# ============================================================
# WEEKLY TREND
# ============================================================
st.markdown('<div class="section">WEEK-WISE CD L2 BREAKUP</div>',unsafe_allow_html=True)
weekly_df = week_breakup_table(cd)
st.dataframe(weekly_df, use_container_width=True, hide_index=True)

# ============================================================
# MONTHLY TREND
# ============================================================
st.markdown('<div class="section">MONTH-WISE CD L2 BREAKUP</div>',unsafe_allow_html=True)
monthly=[]
for key,g in cd.groupby("_MonthSort",sort=True):
    mm=metrics(g)
    monthly.append({
        "Month":g["_Month"].iloc[0],
        "Raised":mm["raised"],
        "Closed / Resolved":mm["closed"],
        "Open / Pending":mm["open"],
        "Closed ≤48h":mm["closed48"],
        "Closed >48h":mm["closedgt48"],
        "48H Closure %":round(mm["closure48pct"],1),
        "Avg Resolution":format_hms(mm["avg"]),
        "90%":format_hms(mm["p90"]),
        "95%":format_hms(mm["p95"]),
        "99%":format_hms(mm["p99"]),
    })
monthly_df=pd.DataFrame(monthly)
st.dataframe(monthly_df,use_container_width=True,hide_index=True)

# ============================================================
# DOWNLOAD
# ============================================================
st.markdown('<div class="section">DOWNLOAD REPORT</div>',unsafe_allow_html=True)
st.caption(
    "The Excel report contains CD L2 raw data with analysis columns, "
    "category/sub-category breakups, open reasons, ticket aging, other-group "
    "pending tickets, group aging, group resolution, weekly and monthly trends. "
    "Every main table has Excel filters enabled."
)

try:
    xlsx=excel_bytes(raw,cd)
    st.download_button(
        "⬇️ Download CD L2 Analysis Excel",
        data=xlsx,
        file_name="SOLV_CD_L2_Ticket_Analysis.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=False,
    )
except Exception as e:
    st.error(f"Could not create the Excel report: {e}")
