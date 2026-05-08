"""
dashboard.py — Minimal read-only Streamlit dashboard.
Run: streamlit run dashboard.py
"""

import json
import sqlite3
from datetime import datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import config

st.set_page_config(
    page_title="Job Search Dashboard",
    page_icon="🔍",
    layout="wide",
)


# ── Data loading ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_jobs() -> pd.DataFrame:
    """Load all jobs from DB into a DataFrame."""
    conn = sqlite3.connect(config.DB_PATH)
    df = pd.read_sql_query(
        "SELECT * FROM jobs ORDER BY scraped_at DESC",
        conn,
    )
    conn.close()

    if df.empty:
        return df

    # Parse dates
    for col in ("scraped_at", "posted_at"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # Parse JSON columns
    for col in ("skills", "llm_strengths", "llm_gaps"):
        if col in df.columns:
            df[col] = df[col].apply(
                lambda x: json.loads(x) if isinstance(x, str) and x else []
            )

    df["is_remote"] = df["is_remote"].astype(bool)
    return df


def reload_data() -> None:
    st.cache_data.clear()
    st.rerun()


# ── Sidebar filters ────────────────────────────────────────────────────────────

def render_sidebar(df: pd.DataFrame) -> pd.DataFrame:
    st.sidebar.header("Filters")

    # Platform
    platforms = ["All"] + sorted(df["platform"].dropna().unique().tolist())
    platform = st.sidebar.selectbox("Platform", platforms)

    # Status
    statuses = ["All"] + sorted(df["status"].dropna().unique().tolist())
    status = st.sidebar.selectbox("Status", statuses)

    # Score range
    score_min, score_max = st.sidebar.slider(
        "LLM Score Range", 0, 100, (0, 100)
    )

    # Date range
    today = datetime.utcnow().date()
    date_from = st.sidebar.date_input("Posted From", today - timedelta(days=7))
    date_to = st.sidebar.date_input("Posted To", today)

    # Remote only
    remote_only = st.sidebar.checkbox("Remote only")

    # Apply filters
    filtered = df.copy()
    if platform != "All":
        filtered = filtered[filtered["platform"] == platform]
    if status != "All":
        filtered = filtered[filtered["status"] == status]
    if remote_only:
        filtered = filtered[filtered["is_remote"]]

    score_mask = (
        (filtered["llm_score"].isna()) |
        (
            (filtered["llm_score"] >= score_min) &
            (filtered["llm_score"] <= score_max)
        )
    )
    filtered = filtered[score_mask]

    if "posted_at" in filtered.columns:
        date_mask = (
            filtered["posted_at"].isna() |
            (
                (filtered["posted_at"].dt.date >= date_from) &
                (filtered["posted_at"].dt.date <= date_to)
            )
        )
        filtered = filtered[date_mask]

    return filtered


# ── Stats bar ──────────────────────────────────────────────────────────────────

def render_stats(df: pd.DataFrame) -> None:
    total = len(df)
    week_ago = datetime.utcnow() - timedelta(days=7)
    this_week = (
        df[df["scraped_at"] >= week_ago].shape[0]
        if "scraped_at" in df.columns else 0
    )
    alerted = df["alerted"].sum() if "alerted" in df.columns else 0
    applied = df[df["status"] == "applied"].shape[0] if "status" in df.columns else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Jobs", total)
    c2.metric("This Week", this_week)
    c3.metric("Alerts Sent", int(alerted))
    c4.metric("Applied", applied)


# ── Jobs table ─────────────────────────────────────────────────────────────────

def render_jobs_table(filtered: pd.DataFrame) -> None:
    st.subheader(f"Jobs ({len(filtered)})")

    display_cols = [
        col for col in
        ["title", "company", "location", "platform", "employment_type",
         "match_score", "llm_score", "llm_verdict", "status", "posted_at", "apply_url"]
        if col in filtered.columns
    ]

    display_df = filtered[display_cols].copy()
    if "match_score" in display_df.columns:
        display_df["match_score"] = display_df["match_score"].apply(
            lambda x: f"{x:.2f}" if pd.notna(x) else ""
        )

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "apply_url": st.column_config.LinkColumn("Apply URL"),
            "llm_score": st.column_config.NumberColumn("LLM Score", format="%d"),
        },
    )


# ── Job detail ─────────────────────────────────────────────────────────────────

