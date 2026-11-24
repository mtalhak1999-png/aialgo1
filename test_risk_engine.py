import sys
import unittest
from pathlib import Path

# Make tests runnable both from the project root and from bridge/.
BRIDGE_DIR = Path(__file__).resolve().parents[1]
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

from risk_engine import (
    ensure_xauusd_symbol, calculate_atr, size_equal_burst, floor_to_step,
    purged_expanding_walk_forward_splits, update_equity_anchors, margin_level_is_safe, capped_compound_bonus,
)


class RiskEngineTests(unittest.TestCase):
    def test_allows_xauusd_broker_suffixes(self):
        self.assertEqual(ensure_xauusd_symbol("XAUUSDm"), "XAUUSDm")
        self.assertEqual(ensure_xauusd_symbol("xauusd.pro"), "xauusd.pro")

    def test_rejects_other_instruments(self):
        with self.assertRaises(ValueError):
            ensure_xauusd_symbol("EURUSD")

    def test_atr_uses_completed_series(self):
        bars = []
        price = 2000.0
        for i in range(16):
            bars.append({"open": price, "high": price + 2, "low": price - 1, "close": price + 0.5})
            price += 0.5
        atr = calculate_atr(bars, 14)
        self.assertGreater(atr, 0)

    def test_risk_sized_ten_order_burst_stays_under_budget(self):
        def fake_order_calc_profit(order_type, symbol, volume, entry, stop):
            return -abs(entry - stop) * 1 * volume
        result = size_equal_burst(
            order_calc_profit=fake_order_calc_profit,
            order_type=0, symbol="XAUUSDm", entry_price=2000.0, stop_price=1990.0,
            equity=10_000.0, risk_pct=0.0035, order_count=10,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
        )
        self.assertAlmostEqual(round(result["volumePerOrder"] / 0.01) * 0.01, result["volumePerOrder"])
        self.assertLessEqual(result["estimatedStopLoss"], 35.0 + 1e-8)
        self.assertEqual(result["aggregateVolume"], result["volumePerOrder"] * 10)

    def test_round_trip_cost_reserve_reduces_or_preserves_volume(self):
        def fake_profit(order_type, symbol, volume, entry, stop):
            return -abs(entry - stop) * 0.1 * volume

        base = size_equal_burst(
            order_calc_profit=fake_profit, order_type=0, symbol="XAUUSD",
            entry_price=2000, stop_price=1990, equity=10000, risk_pct=0.0035,
            order_count=10, volume_min=0.01, volume_max=100, volume_step=0.01,
            estimated_cost_per_lot=0.0,
        )
        with_costs = size_equal_burst(
            order_calc_profit=fake_profit, order_type=0, symbol="XAUUSD",
            entry_price=2000, stop_price=1990, equity=10000, risk_pct=0.0035,
            order_count=10, volume_min=0.01, volume_max=100, volume_step=0.01,
            estimated_cost_per_lot=5.0,
        )
        self.assertLessEqual(with_costs["volumePerOrder"], base["volumePerOrder"])

    def test_rejects_when_broker_minimum_would_break_risk_cap(self):
        def fake_order_calc_profit(order_type, symbol, volume, entry, stop):
            return -abs(entry - stop) * 1000 * volume
        with self.assertRaises(ValueError):
            size_equal_burst(
                order_calc_profit=fake_order_calc_profit,
                order_type=0, symbol="XAUUSDm", entry_price=2000.0, stop_price=1990.0,
                equity=100.0, risk_pct=0.0035, order_count=10,
                volume_min=0.01, volume_max=100.0, volume_step=0.01,
            )

    def test_floor_to_step_never_rounds_up(self):
        self.assertAlmostEqual(floor_to_step(0.029999, 0.01), 0.02)

    def test_daily_roll_preserves_account_peak_equity(self):
        state = {"dayKey": "2026-11-23", "dayStartEquity": 10000.0, "peakEquity": 12000.0}
        rolled = update_equity_anchors(state, 10500.0, "2026-11-24")
        self.assertEqual(rolled["dayStartEquity"], 10500.0)
        self.assertEqual(rolled["peakEquity"], 12000.0)
        higher = update_equity_anchors(rolled, 12500.0, "2026-11-24")
        self.assertEqual(higher["peakEquity"], 12500.0)

    def test_compounding_uses_only_uncommitted_realized_profit_and_caps_bonus(self):
        first = capped_compound_bonus(
            equity=10_000, realized_net_profit=40, profit_share=0.25,
            already_committed=8, maximum_bonus_pct=0.0005,
        )
        self.assertEqual(first["availablePool"], 2.0)
        self.assertEqual(first["bonusCash"], 2.0)
        second = capped_compound_bonus(
            equity=10_000, realized_net_profit=40, profit_share=0.25,
            already_committed=10, maximum_bonus_pct=0.0005,
        )
        self.assertEqual(second["bonusCash"], 0.0)

    def test_compounding_bonus_respects_equity_based_cap(self):
        result = capped_compound_bonus(
            equity=10_000, realized_net_profit=1_000, profit_share=0.25,
            already_committed=0, maximum_bonus_pct=0.0005,
        )
        self.assertEqual(result["bonusCash"], 5.0)

    def test_margin_level_unknown_fails_when_margin_is_used(self):
        self.assertFalse(margin_level_is_safe(100.0, 0.0, 400.0))
        self.assertFalse(margin_level_is_safe(100.0, 300.0, 400.0))
        self.assertTrue(margin_level_is_safe(100.0, 500.0, 400.0))
        self.assertTrue(margin_level_is_safe(0.0, 0.0, 400.0))

    def test_walk_forward_folds_purge_overlapping_future_labels(self):
        origins = list(range(500))
        label_ends = [value + 5 for value in origins]
        folds = purged_expanding_walk_forward_splits(
            origins, label_ends, min_train_samples=100, min_validation_samples=30,
        )
        self.assertEqual(len(folds), 3)
        for train_indices, validation_indices in folds:
            validation_start = min(origins[index] for index in validation_indices)
            self.assertTrue(all(label_ends[index] < validation_start for index in train_indices))
            self.assertTrue(all(origins[index] >= validation_start for index in validation_indices))


if __name__ == "__main__":
    unittest.main()
