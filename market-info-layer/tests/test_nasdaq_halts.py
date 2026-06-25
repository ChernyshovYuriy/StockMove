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
