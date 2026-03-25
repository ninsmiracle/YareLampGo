"""Tests for serial port auto-detection."""

from __future__ import annotations

from unittest.mock import patch

from lampgo.autodetect import _list_serial_ports, detect_ports


def test_list_serial_ports_returns_list():
    """Should return a list (possibly empty if no hardware)."""
    ports = _list_serial_ports()
    assert isinstance(ports, list)


@patch("lampgo.autodetect._list_serial_ports", return_value=[])
def test_detect_no_ports(mock_list):
    result = detect_ports()
    assert result["motor_port"] is None
    assert result["led_port"] is None
    assert len(result["messages"]) > 0
    assert "No serial ports" in result["messages"][0]


@patch("lampgo.autodetect._list_serial_ports", return_value=["/dev/ttyUSB0"])
@patch("lampgo.autodetect._probe_feetech", return_value=False)
@patch("lampgo.autodetect._probe_esp32", return_value=False)
def test_detect_single_port_fallback(mock_esp, mock_fee, mock_list):
    """With one port and no probe success, should assume motor bus."""
    result = detect_ports()
    assert result["motor_port"] == "/dev/ttyUSB0"


@patch("lampgo.autodetect._list_serial_ports", return_value=["/dev/ttyUSB0", "/dev/ttyUSB1"])
@patch("lampgo.autodetect._probe_feetech", side_effect=[True, False])
@patch("lampgo.autodetect._probe_esp32", side_effect=[True])
def test_detect_both_ports(mock_esp, mock_fee, mock_list):
    result = detect_ports()
    assert result["motor_port"] == "/dev/ttyUSB0"
    assert result["led_port"] == "/dev/ttyUSB1"
