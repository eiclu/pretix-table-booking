from django import forms
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from i18nfield.forms import I18nFormField, I18nTextInput
from pretix.base.forms import I18nMarkdownTextarea, SettingsForm

from .models import NumberedRoom, NumberedTable
from .services import remove_room, sync_room
from .terms import TERMS, get_terms

TERM_LABELS = {
    "space": _("Word for a seat"),
    "spaces": _("Word for several seats"),
    "table": _("Word for a table"),
    "tables": _("Word for several tables"),
}


def term_field(key, help_text):
    return I18nFormField(label=TERM_LABELS[key], help_text=help_text, widget=I18nTextInput, required=False,
                         max_length=100)


class WordingSettingsForm(SettingsForm):
    tables_step_label = I18nFormField(
        label=_("Name of the checkout step"),
        help_text=_("Shown in the progress bar of the checkout. Default: “Reserve seats”."),
        widget=I18nTextInput, required=False, max_length=100,
    )
    tables_step_text = I18nFormField(
        label=_("Introduction of the checkout step"),
        help_text=_("Shown above the list of rooms. Default: “You can also reserve seats with your order. This is "
                    "optional: make your selection or just continue.”"),
        widget=I18nMarkdownTextarea, required=False, widget_kwargs={"attrs": {"rows": 3}},
    )
    tables_term_space = term_field("space", _("For example “place”. Default: “seat”."))
    tables_term_spaces = term_field("spaces", _("For example “places”. Default: “seats”."))
    tables_term_table = term_field("table", _("For example “box”. Default: “table”."))
    tables_term_tables = term_field("tables", _("For example “boxes”. Default: “tables”."))


class NumberedRoomForm(forms.Form):
    title = _("Room with numbered tables")

    enabled = forms.BooleanField(
        label=_("Sell this product as a room with numbered tables"),
        help_text=_("The product's price is the price per seat. Buyers pick a table and then their seats in "
                    "a popup. The plugin creates and maintains a quota with the matching size."),
        required=False,
    )
    pairs = forms.BooleanField(
        label=_("Sell in pairs only"),
        help_text=_("Seats 1+2, 3+4, 5+6, … of a table can only be bought together, so no single seat is left "
                    "between two bookings. Requires an even number of seats at every table."),
        required=False,
    )
    map = forms.ImageField(
        label=_("Room map"),
        help_text=_("Shown next to the list of tables in the shop."),
        required=False,
    )
    tables = forms.IntegerField(label=_("Number of tables"), min_value=1, max_value=200, required=False)
    spaces = forms.IntegerField(label=_("Seats per table"), min_value=1, max_value=100, required=False)
    term_space = term_field("space", _("Only for this room. Leave empty to use the wording of the event."))
    term_spaces = term_field("spaces", "")
    term_table = term_field("table", "")
    term_tables = term_field("tables", "")

    def __init__(self, *args, item, **kwargs):
        self.item = item
        self.room = NumberedRoom.objects.filter(item=item).first()
        super().__init__(*args, **kwargs)
        event = item.event

        self.fields["enabled"].initial = bool(self.room)
        for key in TERMS:
            field = self.fields[f"term_{key}"]
            field.widget.enabled_locales = event.settings.locales
            if self.room:
                field.initial = getattr(self.room, f"term_{key}")
        if self.room:
            self.fields["pairs"].initial = self.room.pairs
            self.fields["map"].initial = self.room.map or None
            del self.fields["tables"], self.fields["spaces"]
            tables = list(self.room.tables.all())
            self.fields["enabled"].help_text = format_html(
                '{} <a href="{}">{}</a>',
                _("This room has {tables} tables with {spaces} seats.").format(
                    tables=len(tables), spaces=sum(t.size for t in tables)),
                reverse("plugins:pretix_tables:tables", kwargs={
                    "organizer": event.organizer.slug, "event": event.slug, "item": item.pk,
                }),
                _("Manage tables"),
            )
        else:
            self.fields["tables"].help_text = _("Created as “Table 1”, “Table 2”, … You can rename, add and "
                                                "remove tables afterwards.")

        self.unsupported = None
        if event.has_subevents:
            self.unsupported = _("Rooms with numbered tables are not available for event series.")
        elif event.seating_plan_id:
            self.unsupported = _("Rooms with numbered tables can not be combined with a seating plan.")
        elif item.variations.exists():
            self.unsupported = _("Rooms with numbered tables can not have variations. Use one product per room.")
        if self.unsupported:
            for f in self.fields.values():
                f.disabled = True
            self.fields["enabled"].help_text = self.unsupported

    def clean(self):
        data = super().clean()
        if self.unsupported or not data.get("enabled"):
            return data
        if self.room:
            if data.get("pairs"):
                odd = [t.name for t in self.room.tables.all() if t.size % 2]
                if odd:
                    self.add_error("pairs", _("These tables have an odd number of seats: {tables}").format(
                        tables=", ".join(odd)))
        else:
            for name in ("tables", "spaces"):
                if not data.get(name) and name not in self.errors:
                    self.add_error(name, _("This field is required."))
            if data.get("pairs") and data.get("spaces") and data["spaces"] % 2:
                self.add_error("spaces", _("Pairs need an even number of seats."))
        return data

    def save(self):
        if self.unsupported:
            return
        data = self.cleaned_data
        if not data.get("enabled"):
            if self.room:
                remove_room(self.room)
            return
        room = self.room
        if room is None:
            room = NumberedRoom(item=self.item)
        for key in TERMS:
            setattr(room, f"term_{key}", data.get(f"term_{key}"))
        room.pairs = bool(data.get("pairs"))
        if room.pk is None:
            room.save()
            table = get_terms(self.item.event, room)["Table"]
            NumberedTable.objects.bulk_create([
                NumberedTable(room=room, name=f"{table} {n}", size=data["spaces"], position=n)
                for n in range(1, data["tables"] + 1)
            ])
        if data.get("map") is False:
            if room.map:
                room.map.delete(save=False)
            room.map = None
        elif data.get("map") and data["map"] != room.map:
            room.map = data["map"]
        room.save()
        sync_room(room)
        if not self.item.event.settings.seating_choice:
            self.item.event.settings.seating_choice = True
