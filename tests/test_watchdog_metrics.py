"""Tests for the spike-monitor metrics core — rate/volume detection (pure functions)."""
from watchdog.metrics import (
    sum_counter,
    count_matches,
    check_budget,
    parse_count_header,
    check_rate,
)

HOUR = 3600


# --- sum_counter (windowed log-counter rate) --------------------------------

def test_sum_counter_adds_values_across_lines():
    log = "[supervise] relaunched=2 failed=0\n[supervise] relaunched=5 failed=0\n"
    assert sum_counter(log, "relaunched", lines=10) == 7


def test_sum_counter_ignores_other_keys():
    log = "[supervise] relaunched=3 launched=99\n"
    assert sum_counter(log, "relaunched", lines=10) == 3
    assert sum_counter(log, "launched", lines=10) == 99


def test_sum_counter_only_windows_the_last_n_lines():
    log = "relaunched=100\n" + "\n".join("relaunched=1" for _ in range(5))
    # last 5 lines only -> 5, not 105
    assert sum_counter(log, "relaunched", lines=5) == 5


def test_sum_counter_absent_key_is_zero():
    assert sum_counter("nothing here\n", "relaunched", lines=10) == 0


# --- count_matches (process count) ------------------------------------------

def test_count_matches_counts_matching_lines():
    ps = ("dev 100 python3 poller.py --meet 1\n"
          "dev 101 python3 poller.py --meet 2\n"
          "dev 102 python3 supervise_meets.py\n")
    assert count_matches(ps, r"poller\.py") == 2


def test_count_matches_none_is_zero():
    assert count_matches("dev 1 bash\n", r"poller\.py") == 0


# --- check_budget -----------------------------------------------------------

def test_check_budget_below_warn_is_ok():
    assert check_budget("relaunch", 3, warn_at=8, crit_at=20).level == "ok"


def test_check_budget_between_warn_and_crit_is_warn():
    st = check_budget("relaunch", 10, warn_at=8, crit_at=20)
    assert st.level == "warn"
    assert "10" in st.summary


def test_check_budget_at_or_above_crit_is_crit():
    assert check_budget("relaunch", 20, warn_at=8, crit_at=20).level == "crit"


# --- parse_count_header (PostgREST Content-Range) ---------------------------

def test_parse_count_header_reads_total_after_slash():
    assert parse_count_header("0-999/183621") == 183621


def test_parse_count_header_handles_star_range():
    assert parse_count_header("*/12") == 12   # PostgREST uses */N when range unsatisfiable


def test_parse_count_header_unparseable_is_none():
    assert parse_count_header("garbage") is None
    assert parse_count_header(None) is None


# --- check_rate (Supabase rows/hour from delta) -----------------------------

def test_check_rate_under_budget_is_ok():
    st = check_rate("results", current=1000, prev=0, elapsed_s=HOUR,
                    warn_per_hour=20000, crit_per_hour=100000)
    assert st.level == "ok"


def test_check_rate_over_crit_per_hour():
    # +200k rows in 30 min -> 400k/hr -> crit
    st = check_rate("splits", current=400000, prev=200000, elapsed_s=HOUR // 2,
                    warn_per_hour=20000, crit_per_hour=100000)
    assert st.level == "crit"
    assert "splits" in st.name


def test_check_rate_first_run_no_prev_is_ok():
    # no prior reading -> can't compute a rate -> ok (just records this value)
    st = check_rate("results", current=183621, prev=None, elapsed_s=HOUR,
                    warn_per_hour=20000, crit_per_hour=100000)
    assert st.level == "ok"


def test_check_rate_count_reset_does_not_alert():
    # current < prev (table shrank / count reset) -> treat as 0 rate, never alert
    st = check_rate("results", current=10, prev=1000, elapsed_s=HOUR,
                    warn_per_hour=20000, crit_per_hour=100000)
    assert st.level == "ok"


def test_check_rate_zero_elapsed_is_ok():
    st = check_rate("results", current=999999, prev=0, elapsed_s=0,
                    warn_per_hour=20000, crit_per_hour=100000)
    assert st.level == "ok"   # no div-by-zero blowup
