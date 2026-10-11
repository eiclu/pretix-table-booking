from django.dispatch import receiver
from django.templatetags.static import static
from django.urls import Resolver404, resolve, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from pretix.base.models import CartPosition
from pretix.base.services.cart import CartError
from pretix.base.services.orders import OrderError
from pretix.base.signals import (
    event_copy_data,
    register_data_exporters,
    validate_cart,
    validate_order,
)
from pretix.control.signals import item_forms, nav_event
from pretix.presale.signals import (
    checkout_flow_steps,
    front_page_top,
    html_head,
    process_request,
)
from pretix.presale.views.cart import get_or_create_cart_id

from .checkoutflow import STEP_IDENTIFIER, NumberedSpacesStep
from .exporters import TableReservationsExporter
from .forms import NumberedRoomForm
from .models import NumberedRoom, NumberedTable, parse_guid
from .services import check_positions, sync_room
from .views import render_shop_block


@receiver(item_forms, dispatch_uid="pretix_tables_item_forms")
def control_item_forms(sender, request, item, **kwargs):
    return NumberedRoomForm(
        data=(request.POST if request.method == "POST" else None),
        files=(request.FILES if request.method == "POST" else None),
        item=item,
        prefix="numbered",
    )


@receiver(nav_event, dispatch_uid="pretix_tables_nav_event")
def control_nav_event(sender, request, **kwargs):
    if not request.user.has_event_permission(request.organizer, request.event, "event.items:write", request=request):
        return []
    url = resolve(request.path_info)
    event_kwargs = {"organizer": request.organizer.slug, "event": request.event.slug}
    return [{
        "label": _("Rooms and tables"),
        "url": reverse("plugins:pretix_tables:overview", kwargs=event_kwargs),
        "parent": reverse("control:event.items", kwargs=event_kwargs),
        "active": url.namespace == "plugins:pretix_tables" and url.url_name in ("overview", "tables"),
    }]


@receiver(html_head, dispatch_uid="pretix_tables_html_head")
def presale_html_head(sender, request, **kwargs):
    try:
        url = resolve(request.path_info)
    except Resolver404:  # this also runs while pretix renders its "not found" page
        return ""
    on_step = url.url_name == "event.checkout" and url.kwargs.get("step") == STEP_IDENTIFIER
    if url.url_name != "event.index" and not on_step:
        return ""
    return format_html(
        '<link rel="stylesheet" href="{}"><script src="{}" defer></script>',
        static("pretix_tables/numbered.css"), static("pretix_tables/numbered.js"),
    )


@receiver(front_page_top, dispatch_uid="pretix_tables_front_page_top")
def presale_front_page_top(sender, request, **kwargs):
    return render_shop_block(sender, request)


@receiver(checkout_flow_steps, dispatch_uid="pretix_tables_checkout_flow_steps")
def presale_checkout_flow_steps(sender, **kwargs):
    return NumberedSpacesStep


@receiver(process_request, dispatch_uid="pretix_tables_process_request")
def presale_process_request(sender, request, **kwargs):
    # When one half of a pair is removed from the cart, remove the other half as well.
    if request.method != "POST" or not request.path_info.rstrip("/").endswith("/cart/remove"):
        return
    try:
        position_id = int(request.POST.get("id", ""))
    except ValueError:
        return
    cart_id = get_or_create_cart_id(request, create=False)
    if not cart_id:
        return
    pos = CartPosition.objects.filter(
        event=sender, cart_id=cart_id, pk=position_id, seat__isnull=False
    ).select_related("seat").first()
    parsed = parse_guid(pos.seat.seat_guid) if pos else None
    if not parsed:
        return
    table = NumberedTable.objects.filter(pk=parsed[0], room__pairs=True).first()
    if table:
        CartPosition.objects.filter(
            event=sender, cart_id=cart_id, seat__seat_guid=table.guid(table.partner(parsed[1]))
        ).delete()


@receiver(validate_cart, dispatch_uid="pretix_tables_validate_cart")
def base_validate_cart(sender, positions, **kwargs):
    check_positions(positions, CartError)


@receiver(validate_order, dispatch_uid="pretix_tables_validate_order")
def base_validate_order(sender, positions, **kwargs):
    check_positions(positions, OrderError)


@receiver(register_data_exporters, dispatch_uid="pretix_tables_register_data_exporters")
def base_register_data_exporters(sender, **kwargs):
    return TableReservationsExporter


@receiver(event_copy_data, dispatch_uid="pretix_tables_event_copy_data")
def base_event_copy_data(sender, other, item_map, quota_map, **kwargs):
    for room in NumberedRoom.objects.filter(item__event=other).prefetch_related("tables"):
        if room.item_id not in item_map:
            continue
        copy = NumberedRoom.objects.create(
            item=item_map[room.item_id], pairs=room.pairs, quota=quota_map.get(room.quota_id), map=room.map.name,
        )
        NumberedTable.objects.bulk_create([
            NumberedTable(room=copy, name=t.name, size=t.size, position=t.position, blocked=t.blocked)
            for t in room.tables.all()
        ])
        sync_room(copy)
