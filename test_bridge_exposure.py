import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BRIDGE_DIR = Path(__file__).resolve().parents[1]
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

import mt5_bridge


class FakeMT5:
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP = 4

    def __init__(self, positions=None, orders=None):
        self.positions = positions or []
        self.orders = orders or []

    def positions_get(self, symbol=None):
        if symbol is None:
            return tuple(self.positions)
        return tuple(p for p in self.positions if p.symbol == symbol)

    def orders_get(self, symbol=None):
        if symbol is None:
            return tuple(self.orders)
        return tuple(o for o in self.orders if o.symbol == symbol)

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=2005.0, ask=2005.2)

    def order_calc_profit(self, order_type, symbol, volume, entry, stop):
        return -abs(entry - stop) * 10.0 * volume

    def last_error(self):
        return (0, "ok")


class BridgeExposureTests(unittest.TestCase):
    def _position(self, **overrides):
        values = dict(
            magic=mt5_bridge.MAGIC, symbol="XAUUSDm", ticket=101,
            sl=1990.0, price_open=2000.0, volume=0.1,
            type=FakeMT5.POSITION_TYPE_BUY,
        )
        values.update(overrides)
        return SimpleNamespace(**values)

    def _pending(self, **overrides):
        values = dict(
            magic=mt5_bridge.MAGIC, symbol="XAUUSDm", ticket=202,
            type=FakeMT5.ORDER_TYPE_SELL_LIMIT, sl=2020.0,
            price_open=2010.0, volume_current=0.2, volume_initial=0.2,
        )
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_aggregate_exposure_includes_position_pending_and_cost_reserve(self):
        fake = FakeMT5(positions=[self._position()], orders=[self._pending()])
        with patch.object(mt5_bridge, "mt5", fake), patch.object(mt5_bridge, "ESTIMATED_ROUND_TRIP_COST_PER_LOT", 2.0):
            result = mt5_bridge._current_bot_stop_risk()
        # Position: 10*10*.1 + .1*2 = 10.2; pending: 10*10*.2 + .2*2 = 20.4.
        self.assertAlmostEqual(result["risk"], 30.6)
        self.assertEqual(len(result["positions"]), 1)
        self.assertEqual(len(result["pendingOrders"]), 1)

    def test_break_even_or_profit_locked_position_stop_is_valid(self):
        fake = FakeMT5(positions=[self._position(sl=2000.0)])
        with patch.object(mt5_bridge, "mt5", fake), patch.object(mt5_bridge, "ESTIMATED_ROUND_TRIP_COST_PER_LOT", 0.0):
            result = mt5_bridge._current_bot_stop_risk()
        self.assertEqual(result["risk"], 0.0)

    def test_stop_crossed_by_market_is_rejected(self):
        fake = FakeMT5(positions=[self._position(sl=2006.0)])
        with patch.object(mt5_bridge, "mt5", fake):
            with self.assertRaises(RuntimeError):
                mt5_bridge._current_bot_stop_risk()

    def test_wrong_side_pending_stop_is_rejected(self):
        fake = FakeMT5(orders=[self._pending(sl=2005.0)])
        with patch.object(mt5_bridge, "mt5", fake):
            with self.assertRaises(RuntimeError):
                mt5_bridge._current_bot_stop_risk()

    def test_unsupported_pending_order_type_fails_closed(self):
        fake = FakeMT5(orders=[self._pending(type=FakeMT5.ORDER_TYPE_BUY_STOP)])
        with patch.object(mt5_bridge, "mt5", fake):
            with self.assertRaises(RuntimeError):
                mt5_bridge._current_bot_stop_risk()

    def test_unmanaged_manual_orders_are_not_counted(self):
        fake = FakeMT5(positions=[self._position(magic=123)], orders=[self._pending(magic=123)])
        with patch.object(mt5_bridge, "mt5", fake):
            result = mt5_bridge._current_bot_stop_risk()
        self.assertEqual(result["risk"], 0.0)
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["pendingOrders"], [])


if __name__ == "__main__":
    unittest.main()
