from auth import login


def test_login_ok():
    assert login("ada@example.com", "hunter2") == "ada@example.com"


def test_login_unknown_user():
    # Fails on purpose: login() reads user["password_hash"] when get_user returned None.
    assert login("nobody@example.com", "x") is None
