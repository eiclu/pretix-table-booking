from collections import defaultdict
from django.db import transaction
from django.db.models import F, ProtectedError, Q
from django.utils.timezone import now
from django.utils.translation import gettext as _
from pretix.base.models import (
    CartPosition,
    Order,
    OrderPosition,
    Quota,
    Seat,
    SeatCategoryMapping,
    Voucher,
)

from .models import GUID_PREFIX, NumberedTable, parse_guid
from .terms import get_terms

LAYOUT_CATEGORY = "pretix_tables"

FREE, MINE, HELD, SOLD, BLOCKED = "free", "mine", "held", "sold", "blocked"
TAKEN = (MINE, HELD, SOLD)


def room_seats(room):
    return Seat.objects.filter(event_id=room.item.event_id, subevent__isnull=True, product=room.item,
                               seat_guid__startswith=GUID_PREFIX + "-")


def _delete_or_block(seats):
    for seat in seats:
        try:
            with transaction.atomic():
                seat.delete()
        except ProtectedError:
            # Still referenced by an old order, keep it but take it out of sale
            if not seat.blocked:
                seat.blocked = True
                seat.save(update_fields=["blocked"])


@transaction.atomic
def sync_room(room):
    """Makes seats, seat mapping and quota match the room's tables."""
    item = room.item
    event = item.event
    SeatCategoryMapping.objects.get_or_create(
        event=event, subevent=None, product=item, defaults={"layout_category": LAYOUT_CATEGORY}
    )

    existing = {seat.seat_guid: seat for seat in room_seats(room)}
    create = []
    total = 0
    for table in room.tables.all():
        total += table.for_sale
        not_for_sale = table.blocked_numbers
        for number in range(1, table.size + 1):
            # pretix does not sell blocked seats in the shop, but they can still be assigned in the backend
            blocked = number in not_for_sale
            seat = existing.pop(table.guid(number), None)
            if seat is None:
                create.append(Seat(
                    event=event, subevent=None, product=item, zone_name=table.name, seat_number=str(number),
                    seat_guid=table.guid(number), sorting_rank=table.pk * 10000 + number, blocked=blocked,
                ))
            elif seat.blocked != blocked or seat.zone_name != table.name:
                seat.blocked = blocked
                seat.zone_name = table.name
                seat.save(update_fields=["blocked", "zone_name"])
    Seat.objects.bulk_create(create)
    _delete_or_block(existing.values())

    name = _("{name} (numbered seats)").format(name=str(item))[:200]
    quota = room.quota
    if quota is None:
        quota = Quota.objects.create(event=event, name=name, size=total)
        quota.items.add(item)
        room.quota = quota
        room.save(update_fields=["quota"])
    else:
        if quota.size != total:
            quota.size = total
            quota.save(update_fields=["size"])
        quota.items.add(item)
    quota.rebuild_cache()
    event.cache.clear()


@transaction.atomic
def remove_room(room):
    """
    Turns a room back into a normal product. The quota is kept as a regular quota so the
    product stays on sale.
    """
    item = room.item
    _delete_or_block(room_seats(room))
    SeatCategoryMapping.objects.filter(
        event_id=item.event_id, subevent__isnull=True, product=item, layout_category=LAYOUT_CATEGORY
    ).delete()
    if room.map:
        room.map.delete(save=False)
    room.delete()
    item.event.cache.clear()


def space_states(event, cart_id=None):
    """
    Returns ``{table_id: {number: state}}`` for every seat of the event, using a fixed number of
    queries. A seat is taken by a pending or paid order, an unexpired cart or a valid voucher.
    """
    sold = set(OrderPosition.objects.filter(
        order__event=event, seat__isnull=False, order__status__in=[Order.STATUS_PENDING, Order.STATUS_PAID],
    ).values_list("seat_id", flat=True))
    carts = dict(CartPosition.objects.filter(
        event=event, seat__isnull=False, expires__gte=now()
    ).values_list("seat_id", "cart_id"))
    vouchers = set(Voucher.objects.filter(
        event=event, seat__isnull=False, redeemed__lt=F("max_usages")
    ).filter(Q(valid_until__isnull=True) | Q(valid_until__gte=now())).values_list("seat_id", flat=True))

    result = defaultdict(dict)
    seats = Seat.objects.filter(
        event=event, subevent__isnull=True, seat_guid__startswith=GUID_PREFIX + "-"
    ).values_list("pk", "seat_guid", "blocked")
    for pk, guid, blocked in seats:
        parsed = parse_guid(guid)
        if not parsed:
            continue
        if pk in sold:
            state = SOLD
        elif blocked:
            state = BLOCKED
        elif pk in carts:
            state = MINE if cart_id and carts[pk] == cart_id else HELD
        elif pk in vouchers:
            state = HELD
        else:
            state = FREE
        result[parsed[0]][parsed[1]] = state
    return result


def table_states(table, states):
    """List of states for seats 1..size of a table. Seats without a seat count as not for sale."""
    mine = states.get(table.pk, {})
    return [mine.get(n, BLOCKED) for n in range(1, table.size + 1)]


def count_free(table, states):
    """Number of seats that can still be bought, respecting pairs."""
    st = table_states(table, states)
    return sum(len(g) for g in table.groups() if all(st[n - 1] == FREE for n in g))


def highest_taken(table, states):
    return max((n for n, s in states.get(table.pk, {}).items() if s in TAKEN and n <= table.size), default=0)


def reservations(event):
    """Order positions of pending and paid orders that hold one of our seats, with their table and number."""
    positions = OrderPosition.objects.filter(
        order__event=event, seat__seat_guid__startswith=GUID_PREFIX + "-",
        order__status__in=[Order.STATUS_PENDING, Order.STATUS_PAID],
    ).select_related("order", "seat", "item")
    parsed = [(pos, parse_guid(pos.seat.seat_guid)) for pos in positions]
    tables = NumberedTable.objects.select_related("room__item").in_bulk({p[0] for _pos, p in parsed if p})
    result = [(tables[p[0]], p[1], pos) for pos, p in parsed if p and p[0] in tables]
    result.sort(key=lambda r: (str(r[0].room.item), r[0].position, r[0].pk, r[1]))
    return result


def check_positions(positions, exc_class):
    """
    Validates cart positions that carry one of our seats: pairs must be complete. That the seat
    belongs to the product and is still free is checked by pretix itself.
    """
    present = {parse_guid(pos.seat.seat_guid) for pos in positions if pos.seat_id} - {None}
    if not present:
        return
    tables = NumberedTable.objects.select_related("room__item").in_bulk({t for t, _n in present})
    for table_id, number in sorted(present):
        table = tables.get(table_id)
        if table and table.room.pairs and (table_id, table.partner(number)) not in present:
            raise exc_class(
                _("{room_name}, {table_name}: {spaces} are only sold in pairs. Please add {space} {missing} as "
                  "well or remove {space} {number} from your cart.").format(
                    room_name=str(table.room), table_name=table.name, missing=table.partner(number),
                    number=number, **get_terms(table.room.item.event, table.room))
            )
