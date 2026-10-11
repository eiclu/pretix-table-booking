from django.contrib import messages
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import get_template
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.translation import gettext as _
from django.views.generic import TemplateView
from pretix.base.models import Quota
from pretix.base.templatetags.money import money_filter
from pretix.control.permissions import EventPermissionRequiredMixin
from pretix.multidomain.urlreverse import eventreverse
from pretix.presale.productlist import prepare_item_list_for_shop
from pretix.presale.views.cart import get_or_create_cart_id

from .models import NumberedRoom, NumberedTable, parse_numbers
from .services import (
    BLOCKED,
    FREE,
    HELD,
    MINE,
    SOLD,
    TAKEN,
    count_free,
    highest_taken,
    reservations,
    space_states,
    sync_room,
    table_states,
)

STATE_CHARS = {FREE: "f", MINE: "m", HELD: "h", SOLD: "s", BLOCKED: "x"}


def _compact(table, states):
    """One character per space, e.g. ``ffssffhh``. Keeps the page small for rooms with many tables."""
    return "".join(STATE_CHARS[s] for s in table_states(table, states))


def _memberships(event, request):
    customer = getattr(request, "customer", None)
    return customer.usable_memberships(for_event=event, testmode=event.testmode) if customer else None


def build_block(event, request, items):
    """
    Builds the template context for a list of rooms. ``items`` must come from
    ``prepare_item_list_for_shop``. Returns ``None`` if there is nothing to show.
    """
    by_item = {
        r.item_id: r
        for r in NumberedRoom.objects.filter(item__event=event).select_related("item").prefetch_related("tables")
    }
    states = space_states(event, get_or_create_cart_id(request, create=False))
    net = event.settings.display_net_prices

    rooms = []
    config_rooms = {}
    for item in items:
        room = by_item.get(item.pk)
        tables = list(room.tables.all()) if room and not item.has_variations else []
        if not tables:
            continue
        on_sale = item.cached_availability[0] == Quota.AVAILABILITY_OK
        price = money_filter(item.display_price.net if net else item.display_price.gross, event.currency)
        config_tables = []
        for table in tables:
            compact = _compact(table, states)
            config_tables.append({
                "id": table.pk, "name": table.name,
                "s": compact if on_sale else compact.replace("f", "s"),
            })
        rooms.append({
            "room": room,
            "item": item,
            "price": price,
            "tables": len(tables),
            "size": sum(t.for_sale for t in tables),
            "free": sum(count_free(t, states) for t in tables) if on_sale else 0,
        })
        config_rooms[str(room.pk)] = {
            "name": str(item.name), "field": room.field_name, "pairs": room.pairs, "price": price,
            "tables": config_tables,
        }
    if not rooms:
        return None

    config = {
        "statusUrl": eventreverse(event, "plugins:pretix_tables:status"),
        "rooms": config_rooms,
        "strings": {
            "tableFree": _("%f of %t free"),
            "roomFree": _("%f of %t spaces free"),
            "soldOut": _("Sold out"),
            "selected": _("Selected: %s"),
            "inCart": _("In cart: %s"),
            "shown": _("%a of %b tables match"),
            "noTables": _("No table in this room has that many free spaces."),
            "summaryLine": _("%r, %t: spaces %s"),
            "sub": _("Choose your spaces. %s per space."),
            "subPairs": _("Choose your spaces. %s per space. Spaces are sold in pairs."),
            "none": _("No space selected"),
            "countOne": _("1 space selected"),
            "countMany": _("%d spaces selected"),
            "gone": _("Space %s was just taken by someone else and has been removed from your selection."),
            "space": _("Space %s"),
            "clear": _("Clear selection"),
            "states": {
                "f": _("free"), "sel": _("selected"), "m": _("in cart"), "h": _("reserved"), "s": _("sold"),
                "x": _("not available"),
            },
        },
    }
    return {"event": event, "rooms": rooms, "config": config}


def render_shop_block(event, request):
    """Renders the rooms for the shop front page. Returns '' if there is nothing to show."""
    if event.has_subevents or not event.presale_is_running:
        return ""
    if not NumberedRoom.objects.filter(item__event=event).exists():
        return ""
    items, _display = prepare_item_list_for_shop(
        event, channel=request.sales_channel, require_seat=True, memberships=_memberships(event, request),
    )
    ctx = build_block(event, request, items)
    if not ctx:
        return ""
    return get_template("pretix_tables/shop.html").render(ctx, request=request)


def status_view(request, **kwargs):
    states = space_states(request.event, get_or_create_cart_id(request, create=False))
    tables = NumberedTable.objects.filter(room__item__event=request.event)
    return JsonResponse({"tables": {str(t.pk): _compact(t, states) for t in tables}})


