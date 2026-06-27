from sqlalchemy.orm import Session

from market_info_layer.analysis.sec_filing_parser import store_generic_filing_events
from market_info_layer.db.database import get_engine, init_db
from market_info_layer.db.models import FilingEvent


def _events_for(tmp_path, text):
    db_url = f"sqlite:///{tmp_path / 'parser.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        store_generic_filing_events(
            session, 1, "ABC", "10-Q", text, "https://www.sec.gov/x", "2026-01-01"
        )
        session.commit()
        events = session.query(FilingEvent).all()
        return [e.event_type for e in events], [e.summary for e in events]


def test_material_weakness_negated_no_changes_not_flagged(tmp_path):
    types, _ = _events_for(
        tmp_path,
        "There were no changes in the Company’s internal control "
        "over financial reporting."
    )
    assert "material_weakness" not in types


def test_material_weakness_did_not_identify_not_flagged(tmp_path):
    types, _ = _events_for(
        tmp_path,
        "Management did not identify any material weakness in internal control "
        "over financial reporting.",
    )
    assert "material_weakness" not in types


def test_third_party_going_concern_not_flagged(tmp_path):
    types, _ = _events_for(tmp_path, "Some customers may be unable to continue as a going concern.")
    assert "going_concern" not in types


def test_toc_risk_factor_heading_not_flagged(tmp_path):
    types, _ = _events_for(tmp_path, "Item 1A. Risk Factors")
    assert "risk_factor_update" not in types


def test_emerging_growth_company_not_performance(tmp_path):
    types, _ = _events_for(
        tmp_path, "Indicate by check mark whether the registrant is an emerging growth company."
    )
    assert "revenue_growth_profitability" not in types


def test_material_weakness_positive_flagged_with_evidence(tmp_path):
    types, summaries = _events_for(
        tmp_path,
        "Management identified a material weakness in internal control over financial reporting.",
    )
    assert "material_weakness" in types
    assert "matched_phrase=" in "\n".join(summaries)
    assert "negation_filter_checked=True" in "\n".join(summaries)


def test_company_going_concern_positive_flagged(tmp_path):
    types, _ = _events_for(
        tmp_path,
        "These conditions raise substantial doubt about "
        "the Company’s ability to continue as a going concern.",
    )
    assert "going_concern" in types


def test_risk_factor_material_changes_positive_flagged(tmp_path):
    types, _ = _events_for(
        tmp_path,
        "There have been material changes to the risk factors previously disclosed "
        "in our Annual Report on Form 10-K.",
    )
    assert "risk_factor_update" in types


def test_material_weakness_internal_control_not_effective_flagged(tmp_path):
    types, _ = _events_for(
        tmp_path,
        ("Management concluded that internal control over financial reporting "
         "was not effective as of year end."),
    )
    assert "material_weakness" in types


def test_material_weakness_audit_risk_assessment_boilerplate_not_flagged(tmp_path):
    types, summaries = _events_for(
        tmp_path,
        ("Our audit included assessing the risk that a material weakness exists "
         "and obtaining reasonable assurance."),
    )
    assert "material_weakness" not in types
    assert "importance=high" not in "\n".join(summaries)


def test_material_weakness_effective_controls_boilerplate_not_flagged(tmp_path):
    types, _ = _events_for(
        tmp_path,
        ("The Company maintained effective internal control over financial reporting "
         "in all material respects."),
    )
    assert "material_weakness" not in types


def test_material_weakness_no_weaknesses_identified_not_flagged(tmp_path):
    types, _ = _events_for(tmp_path, "No material weaknesses were identified during the period.")
    assert "material_weakness" not in types
