from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm


class StyledFormMixin:
    """Attach our CSS classes and mark invalid fields for the inline error slot."""

    default_widget_class = "field__input"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect)):
                continue
            classes = widget.attrs.get("class", "").split()
            if isinstance(widget, forms.Select):
                classes.append("field__select")
            elif isinstance(widget, forms.Textarea):
                classes.append("field__textarea")
            else:
                classes.append(self.default_widget_class)
            widget.attrs["class"] = " ".join(dict.fromkeys(classes))
            if field.required:
                widget.attrs.setdefault("required", "required")

    def add_error(self, field, error):
        super().add_error(field, error)
        if field and field in self.fields:
            widget = self.fields[field].widget
            widget.attrs["aria-invalid"] = "true"
            widget.attrs["class"] = (widget.attrs.get("class", "") + " is-invalid").strip()


class LoginForm(StyledFormMixin, AuthenticationForm):
    username = forms.CharField(
        label="Username",
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username"}),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    error_messages = {
        "invalid_login": "That username and password don't match. Check both and try again.",
        "inactive": "This account has been deactivated. Ask an administrator for help.",
    }


class ForcePasswordChangeForm(StyledFormMixin, SetPasswordForm):
    new_password1 = forms.CharField(
        label="New password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "autofocus": True}),
        help_text="At least 8 characters, and not something obvious.",
    )
    new_password2 = forms.CharField(
        label="Confirm new password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
