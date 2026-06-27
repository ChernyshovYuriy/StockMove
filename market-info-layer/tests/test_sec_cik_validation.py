from market_info_layer.collectors.sec_edgar import validate_watchlist_ciks

COMPANY_TICKERS = {
    "0": {"ticker": "CEG", "cik_str": 1868275, "title": "Constellation Energy Corp"},
    "1": {"ticker": "LULU", "cik_str": 1397187, "title": "lululemon athletica inc."},
}

def test_ceg_and_lulu_correct_ciks_pass():
    items = [{"ticker": "CEG", "cik": "1868275"}, {"ticker": "LULU", "cik": "1397187"}]
    assert validate_watchlist_ciks(items, COMPANY_TICKERS) == []

def test_old_ceg_and_lulu_ciks_fail():
    items = [{"ticker": "CEG", "cik": "1894454"}, {"ticker": "LULU", "cik": "1397856"}]
    errors = validate_watchlist_ciks(items, COMPANY_TICKERS)
    assert "CEG configured CIK 1894454" in errors[0]
    assert "LULU configured CIK 1397856" in errors[1]
