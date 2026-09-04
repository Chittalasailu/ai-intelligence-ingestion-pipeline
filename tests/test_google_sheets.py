from unittest.mock import MagicMock, patch

from src.export.google_sheets import SheetsNotConfigured, _resolve_credentials, push_to_google_sheets


def test_resolve_credentials_none_available_returns_none_none(tmp_path):
    missing_path = str(tmp_path / "does-not-exist.json")
    with patch("google.auth.default", side_effect=Exception("no ADC configured")):
        creds, method = _resolve_credentials(missing_path)
    assert creds is None
    assert method is None


def test_resolve_credentials_prefers_service_account_file_when_present(tmp_path):
    # A real, minimal service-account key shape (fake key material — only
    # used to prove Credentials.from_service_account_file is reached).
    key_path = tmp_path / "service-account.json"
    key_path.write_text(
        """{
        "type": "service_account",
        "project_id": "test-project",
        "private_key_id": "abc",
        "private_key": "-----BEGIN PRIVATE KEY-----\\nMIIBVgIBADANBgkqhkiG9w0BAQEFAASCAT8wggE7AgEAAkEAvY9wp\\n-----END PRIVATE KEY-----\\n",
        "client_email": "test@test-project.iam.gserviceaccount.com",
        "client_id": "123",
        "token_uri": "https://oauth2.googleapis.com/token"
        }""",
        encoding="utf-8",
    )
    with patch("google.oauth2.service_account.Credentials.from_service_account_file") as mock_from_file:
        mock_from_file.return_value = MagicMock()
        creds, method = _resolve_credentials(str(key_path))
    assert method == "service_account"
    assert creds is not None
    mock_from_file.assert_called_once()


def test_resolve_credentials_falls_back_to_adc_when_no_file(tmp_path):
    missing_path = str(tmp_path / "does-not-exist.json")
    fake_creds = MagicMock()
    with patch("google.auth.default", return_value=(fake_creds, "some-project")) as mock_default:
        creds, method = _resolve_credentials(missing_path)
    assert method == "application_default"
    assert creds is fake_creds
    mock_default.assert_called_once()


def test_push_to_google_sheets_raises_not_configured_when_nothing_available(tmp_path):
    missing_path = str(tmp_path / "does-not-exist.json")
    with patch("google.auth.default", side_effect=Exception("no ADC configured")):
        try:
            push_to_google_sheets({}, missing_path)
            assert False, "expected SheetsNotConfigured"
        except SheetsNotConfigured as e:
            assert "GOOGLE_SHEETS_SETUP.md" in str(e)
