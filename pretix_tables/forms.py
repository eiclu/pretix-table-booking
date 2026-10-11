from django import forms
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import NumberedRoom, NumberedTable
from .services import remove_room, sync_room


class NumberedRoomForm(forms.Form):
    title = _("Room with numbered tables")

    enabled = forms.BooleanField(
        label=_("Sell this product as a room with numbered tables"),
        help_text=_("The product's price is the price per space. Buyers pick a table and then their spaces in "
                    "a popup. The plugin creates and maintains a quota with the matching size."),
        required=False,
    )
    pairs = forms.BooleanField(
        label=_("Sell in pairs only"),
        help_text=_("Spaces 1+2, 3+4, 5+6, … of a table can only be bought together, so no single space is left "
                    "between two bookings. Requires an even number of spaces at every table."),
        required=False,
    )
    map = forms.ImageField(
        label=_("Room map"),
        help_text=_("Shown next to the list of tables in the shop."),
        required=False,
    )
    tables = forms.IntegerField(label=_("Number of tables"), min_value=1, max_value=200, required=False)
    spaces = forms.IntegerField(label=_("Spaces per table"), min_value=1, max_value=100, required=False)

    def __init__(self, *args, item, **kwargs):
        self.item = item
        self.room = NumberedRoom.objects.filter(item=item).first()
        super().__init__(*args, **kwargs)
        event = item.event

        self.fields["enabled"].initial = bool(self.room)
        if self.room:
            self.fields["pairs"].initial = self.room.pairs
            self.fields["map"].initial = self.room.map or None
            del self.fields["tables"], self.fields["spaces"]
            tables = list(self.room.tables.all())
            self.fields["enabled"].help_text = format_html(
                '{} <a href="{}">{}</a>',
                _("This room has {tables} tables with {spaces} spaces.").format(
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
                    self.add_error("pairs", _("These tables have an odd number of spaces: {tables}").format(
                        tables=", ".join(odd)))
        else:
            for name in ("tables", "spaces"):
                if not data.get(name) and name not in self.errors:
                    self.add_error(name, _("This field is required."))
            if data.get("pairs") and data.get("spaces") and data["spaces"] % 2:
                self.add_error("spaces", _("Pairs need an even number of spaces."))
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
            room = NumberedRoom.objects.create(item=self.item, pairs=bool(data.get("pairs")))
            NumberedTable.objects.bulk_create([
                NumberedTable(room=room, name=str(_("Table {number}").format(number=n)), size=data["spaces"],
                              position=n)
                for n in range(1, data["tables"] + 1)
            ])
        room.pairs = bool(data.get("pairs"))
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
