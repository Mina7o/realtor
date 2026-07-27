from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

COALESCE_MV = "COALESCE(t.mkt_val_total, mp.amt_totalvalue, up.fmv_total, yp.apr_tot_val, a.assessed_value, l.zestimate)"

# Prometheus-style counters for /metrics endpoint
request_count = 0
request_latency_sum = 0.0


def get_deal_tier(diff_pct):
    if diff_pct is None:
        return None
    if diff_pct < 0:
        return "undervalued"
    if diff_pct <= 5:
        return "at_value"
    if diff_pct <= 20:
        return "premium"
    return "overpriced"
