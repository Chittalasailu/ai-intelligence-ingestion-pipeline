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


def test_push_to_google_sheets_shares_publicly_when_using_existing_sheet_id():
    # Regression: only the client.create() branch used to call .share() --
    # a spreadsheet supplied via GOOGLE_SHEET_ID (the open_by_key branch,
    # which is what a service account with no Drive quota of its own must
    # use) silently kept whatever privacy its human owner left it at, even
    # though the assignment requires a publicly viewable link either way.
    fake_spreadsheet = MagicMock()
    fake_spreadsheet.worksheets.return_value = [MagicMock(title="Sheet1")]
    fake_client = MagicMock()
    fake_client.open_by_key.return_value = fake_spreadsheet

    with (
        patch("src.export.google_sheets._resolve_credentials", return_value=(MagicMock(), "service_account")),
        patch("gspread.authorize", return_value=fake_client),
    ):
        url, made_public = push_to_google_sheets({}, "unused.json", sheet_id="existing-sheet-id")

    fake_client.open_by_key.assert_called_once_with("existing-sheet-id")
    fake_client.create.assert_not_called()
    fake_spreadsheet.share.assert_called_once_with(None, perm_type="anyone", role="reader")
    assert made_public is True


def test_push_to_google_sheets_continues_when_sharing_is_rejected_by_owner():
    # Regression: some file owners restrict editors (including this service
    # account) from changing sharing settings on a file they don't own --
    # gspread surfaces that as an APIError from .share(). That must not
    # abort the run: the tabs are the primary deliverable and should still
    # get written, with the caller told sharing needs a manual follow-up.
    fake_spreadsheet = MagicMock()
    fake_spreadsheet.worksheets.return_value = [MagicMock(title="Sheet1")]
    fake_spreadsheet.share.side_effect = Exception("APIError: [404]: File not found")
    fake_client = MagicMock()
    fake_client.open_by_key.return_value = fake_spreadsheet

    with (
        patch("src.export.google_sheets._resolve_credentials", return_value=(MagicMock(), "service_account")),
        patch("gspread.authorize", return_value=fake_client),
    ):
        url, made_public = push_to_google_sheets({}, "unused.json", sheet_id="existing-sheet-id")

    assert made_public is False
    assert url == fake_spreadsheet.url
