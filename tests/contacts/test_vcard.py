from app.modules.contacts.services.vcard import parse_vcard, generate_vcard, extract_uid


def test_parse_basic_vcard():
    text = """BEGIN:VCARD
VERSION:3.0
UID:test-uid-123
FN:Alice Smith
N:Smith;Alice;;;
EMAIL;TYPE=WORK:alice@example.com
TEL;TYPE=CELL:+1234567890
ORG:Acme Corp
TITLE:Engineer
NOTE:Test note
END:VCARD"""
    result = parse_vcard(text)
    assert result["fn"] == "Alice Smith"
    assert result["last_name"] == "Smith"
    assert result["first_name"] == "Alice"
    assert result["uid"] == "test-uid-123"
    assert result["email_work"] == "alice@example.com"
    assert result["tel_cell"] == "+1234567890"
    assert result["org"] == "Acme Corp"
    assert result["title"] == "Engineer"
    assert result["note"] == "Test note"


def test_parse_multiple_emails():
    text = """BEGIN:VCARD
VERSION:3.0
FN:Bob
EMAIL;TYPE=WORK:bob@work.com
EMAIL;TYPE=HOME:bob@home.com
END:VCARD"""
    result = parse_vcard(text)
    assert result["email_work"] == "bob@work.com"
    assert result["email_home"] == "bob@home.com"


def test_parse_home_phone():
    text = """BEGIN:VCARD
VERSION:3.0
FN:Carol
TEL;TYPE=HOME:+1111
END:VCARD"""
    result = parse_vcard(text)
    assert result["tel_home"] == "+1111"


def test_parse_untyped_email_fills_work():
    text = """BEGIN:VCARD
VERSION:3.0
FN:Dan
EMAIL:dan@example.com
END:VCARD"""
    result = parse_vcard(text)
    assert result["email_work"] == "dan@example.com"


def test_parse_untyped_phone_fills_work():
    text = """BEGIN:VCARD
VERSION:3.0
FN:Eve
TEL:+2222
END:VCARD"""
    result = parse_vcard(text)
    assert result["tel_work"] == "+2222"


def test_parse_empty():
    assert parse_vcard("") == {}
    assert parse_vcard(None) == {}


def test_generate_vcard():
    data = {
        "fn": "Alice Smith",
        "first_name": "Alice",
        "last_name": "Smith",
        "email_work": "alice@example.com",
        "tel_cell": "+1234",
        "org": "Acme",
        "title": "Eng",
        "note": "A note",
    }
    result = generate_vcard(data, uid="test-uid")
    assert "BEGIN:VCARD" in result
    assert "VERSION:3.0" in result
    assert "UID:test-uid" in result
    assert "FN:Alice Smith" in result
    assert "N:Smith;Alice;;;" in result
    assert "EMAIL;TYPE=WORK:alice@example.com" in result
    assert "TEL;TYPE=CELL:+1234" in result
    assert "ORG:Acme" in result
    assert "TITLE:Eng" in result
    assert "NOTE:A note" in result
    assert "END:VCARD" in result


def test_generate_vcard_auto_fn():
    data = {"first_name": "Bob", "last_name": "Jones"}
    result = generate_vcard(data, uid="uid-1")
    assert "FN:Bob Jones" in result


def test_generate_vcard_auto_uid():
    data = {"fn": "Test"}
    result = generate_vcard(data)
    assert "UID:" in result


def test_roundtrip():
    original_data = {
        "fn": "Round Trip",
        "first_name": "Round",
        "last_name": "Trip",
        "email_work": "rt@example.com",
        "email_home": "rt@home.com",
        "tel_work": "+111",
        "tel_home": "+222",
        "tel_cell": "+333",
        "org": "Org",
        "title": "Title",
        "note": "Note",
    }
    vcard_text = generate_vcard(original_data, uid="rt-uid")
    parsed = parse_vcard(vcard_text)
    assert parsed["fn"] == original_data["fn"]
    assert parsed["first_name"] == original_data["first_name"]
    assert parsed["last_name"] == original_data["last_name"]
    assert parsed["email_work"] == original_data["email_work"]
    assert parsed["email_home"] == original_data["email_home"]
    assert parsed["tel_work"] == original_data["tel_work"]
    assert parsed["tel_home"] == original_data["tel_home"]
    assert parsed["tel_cell"] == original_data["tel_cell"]
    assert parsed["org"] == original_data["org"]
    assert parsed["title"] == original_data["title"]
    assert parsed["note"] == original_data["note"]


def test_extract_uid():
    text = "BEGIN:VCARD\nVERSION:3.0\nUID:my-uid-123\nFN:Test\nEND:VCARD"
    assert extract_uid(text) == "my-uid-123"


def test_extract_uid_none():
    text = "BEGIN:VCARD\nVERSION:3.0\nFN:Test\nEND:VCARD"
    assert extract_uid(text) is None
