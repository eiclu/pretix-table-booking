import datetime
import pytest
from decimal import Decimal
from django.test import Client
from django.utils.timezone import now
from django_scopes import scopes_disabled
from pretix.base.models import (
    CartPosition,
    Event,
    Item,
    ItemCategory,
    ItemVariation,
    Order,
    OrderPosition,
    Organizer,
    Quota,
    Seat,
    Team,
    User,
)
from pretix.base.services.cart import CartError

from pretix_tables.exporters import TableReservationsExporter
from pretix_tables.forms import NumberedRoomForm
from pretix_tables.models import NumberedRoom
from pretix_tables.services import check_positions, space_states


def room_form(item, **data):
    return NumberedRoomForm(data={f"numbered-{k}": v for k, v in data.items()}, files={}, item=item,
                            prefix="numbered")


def configure(item, **data):
    form = room_form(item, **data)
    assert form.is_valid(), form.errors
    form.save()


@pytest.fixture
def env(db):
    with scopes_disabled():
        orga = Organizer.objects.create(name="Ball", slug="ball")
        event = Event.objects.create(
            organizer=orga, name="Winter Ball", slug="wb", live=True, plugins="pretix_tables",
            date_from=now() + datetime.timedelta(days=30),
        )
        hall = Item.objects.create(event=event, name="Great Hall", default_price=Decimal("25.00"))
        bar = Item.objects.create(event=event, name="Bar", default_price=Decimal("18.00"))
        ticket = Item.objects.create(event=event, name="Standing", default_price=Decimal("15.00"))
        q = Quota.objects.create(event=event, name="Standing", size=100)
        q.items.add(ticket)
        configure(hall, enabled="on", pairs="on", tables=3, spaces=12)
        configure(bar, enabled="on", tables=1, spaces=8)
        rhall = NumberedRoom.objects.get(item=hall)
        rbar = NumberedRoom.objects.get(item=bar)
        t1, t2, t3 = rhall.tables.all()
        tbar = rbar.tables.get()
    return dict(orga=orga, event=event, hall=hall, bar=bar, ticket=ticket, rhall=rhall, rbar=rbar,
                t1=t1, t2=t2, t3=t3, tbar=tbar)


@pytest.fixture
def admin(env, client):
    with scopes_disabled():
        user = User.objects.create_superuser("admin@example.org", "admin")
        team = Team.objects.create(organizer=env["orga"], all_events=True, all_event_permissions=True,
                                   all_organizer_permissions=True)
        team.members.add(user)
    client.force_login(user)
    session = client.session
    session["pretix_auth_login_time"] = session["pretix_auth_last_used"] = int(now().timestamp())
    session.save()
    return client


def add(client, data):
    return client.post("/ball/wb/cart/add", data, follow=True)


def test_setup_creates_seats_quota_and_mapping(env):
    with scopes_disabled():
        event, rhall, t1 = env["event"], env["rhall"], env["t1"]
        assert Seat.objects.filter(event=event, product=env["hall"]).count() == 36
        assert Seat.objects.filter(event=event).count() == 36 + 8
        assert rhall.quota.size == 36
        assert list(rhall.quota.items.all()) == [env["hall"]]
        assert event.seat_category_mappings.filter(product=env["hall"]).count() == 1
        assert [t.name for t in rhall.tables.all()] == ["Table 1", "Table 2", "Table 3"]
        assert str(Seat.objects.get(seat_guid=t1.guid(5))) == "Table 1, Seat 5"


def test_shop_page_lists_rooms(env, client):
    content = client.get("/ball/wb/").content.decode()
    assert 'id="numbered-spaces"' in content
    assert "Great Hall" in content and "Bar" in content
    assert "36 of 36 spaces free" in content
    assert '"s": "ffffffffffff"' in content
    assert "pretix_tables/numbered.js" in content
    # the regular product still shows and brings the shop's own button
    assert 'id="btn-add-to-cart"' in content