def render_job_detail(df: pd.DataFrame) -> None:
    st.subheader("Job Detail")

    if df.empty:
        st.info("No jobs match the current filters.")
        return

    job_options = {
        f"{row['title']} @ {row['company']} ({row['platform']})": row["id"]
        for _, row in df.iterrows()
    }
    selected_label = st.selectbox("Select a job", list(job_options.keys()))
    if not selected_label:
        return

    selected_id = job_options[selected_label]
    job = df[df["id"] == selected_id].iloc[0]

    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown(f"### {job['title']}")
        st.markdown(f"**{job['company']}** · {job.get('location', '')} · {job.get('platform', '').capitalize()}")
        if job.get("apply_url"):
            st.markdown(f"[Apply ↗]({job['apply_url']})")
        st.markdown("---")
        st.markdown("**Description:**")
        st.text_area(
            "",
            value=str(job.get("description_clean", ""))[:3000],
            height=300,
            label_visibility="collapsed",
        )

    with col2:
        llm_score = job.get("llm_score")
        if llm_score:
            st.metric("LLM Score", f"{llm_score}/100")
            st.markdown(f"**Verdict:** {job.get('llm_verdict', '').upper()}")

        match_score = job.get("match_score")
        if match_score:
            st.metric("Semantic Match", f"{float(match_score):.2f}")

        one_liner = job.get("llm_one_liner")
        if one_liner:
            st.markdown(f"*{one_liner}*")

        strengths = job.get("llm_strengths") or []
        if isinstance(strengths, str):
            try:
                strengths = json.loads(strengths)
            except Exception:
                strengths = []
        if strengths:
            st.markdown("**Strengths:**")
            for s in strengths:
                st.markdown(f"✅ {s}")

        gaps = job.get("llm_gaps") or []
        if isinstance(gaps, str):
            try:
                gaps = json.loads(gaps)
            except Exception:
                gaps = []
        if gaps:
            st.markdown("**Gaps:**")
            for g in gaps:
                st.markdown(f"⚠️ {g}")

        st.markdown("---")
        st.markdown("**Update Status:**")
        new_status = st.selectbox(
            "Status",
            ["new", "seen", "applied", "rejected", "saved"],
            index=["new", "seen", "applied", "rejected", "saved"].index(
                str(job.get("status", "new"))
            ),
            key=f"status_{selected_id}",
        )
        if st.button("Update", key=f"update_{selected_id}"):
            import db as _db
            _db.update_status(selected_id, new_status)
            st.success(f"Status updated to '{new_status}'")
            reload_data()


# ── Charts ─────────────────────────────────────────────────────────────────────

def render_charts(df: pd.DataFrame) -> None:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Jobs by Platform")
        if not df.empty and "platform" in df.columns:
            platform_counts = (
                df["platform"].value_counts().reset_index()
                .rename(columns={"index": "platform", "platform": "count"})
            )
            chart = (
                alt.Chart(platform_counts)
                .mark_bar()
                .encode(
                    x=alt.X("platform:N", sort="-y", axis=alt.Axis(labelAngle=-30)),
                    y=alt.Y("count:Q"),
                    color=alt.Color("platform:N", legend=None),
                    tooltip=["platform", "count"],
                )
                .properties(height=300)
            )
            st.altair_chart(chart, use_container_width=True)

    with col2:
        st.subheader("Match Score Distribution")
        scored = df.dropna(subset=["match_score"])
        if not scored.empty:
            hist = (
                alt.Chart(scored)
                .mark_bar(opacity=0.8)
                .encode(
                    x=alt.X("match_score:Q", bin=alt.Bin(maxbins=20), title="Match Score"),
                    y=alt.Y("count():Q", title="Count"),
                    tooltip=["count()"],
                )
                .properties(height=300)
            )
            st.altair_chart(hist, use_container_width=True)
        else:
            st.info("No scored jobs yet.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    st.title("🔍 Job Search Dashboard")
    st.caption(f"DB: `{config.DB_PATH}` · Auto-refreshes every 60s")

    if st.button("🔄 Refresh"):
        reload_data()

    df = load_jobs()
    if df.empty:
        st.warning("No jobs in DB yet. Run `python main.py` first.")
        return

    filtered = render_sidebar(df)

    render_stats(df)
    st.markdown("---")
    render_charts(filtered)
    st.markdown("---")
    render_jobs_table(filtered)
    st.markdown("---")
    render_job_detail(filtered)


if __name__ == "__main__":
    main()
