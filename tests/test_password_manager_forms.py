"""Browser form signals for password-manager-safe non-login workflows."""

from support import body, create_dataset, login, request, session_csrf


def test_dataset_and_field_forms_are_excluded_from_password_managers(dataset_browser):
    _, services, admin, client = dataset_browser
    login(client)

    page = body(request(client, "get", "/datasets/new"))
    assert "data-form-type='other'" in page
    assert "data-1p-ignore" in page and "data-lpignore='true'" in page
    assert "data-bwignore='true'" in page
    assert "name='dataset_label'" in page
    assert "id='dataset-name' name='dataset_label' type='text'" in page
    assert "data-key-source" in page
    assert "autocomplete='off'" in page

    dataset = create_dataset(services, admin, "autofill", "Autofill")
    page = body(request(client, "get", f"/datasets/{dataset.key}/fields/new"))
    assert "name='field_label'" in page
    assert "data-key-source" in page
    assert "data-bwignore='true'" in page


def test_login_form_keeps_explicit_password_manager_signals(dataset_browser):
    _, _, _, client = dataset_browser

    page = body(request(client, "get", "/login"))
    assert "data-form-type='login'" in page
    assert "name='email' type='email'" in page
    assert "autocomplete='username'" in page
    assert "autocomplete='current-password'" in page
    assert "data-form-type='username'" in page
    assert "data-form-type='password'" in page
    assert "data-1p-ignore" not in page


def test_admin_created_user_form_is_shielded_and_uses_neutral_names(dataset_browser):
    _, services, admin, client = dataset_browser
    login(client)
    page = body(request(client, "get", "/users"))
    assert "name='invitee_email' type='email' autocomplete='off'" in page
    assert "name='invitee_display_name' autocomplete='off'" in page
    assert "name='invitee_password' type='password' autocomplete='off'" in page
    assert "data-bwignore='true'" in page

    response = request(
        client,
        "post",
        "/users",
        data={
            "invitee_email": "new-person@example.com",
            "invitee_display_name": "New Person",
            "invitee_password": "valid user password",
            "role": "user",
            "csrf_token": session_csrf(client, services),
        },
    )
    assert response.status_code == 302
    assert any(
        user.email == "new-person@example.com" for user in services.users.list_users(admin.id)
    )
