from django.utils.translation import gettext, gettext_lazy as _, pgettext
from i18nfield.strings import LazyI18nString
from pretix.base.settings import settings_hierarkey

# Words the shop uses for a seat and a table. They can be set for the whole event and overridden
# per room, e.g. "seat" at a "table" in one room and "stool" at a "bar" in another.
TERMS = {
    "space": _("seat"),
    "spaces": _("seats"),
    "table": _("table"),
    "tables": _("tables"),
}

for _key in ("step_label", "step_text", *(f"term_{k}" for k in TERMS)):
    settings_hierarkey.add_default(f"tables_{_key}", "", LazyI18nString)


def _text(value):
    return str(value or "").strip()


def get_terms(event, room=None):
    """
    Returns the words to use in the current language as ``{"space": …, "Space": …, "spaces": …}``.
    The capitalised keys are meant for the start of a sentence.
    """
    terms = {}
    for key, default in TERMS.items():
        value = (
            (_text(getattr(room, f"term_{key}")) if room else "")
            or _text(event.settings.get(f"tables_term_{key}"))
            or str(default)
        )
        terms[key] = value
        terms[key.capitalize()] = value[:1].upper() + value[1:]
    return terms


def step_label(event):
    return _text(event.settings.tables_step_label) or pgettext(
        "checkoutflow", "Reserve {spaces}").format(**get_terms(event))


def step_text(event):
    return _text(event.settings.tables_step_text) or gettext(
        "You can also reserve {spaces} with your order. This is optional: make your selection or just continue."
    ).format(**get_terms(event))