def _room_stats(room, tables, states):
    flat = [s for t in tables for s in table_states(t, states)]
    return {
        "room": room,
        "tables": len(tables),
        "size": len(flat) - flat.count(BLOCKED),
        "blocked": flat.count(BLOCKED),
        "free": sum(count_free(t, states) for t in tables),
        "sold": flat.count(SOLD),
        "held": flat.count(HELD) + flat.count(MINE),
    }


class OverviewView(EventPermissionRequiredMixin, TemplateView):
    template_name = "pretix_tables/control_overview.html"
    permission = "event.items:write"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        states = space_states(self.request.event)
        # same order as the product list
        rooms = NumberedRoom.objects.filter(item__event=self.request.event).select_related("item").prefetch_related(
            "tables").order_by(F("item__category__position").asc(nulls_first=True), "item__category", "item__position")
        ctx["rooms"] = [_room_stats(room, list(room.tables.all()), states) for room in rooms]
        return ctx


class TablesView(EventPermissionRequiredMixin, TemplateView):
    template_name = "pretix_tables/control_tables.html"
    permission = "event.items:write"

    @cached_property
    def room(self):
        return get_object_or_404(
            NumberedRoom.objects.select_related("item"), item__event=self.request.event, item_id=self.kwargs["item"]
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        states = space_states(self.request.event)
        held_by = {(t.pk, n): pos for t, n, pos in reservations(self.request.event)}
        rows = []
        tables = list(self.room.tables.all())
        for table in tables:
            st = table_states(table, states)
            rows.append({
                "table": table,
                "free": count_free(table, states),
                "taken": sum(1 for s in st if s in TAKEN),
                "spaces": [{"n": n, "state": s, "position": held_by.get((table.pk, n))}
                           for n, s in enumerate(st, start=1)],
            })
        ctx["room"] = self.room
        ctx["rows"] = rows
        ctx["stats"] = _room_stats(self.room, tables, states)
        return ctx

    def _int(self, value, low, high):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return None
        return value if low <= value <= high else None

    def post(self, request, *args, **kwargs):
        room = self.room
        tables = list(room.tables.all())
        states = space_states(request.event)
        errors = []

        if "delete" in request.POST:
            table = next((t for t in tables if str(t.pk) == request.POST["delete"]), None)
            if table and highest_taken(table, states):
                errors.append(_("{table} has reservations and can not be deleted.").format(table=table.name))
            elif table:
                table.delete()
                messages.success(request, _("{table} has been deleted.").format(table=table.name))
        elif request.POST.get("action") == "add":
            count = self._int(request.POST.get("count"), 1, 200)
            size = self._int(request.POST.get("size"), 1, 100)
            if not count or not size:
                errors.append(_("Please enter a number of tables (1–200) and spaces per table (1–100)."))
            elif room.pairs and size % 2:
                errors.append(_("Pairs need an even number of spaces."))
            else:
                start = len(tables)
                position = max((t.position for t in tables), default=0)
                NumberedTable.objects.bulk_create([
                    NumberedTable(room=room, name=_("Table {number}").format(number=start + k), size=size,
                                  position=position + k)
                    for k in range(1, count + 1)
                ])
                messages.success(request, _("{count} tables have been added.").format(count=count))
        else:
            changed = []
            for table in tables:
                name = (request.POST.get(f"name_{table.pk}") or "").strip()[:190]
                size = self._int(request.POST.get(f"size_{table.pk}"), 1, 100)
                taken = highest_taken(table, states)
                if not name or not size:
                    errors.append(_("{table}: please enter a name and a number of spaces between 1 and 100.").format(
                        table=table.name))
                elif room.pairs and size % 2:
                    errors.append(_("{table}: pairs need an even number of spaces.").format(table=table.name))
                elif size < taken:
                    errors.append(_("{table}: space {number} is already sold or reserved, so you can not go below "
                                    "{number} spaces.").format(table=table.name, number=taken))
                else:
                    try:
                        blocked = parse_numbers(request.POST.get(f"blocked_{table.pk}") or "")
                    except ValueError:
                        blocked = None
                    if blocked is None or any(n > size for n in blocked):
                        errors.append(_("{table}: “not for sale” must list space numbers between 1 and {size}, "
                                        "for example “3-4, 9”.").format(table=table.name, size=size))
                    elif room.pairs and any(table.partner(n) not in blocked for n in blocked):
                        errors.append(_("{table}: this room is sold in pairs, so only whole pairs (1-2, 3-4, …) "
                                        "can be taken out of sale.").format(table=table.name))
                    else:
                        value = ",".join(str(n) for n in sorted(blocked))
                        if (name, size, value) != (table.name, table.size, table.blocked):
                            table.name, table.size, table.blocked = name, size, value
                            changed.append(table)
            if not errors:
                for table in changed:
                    table.save()
                messages.success(request, _("Your changes have been saved."))

        for e in errors:
            messages.error(request, e)
        if not errors:
            sync_room(room)
        return redirect(reverse("plugins:pretix_tables:tables", kwargs={
            "organizer": request.organizer.slug, "event": request.event.slug, "item": room.item_id,
        }))
