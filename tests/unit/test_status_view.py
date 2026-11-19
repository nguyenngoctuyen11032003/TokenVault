

def test_status_tells_employee_to_ask_owner_for_reset() -> None:
    from tokenvault.adapter import status_view

    d = {"connection": "rejected", "last_error": "auth: device already enrolled; ask the owner",
         "device_id": "h:u:abcd1234", "account": "a", "data_dir": "d", "configured": True,
         "team": "Dev", "manager": None, "device_key": "missing", "agent_running": True,
         "outbox": {"queued": 0, "delivered": 0, "waiting": 0}}
    text = status_view.format_text(d)
    assert "Đặt lại thiết bị" in text and "chủ Manager" in text
    d["last_error"] = "auth: enrollment refused"
    assert "Đặt lại thiết bị" not in status_view.format_text(d)
