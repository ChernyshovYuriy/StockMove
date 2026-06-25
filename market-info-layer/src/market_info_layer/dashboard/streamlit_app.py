import streamlit as st
from sqlalchemy.orm import Session

from market_info_layer.db.database import get_engine, init_db
from market_info_layer.db.models import DailyNote, Filing, MacroObservation, TradingHalt, Watchlist
from market_info_layer.settings import ROOT_DIR
from market_info_layer.utils.time import utc_now_iso

init_db()
st.title("Market Information Layer")
page = st.sidebar.radio(
    "Section",
    [
        "Watchlist",
        "SEC filings",
        "Macro observations",
        "Trading halts",
        "Daily brief viewer",
        "Manual daily notes entry",
    ],
)
with Session(get_engine()) as session:
    if page == "Watchlist":
        st.dataframe([w.__dict__ for w in session.query(Watchlist).all()])
    elif page == "SEC filings":
        st.dataframe([f.__dict__ for f in session.query(Filing).all()])
    elif page == "Macro observations":
        st.dataframe([m.__dict__ for m in session.query(MacroObservation).all()])
    elif page == "Trading halts":
        st.dataframe([h.__dict__ for h in session.query(TradingHalt).all()])
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
