import pytest

from market_info_layer.collectors.nasdaq_halts import HaltParseError, parse_halts_html


def test_parse_halts_mocked_html():
    html = """
    <table>
      <tr>
        <th>Issue Symbol</th><th>Halt Time</th><th>Resumption Trade Time</th>
        <th>Reason Code</th><th>Reason</th>
      </tr>
      <tr><td>ABC</td><td>10:00</td><td>10:30</td><td>T1</td><td>News pending</td></tr>
    </table>
    """
    rows = parse_halts_html(html)
    assert rows[0]["ticker"] == "ABC"
    assert rows[0]["reason_code"] == "T1"


def test_parse_halts_clear_error_on_changed_format():
    with pytest.raises(HaltParseError):
        parse_halts_html("<html><body>No table</body></html>")


class _Response:
    text = """
    <table>
      <tr>
        <th>Issue Symbol</th><th>Halt Time</th><th>Resumption Trade Time</th>
        <th>Reason Code</th><th>Reason</th>
      </tr>
      <tr>
        <td>XYZ</td><td>2024-01-01 10:00</td><td>2024-01-01 10:30</td>
        <td>T1</td><td>News pending</td>
      </tr>
    </table>
    """

    def raise_for_status(self):
        return None


def test_collect_halts_prevents_duplicates(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    from market_info_layer.collectors.nasdaq_halts import collect_halts
    from market_info_layer.db.database import get_engine, init_db

    monkeypatch.setattr(
        "market_info_layer.collectors.nasdaq_halts.requests.get",
        lambda url, timeout: _Response(),
    )
    db_url = f"sqlite:///{tmp_path / 'halts.db'}"
    init_db(db_url)
    with Session(get_engine(db_url)) as session:
        assert collect_halts(session) == 1
        assert collect_halts(session) == 0
