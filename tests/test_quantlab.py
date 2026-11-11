import numpy as np
import pandas as pd
import pytest

from quantlab import data, validation
from quantlab.backtest import Costs, bar_returns, max_drawdown, net_returns
from quantlab.pipeline import Config, effective_trials, run
from quantlab.report import render
from quantlab.strategies import Indicators, generate_specs, positions


def test_specs_are_unique_and_cover_all_families():
    specs = generate_specs()
    assert len({s.id for s in specs}) == len(specs) > 1000
    assert {s.family for s in specs} == {"trend", "breakout", "meanrev", "rsi", "momentum"}


def test_positions_never_use_future_bars():
    """Positions on a truncated history must match the same bars of the full history."""
    close = data.synthetic(3000, seed=1)["close"].to_numpy()
    cut = 2000
    full, part = Indicators(close), Indicators(close[:cut])
    for spec in generate_specs()[::7]:
        np.testing.assert_array_equal(positions(spec, full)[:cut], positions(spec, part), err_msg=spec.id)


def test_position_earns_the_next_bar_not_the_current_one():
    ret = bar_returns(np.array([100.0, 110.0, 99.0]))
    r, turn = net_returns(ret, np.array([0.0, 1.0, 1.0]), Costs(0, 0, 0))
    assert r == pytest.approx([0.0, 0.0, -0.1])      # bought at 110, not at 100
    assert turn.tolist() == [0.0, 0.0, 1.0]


def test_costs_are_charged_on_every_position_change():
    costs = Costs(spread_bps=2, commission_bps=1, slippage_bps=1)   # 3 bps per side
    r, _ = net_returns(np.zeros(5), np.array([1.0, 1.0, -1.0, 0.0, 0.0]), costs)
    assert r.sum() == pytest.approx(-(1 + 2 + 1) * 3e-4)


def test_max_drawdown():
    assert max_drawdown(np.array([0.1, -0.5, 0.2])) == pytest.approx(0.5)


def test_more_trials_raise_the_luck_benchmark_and_lower_deflated_sharpe():
    assert validation.expected_max_sharpe(1000, 1e-4) > validation.expected_max_sharpe(10, 1e-4) > 0
    r = np.random.default_rng(0).normal(0.0005, 0.01, 5000)
    assert validation.deflated_sharpe(r, 2, 1 / 5000) > validation.deflated_sharpe(r, 5000, 1 / 5000)


def test_effective_trials_discounts_duplicates():
    x = np.random.default_rng(0).normal(size=(40, 200))
    assert effective_trials(np.repeat(x[:1], 40, axis=0)) == pytest.approx(1.0)
    assert effective_trials(x) > 20


def test_walk_forward_picks_the_recent_winner():
    fsum = np.array([[1.0, 1.0, 1.0, 1.0], [-1.0, -1.0, -1.0, 5.0]])
    fsq = np.full((2, 4), 2.0)
    _, picks = validation.walk_forward(fsum, fsq, np.full(4, 10.0), 252, train_folds=2)
    assert [p[1] for p in picks] == [0, 0]


def test_csv_loader_reports_and_removes_bad_rows(tmp_path):
    df = data.synthetic(50).reset_index(names="timestamp")
    df.loc[10, "close"] = np.nan
    df.loc[20, ["high", "low"]] = [1.0, 2.0]
    df = pd.concat([df, df.iloc[[5]]])
    path = tmp_path / "bars.csv"
    df.to_csv(path, index=False)
    out = data.load_csv(str(path))
    assert len(out) == 48 and out.index.is_monotonic_increasing
    assert len(out.attrs["issues"]) >= 3


@pytest.fixture(scope="module")
def no_edge():
    return run(data.synthetic(20_000, seed=1), Config(mc_draws=100))


def test_no_edge_data_validates_nothing(no_edge):
    assert no_edge.funnel["passed_initial_filter"] > 0      # in-sample luck does exist
    assert no_edge.funnel["passed_validation"] == 0         # and the gate rejects it


def test_planted_momentum_edge_is_found_in_the_right_families():
    res = run(data.synthetic(20_000, seed=1, autocorr=0.25), Config(mc_draws=100))
    assert len(res.validated) > 0
    assert set(res.validated["family"]) <= {"momentum", "breakout", "trend"}


def test_report_never_clears_live_deployment(no_edge):
    text = render(no_edge, "test")
    assert "| Live deployment | BLOCKED |" in text
    assert "RESEARCH ONLY" in text