def test_space_can_not_be_booked_twice(env, client):
    t1, field = env["t1"], env["rhall"].field_name
    add(client, {field: [t1.guid(5), t1.guid(6)], f"item_{env['ticket'].pk}": "1"})
    with scopes_disabled():
        assert CartPosition.objects.filter(seat__isnull=False).count() == 2
        assert CartPosition.objects.count() == 3
        assert space_states(env["event"])[t1.pk][5] == "held"

    other = Client()
    r = add(other, {field: [t1.guid(5), t1.guid(6)]})
    assert "alert-danger" in r.content.decode()
    with scopes_disabled():
        assert CartPosition.objects.filter(seat__isnull=False).count() == 2

    mine = client.get("/ball/wb/numbered-spaces/status/").json()["tables"][str(t1.pk)]
    theirs = other.get("/ball/wb/numbered-spaces/status/").json()["tables"][str(t1.pk)]
    assert mine == "ffffmmffffff" and theirs == "ffffhhffffff"
    assert "34 of 36 spaces free" in other.get("/ball/wb/").content.decode()


def test_pairs_are_enforced(env, client):
    t1, field = env["t1"], env["rhall"].field_name
    add(client, {field: [t1.guid(1)]})
    with scopes_disabled():
        with pytest.raises(CartError):
            check_positions(CartPosition.objects.all(), CartError)
    r = client.get("/ball/wb/checkout/start", follow=True)
    assert "only sold in pairs" in r.content.decode()

    add(client, {field: [t1.guid(2)]})
    with scopes_disabled():
        check_positions(CartPosition.objects.all(), CartError)


def test_removing_one_half_removes_the_pair(env, client):
    t1, tbar = env["t1"], env["tbar"]
    add(client, {env["rhall"].field_name: [t1.guid(7), t1.guid(8)],
                 env["rbar"].field_name: [tbar.guid(1), tbar.guid(2)]})
    with scopes_disabled():
        assert CartPosition.objects.count() == 4
        pos = CartPosition.objects.get(seat__seat_guid=t1.guid(7))
    client.post("/ball/wb/cart/remove", {"id": pos.pk}, follow=True)
    with scopes_disabled():
        assert sorted(CartPosition.objects.values_list("seat__seat_guid", flat=True)) == [tbar.guid(1), tbar.guid(2)]
        pos = CartPosition.objects.get(seat__seat_guid=tbar.guid(1))
    client.post("/ball/wb/cart/remove", {"id": pos.pk}, follow=True)
    with scopes_disabled():
        assert list(CartPosition.objects.values_list("seat__seat_guid", flat=True)) == [tbar.guid(2)]


def test_manage_tables(env, admin):
    hall, rhall, t1, t2, t3 = env["hall"], env["rhall"], env["t1"], env["t2"], env["t3"]
    url = f"/control/event/ball/wb/numbered-spaces/{hall.pk}/"
    add(Client(), {rhall.field_name: [t1.guid(9), t1.guid(10)]})

    assert "Table 1" in admin.get(url).content.decode()

    admin.post(url, {"action": "add", "count": "2", "size": "10"})
    with scopes_disabled():
        rhall.refresh_from_db()
        assert [t.name for t in rhall.tables.all()][-2:] == ["Table 4", "Table 5"]
        assert rhall.quota.size == 36 + 20
        assert Seat.objects.filter(product=hall).count() == 56
    # odd size is refused for a room sold in pairs
    admin.post(url, {"action": "add", "count": "1", "size": "7"})
    with scopes_disabled():
        assert rhall.tables.count() == 5

    def save(**changes):
        with scopes_disabled():
            data = {"action": "save"}
            for t in rhall.tables.all():
                data[f"name_{t.pk}"] = t.name
                data[f"size_{t.pk}"] = str(t.size)
        data.update(changes)
        return admin.post(url, data, follow=True)

    # shrinking below a held space is refused and nothing is saved
    r = save(**{f"size_{t1.pk}": "8", f"name_{t2.pk}": "Family table"})
    assert "alert-danger" in r.content.decode()
    with scopes_disabled():
        t1.refresh_from_db(), t2.refresh_from_db()
        assert t1.size == 12 and t2.name == "Table 2"

    save(**{f"size_{t1.pk}": "10", f"name_{t2.pk}": "Family table"})
    with scopes_disabled():
        t1.refresh_from_db(), t2.refresh_from_db()
        assert t1.size == 10 and t2.name == "Family table"
        assert Seat.objects.filter(seat_guid=t2.guid(1)).get().zone_name == "Family table"
        assert not Seat.objects.filter(seat_guid=t1.guid(11)).exists()
        rhall.refresh_from_db()
        assert rhall.quota.size == 10 + 12 + 12 + 20

    admin.post(url, {"delete": str(t1.pk)})
    admin.post(url, {"delete": str(t3.pk)})
    with scopes_disabled():
        assert rhall.tables.filter(pk=t1.pk).exists()
        assert not rhall.tables.filter(pk=t3.pk).exists()
        assert not Seat.objects.filter(seat_guid=t3.guid(1)).exists()


