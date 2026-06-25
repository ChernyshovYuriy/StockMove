import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from market_info_layer.collectors.fred_macro import latest_macro_values
from market_info_layer.dashboard.dataframes import dashboard_rows, price_summary_rows
from market_info_layer.db.database import get_engine, init_db
from market_info_layer.db.models import (
    DailyNote,
    Filing,
    FilingEvent,
    InsiderTransaction,
    MacroObservation,
    Price,
    TradingHalt,
    Watchlist,
)
from market_info_layer.settings import ROOT_DIR
from market_info_layer.utils.time import utc_now_iso

init_db()


def _link_config(label: str = "SEC source") -> dict:
    return {"source_url": st.column_config.LinkColumn(label)}


IMPORTANCE_ORDER = {"high": 0, "medium": 1, "low": 2, "unknown": 3}


st.title("Market Information Layer")
page = st.sidebar.radio(
    "Section",
    [
        "Watchlist",
        "SEC filings",
        "Insider transactions",
        "Filing events",
        "Macro observations",
        "Prices",
        "Trading halts",
        "Daily brief viewer",
        "Manual daily notes entry",
    ],
)
with Session(get_engine()) as session:
    if page == "Watchlist":
        st.dataframe(dashboard_rows(session, Watchlist))
    elif page == "SEC filings":
        rows = dashboard_rows(session, Filing)
        df = pd.DataFrame(rows)
        if not df.empty:
            ticker = st.text_input("Ticker filter").upper().strip()
            form_type = st.text_input("Form type filter").upper().strip()
            show_form4 = st.checkbox("Show Form 4 insider filings", value=False)
            processed = st.selectbox("Processed status", ["All", "Processed", "Unprocessed"])
            date_range = st.date_input("Filing date range", value=[])
            start = date_range[0] if len(date_range) >= 1 else None
            end = date_range[1] if len(date_range) >= 2 else None
            if ticker:
                df = df[df["ticker"] == ticker]
            if form_type:
                df = df[df["form_type"] == form_type]
            if not show_form4:
                df = df[df["form_type"] != "4"]
            if processed != "All":
                df = df[df["processed"] == (processed == "Processed")]
            if start:
                df = df[df["filing_date"] >= start.isoformat()]
            if end:
                df = df[df["filing_date"] <= end.isoformat()]
            useful = [
                "ticker",
                "form_type",
                "filing_date",
                "report_date",
                "accession_number",
                "processed",
                "filing_url",
            ]
            st.dataframe(
                df[[c for c in useful if c in df.columns]],
                column_config={"filing_url": st.column_config.LinkColumn("SEC source")},
            )
        else:
            st.info("No SEC filings collected yet.")
    elif page == "Insider transactions":
        rows = dashboard_rows(session, InsiderTransaction)
        df = pd.DataFrame(rows)
        columns = [
            "ticker",
            "owner_name",
            "owner_role",
            "transaction_date",
            "transaction_type",
            "transaction_code",
            "shares",
            "price",
            "direct_or_indirect",
            "shares_owned_after",
            "source_url",
            "importance",
        ]
        if df.empty:
            st.info("No insider transactions parsed yet.")
        else:
            ticker = st.text_input("Ticker filter").upper().strip()
            owner = st.text_input("Owner filter").strip().casefold()
            transaction_types = sorted(t for t in df["transaction_type"].dropna().unique())
            selected_types = st.multiselect("Transaction type", transaction_types)
            importance_values = ["high", "medium", "low", "unknown"]
            selected_importance = st.multiselect("Importance", importance_values)
            if ticker:
                df = df[df["ticker"] == ticker]
            if owner:
                df = df[df["owner_name"].fillna("").str.casefold().str.contains(owner)]
            if selected_types:
                df = df[df["transaction_type"].isin(selected_types)]
            if selected_importance:
                df = df[df["importance"].isin(selected_importance)]
            df = df.sort_values(by="transaction_date", ascending=False, na_position="last")
            st.dataframe(
                df[[c for c in columns if c in df.columns]],
                column_config=_link_config(),
            )
    elif page == "Filing events":
        rows = dashboard_rows(session, FilingEvent)
        df = pd.DataFrame(rows)
        if not df.empty:
            df["_importance_rank"] = df["importance"].map(IMPORTANCE_ORDER).fillna(4)
            df = df.sort_values(
                by=["event_date", "_importance_rank"],
                ascending=[False, True],
                na_position="last",
            )
        columns = [
            "ticker",
            "form_type",
            "event_date",
            "event_type",
            "summary",
            "importance",
            "needs_human_review",
            "source_url",
        ]
        if df.empty:
            st.info("No parsed filing events yet.")
        else:
            view = st.radio("View", ["Cards", "Table"], horizontal=True)
            if view == "Table":
                table_columns = [
                    "ticker",
                    "form_type",
                    "event_date",
                    "event_type",
                    "sec_item",
                    "headline",
                    "summary",
                    "importance",
                    "needs_human_review",
                    "source_url",
                ]
                st.dataframe(
                    df[[c for c in table_columns if c in df.columns]],
                    column_config=_link_config(),
                    hide_index=True,
                )
            else:
                for row in df[[c for c in columns if c in df.columns]].to_dict("records"):
                    importance = (row.get("importance") or "unknown").upper()
                    review = "Needs review" if row.get("needs_human_review") else "Parsed"
                    st.markdown(
                        f"**{row.get('ticker', '')}** · {row.get('event_date') or 'No date'} · "
                        f"{row.get('form_type', '')} · **{importance}** · {review}"
                    )
                    st.markdown(f"{row.get('event_type') or 'Filing event'}")
                    if row.get("summary"):
                        st.write(row["summary"])
                    if row.get("source_url"):
                        st.link_button("SEC source", row["source_url"])
                    st.divider()
    elif page == "Macro observations":
        st.subheader("Latest values by configured series")
        latest_df = pd.DataFrame(latest_macro_values(session))
        st.dataframe(latest_df, hide_index=True)
        st.subheader("Historical observations")
        rows = dashboard_rows(session, MacroObservation)
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values(by=["series_id", "observation_date"], ascending=[True, False])
        st.dataframe(df, hide_index=True)
    elif page == "Prices":
        rows = dashboard_rows(session, Price)
        df = pd.DataFrame(rows)
        if df.empty:
            st.info("No prices collected yet.")
        else:
            ticker = st.text_input("Ticker filter").upper().strip()
            if ticker:
                df = df[df["ticker"] == ticker]
            df = df.sort_values(by=["ticker", "price_date"], ascending=[True, False])
            st.subheader("Latest complete price by ticker")
            st.dataframe(
                pd.DataFrame(price_summary_rows(session)),
                hide_index=True,
            )
            st.subheader("Close price chart")
            chart_df = df.sort_values("price_date")
            if ticker:
                st.line_chart(chart_df, x="price_date", y="close")
            else:
                st.line_chart(chart_df, x="price_date", y="close", color="ticker")
            st.subheader("Volume chart")
            if ticker:
                st.bar_chart(chart_df, x="price_date", y="volume")
            else:
                st.bar_chart(chart_df, x="price_date", y="volume", color="ticker")
            st.subheader("Historical OHLCV")
            st.dataframe(
                df[
                    [
                        "ticker",
                        "price_date",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "is_complete",
                        "source",
                    ]
                ],
                hide_index=True,
            )
    elif page == "Trading halts":
        rows = dashboard_rows(session, TradingHalt)
        df = pd.DataFrame(rows)
        if not df.empty:
            ticker = st.text_input("Ticker filter").upper().strip()
            if ticker:
                df = df[df["ticker"] == ticker]
            df = df.sort_values(by=["halt_datetime", "collected_at"], ascending=[False, False])
            st.dataframe(df, hide_index=True)
        else:
            st.info("No trading halts collected yet.")
    elif page == "Daily brief viewer":
        files = (
            sorted((ROOT_DIR / "reports" / "daily").glob("*.md"))
            if (ROOT_DIR / "reports" / "daily").exists()
            else []
        )
        chosen = st.selectbox("Brief", files, format_func=lambda p: p.name) if files else None
        if chosen:
            st.markdown(chosen.read_text())
    else:
        note_date = st.date_input("Note date")
        ticker = st.text_input("Ticker")
        observed_move = st.text_area("Observed move")
        suspected_reason = st.text_area("Suspected reason")
        evidence = st.text_area("Evidence")
        confidence = st.slider("Confidence", 1, 5, 3)
        lesson = st.text_area("Lesson")
        if st.button("Save note"):
            session.add(
                DailyNote(
                    note_date=note_date.isoformat(),
                    ticker=ticker.upper() or None,
                    observed_move=observed_move,
                    suspected_reason=suspected_reason,
                    evidence=evidence,
                    confidence=confidence,
                    lesson=lesson,
                    created_at=utc_now_iso(),
                )
            )
            session.commit()
            st.success("Saved")
