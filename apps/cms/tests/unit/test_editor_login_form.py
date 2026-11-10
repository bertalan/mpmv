"""CMP.9 (WCAG 2.2 SC 3.3.8): the editors' (Wagtail) login form lets password managers fill it."""

from apps.cms.admin_forms import EditorLoginForm


def test_the_editor_login_form_lets_password_managers_fill_it() -> None:
    form = EditorLoginForm()
    assert form.fields["username"].widget.attrs["autocomplete"] == "username"
    password = form.fields["password"].widget.attrs
    assert password["autocomplete"] == "current-password"
    assert "data-password-toggle" in password
