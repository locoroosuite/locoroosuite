from app.shared.email_validation import is_valid_email


def test_valid_emails():
    assert is_valid_email("user@example.com")
    assert is_valid_email("user.name@example.com")
    assert is_valid_email("user+tag@example.com")
    assert is_valid_email("user-name@sub.example.com")
    assert is_valid_email("a@b.co")
    assert is_valid_email("test@localhost.localdomain")


def test_invalid_emails():
    assert not is_valid_email("")
    assert not is_valid_email("notanemail")
    assert not is_valid_email("@example.com")
    assert not is_valid_email("user@")
    assert not is_valid_email("user@.com")
    assert not is_valid_email("user@example")
    assert not is_valid_email("user@example.")
    assert not is_valid_email("user name@example.com")
    assert not is_valid_email("user@example..com")


def test_none_and_non_string():
    assert not is_valid_email(None)
    assert not is_valid_email(123)
    assert not is_valid_email([])


def test_whitespace_handling():
    assert is_valid_email("  user@example.com  ")
    assert not is_valid_email("   ")
