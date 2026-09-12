"""onboard_stock 单元测试"""
import pytest
from unittest.mock import patch, MagicMock


class TestOnboardStock:
    """onboard_stock 基础测试"""

    @patch("src.collector.onboard._step_financial_summary")
    @patch("src.collector.onboard._step_financial_history")
    @patch("src.collector.onboard._step_snapshot")
    def test_onboard_stock_calls_all_steps(self, mock_snap, mock_hist, mock_sum):
        from src.collector.onboard import onboard_stock
        result = onboard_stock("000001")
        assert result["ok"] is True
        assert result["code"] == "000001"
        mock_snap.assert_called_once()
        mock_hist.assert_called_once()
        mock_sum.assert_called_once()

    @patch("src.collector.onboard._step_financial_summary", side_effect=Exception("sum err"))
    @patch("src.collector.onboard._step_financial_history", side_effect=Exception("hist err"))
    @patch("src.collector.onboard._step_snapshot", side_effect=Exception("snap err"))
    def test_onboard_stock_catches_errors(self, mock_snap, mock_hist, mock_sum):
        from src.collector.onboard import onboard_stock
        result = onboard_stock("000002")
        assert result["ok"] is False
        assert len(result["errors"]) == 3
        assert "snapshot" in result["errors"][0]

    @patch("src.collector.onboard.onboard_stock")
    def test_onboard_stock_async_starts_thread(self, mock_fn):
        from src.collector.onboard import onboard_stock_async
        with patch("src.collector.onboard.threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            onboard_stock_async("000003")
            mock_thread.assert_called_once()
            mock_thread.return_value.start.assert_called_once()