def test_form_rules_and_disable(env, client):
    hall, bar = env["hall"], env["bar"]
    with scopes_disabled():
        # an existing room with an odd table can not be switched to pairs
        tbar = env["tbar"]
        tbar.size = 7
        tbar.save()
        assert not room_form(bar, enabled="on", pairs="on").is_valid()
        # a new room needs tables and spaces
        plain = Item.objects.create(event=env["event"], name="Plain", default_price=Decimal("1.00"))
        assert not room_form(plain, enabled="on").is_valid()
        assert not room_form(plain, enabled="on", pairs="on", tables=2, spaces=5).is_valid()
        # products with variations can not be rooms
        ItemVariation.objects.create(item=plain, value="A")
        form = room_form(plain, enabled="on", tables=2, spaces=4)
        assert form.unsupported and form.is_valid()
        form.save()
        assert not NumberedRoom.objects.filter(item=plain).exists()

        quota = env["rhall"].quota
        configure(hall)
        assert not NumberedRoom.objects.filter(item=hall).exists()
        assert not Seat.objects.filter(product=hall).exists()
        assert not env["event"].seat_category_mappings.filter(product=hall).exists()
        assert Quota.objects.filter(pk=quota.pk).exists()
    # back in the normal product list
    assert f'name="item_{hall.pk}"' in client.get("/ball/wb/").content.decode()


def test_control_pages(env, admin):
    r = admin.get(f"/control/event/ball/wb/items/{env['hall'].pk}/")
    assert r.status_code == 200
    content = r.content.decode()
    assert "numbered-pairs" in content and "numbered-map" in content and "Manage tables" in content
    r = admin.get("/control/event/ball/wb/numbered-spaces/")
    assert r.status_code == 200
    assert "Great Hall" in r.content.decode()


def test_export_lists_reservations(env):
    event, t2 = env["event"], env["t2"]
    with scopes_disabled():
        order = Order.objects.create(
            code="ABCDE", event=event, email="guest@example.org", status=Order.STATUS_PAID,
            datetime=now(), expires=now() + datetime.timedelta(days=10), total=Decimal("50.00"),
            sales_channel=env["orga"].sales_channels.get(identifier="web"),
        )
        for n in (3, 4):
            OrderPosition.objects.create(
                order=order, item=env["hall"], price=Decimal("25.00"), positionid=n,
                attendee_name_parts={"_legacy": "Ada Huber"}, seat=Seat.objects.get(seat_guid=t2.guid(n)),
            )
        rows = list(TableReservationsExporter(event, env["orga"]).iterate_list({}))
        assert len(rows) == 3
        assert [str(c) for c in rows[1][:5]] == ["Great Hall", "Table 2", "3", "ABCDE", "3"]
        assert rows[1][6] == "Ada Huber" and rows[1][8] == "guest@example.org"
        assert space_states(event)[t2.pk][3] == "sold"


def test_checkout_step_offers_cross_selling_rooms(env, client):
    hall, rhall, rbar, t1, tbar = env["hall"], env["rhall"], env["rbar"], env["t1"], env["tbar"]
    step = "/ball/wb/checkout/numbered-spaces/"
    with scopes_disabled():
        cat = ItemCategory.objects.create(event=env["event"], name="Tables", cross_selling_mode="only",
                                          cross_selling_condition="always")
        hall.category = cat
        hall.save()

    # cross-selling-only rooms are not on the front page, the bar still is
    content = client.get("/ball/wb/").content.decode()
    assert "Great Hall" not in content and '"name": "Bar"' in content

    add(client, {f"item_{env['ticket'].pk}": "1"})
    r = client.get("/ball/wb/checkout/start", follow=True)
    assert step in r.redirect_chain[-1][0]
    content = r.content.decode()
    assert '"name": "Great Hall"' in content and '"name": "Bar"' not in content
    assert "pretix_tables/numbered.js" in content

    r = client.post(step, {rhall.field_name: [t1.guid(3), t1.guid(4)], rbar.field_name: [tbar.guid(1)]})
    assert r.status_code == 302 and step not in r.url
    with scopes_disabled():
        # the bar is not offered in this step, so it is ignored
        assert sorted(CartPosition.objects.filter(seat__isnull=False).values_list("seat__seat_guid", flat=True)) == [
            t1.guid(3), t1.guid(4)
        ]

    # someone else took the space in the meantime
    other = Client()
    add(other, {f"item_{env['ticket'].pk}": "1"})
    r = other.post(step, {rhall.field_name: [t1.guid(3), t1.guid(4)]}, follow=True)
    assert step in r.redirect_chain[-1][0]
    assert "alert-danger" in r.content.decode()

    # continuing without a selection is fine
    r = other.post(step, {})
    assert r.status_code == 302 and step not in r.url


def test_checkout_step_is_skipped_without_cross_selling(env, client):
    add(client, {f"item_{env['ticket'].pk}": "1"})
    r = client.get("/ball/wb/checkout/start", follow=True)
    assert "numbered-spaces" not in r.redirect_chain[-1][0]


def test_spaces_not_for_sale(env, admin):
    hall, rhall, t1, t2 = env["hall"], env["rhall"], env["t1"], env["t2"]
    url = f"/control/event/ball/wb/numbered-spaces/{hall.pk}/"

    def save(**changes):
        with scopes_disabled():
            data = {"action": "save"}
            for t in rhall.tables.all():
                data.update({f"name_{t.pk}": t.name, f"size_{t.pk}": str(t.size), f"blocked_{t.pk}": t.blocked})
        data.update(changes)
        return admin.post(url, data, follow=True)

    # half a pair, a number beyond the table and nonsense are refused
    for bad in ("3", "13-14", "x"):
        assert "alert-danger" in save(**{f"blocked_{t1.pk}": bad}).content.decode()
    save(**{f"blocked_{t1.pk}": "3-4, 11, 12", f"size_{t2.pk}": "10"})
    with scopes_disabled():
        t1.refresh_from_db(), rhall.refresh_from_db()
        assert t1.blocked == "3,4,11,12" and t1.blocked_display == "3-4, 11-12"
        assert rhall.quota.size == 8 + 10 + 12
        assert Seat.objects.get(seat_guid=t1.guid(3)).blocked
        assert space_states(env["event"])[t1.pk][3] == "blocked"
    assert 'value="3-4, 11-12"' in admin.get(url).content.decode()

    buyer = Client()
    content = buyer.get("/ball/wb/").content.decode()
    assert '"s": "ffxxffffffxx"' in content and "30 of 30 spaces free" in content
    # the shop refuses a blocked space even if it is requested by hand
    add(buyer, {rhall.field_name: [t1.guid(3), t1.guid(4)]})
    with scopes_disabled():
        assert not CartPosition.objects.exists()

    # putting them back on sale
    save(**{f"blocked_{t1.pk}": ""})
    with scopes_disabled():
        rhall.refresh_from_db()
        assert rhall.quota.size == 12 + 10 + 12
        assert not Seat.objects.get(seat_guid=t1.guid(3)).blocked


def test_overview_uses_product_order(env, admin):
    with scopes_disabled():
        env["hall"].position = 5
        env["hall"].save()
        env["bar"].position = 1
        env["bar"].save()
    content = admin.get("/control/event/ball/wb/numbered-spaces/").content.decode()
    assert content.index(">Bar<") < content.index(">Great Hall<")
