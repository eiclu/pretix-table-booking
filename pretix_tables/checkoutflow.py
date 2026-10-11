from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from pretix.base.services.cart import CartError, CartManager
from pretix.base.services.cross_selling import CrossSellingService
from pretix.base.services.locking import LockTimeoutException
from pretix.presale.checkoutflow import TemplateFlowStep
from pretix.presale.productlist import prepare_item_list_for_shop
from pretix.presale.views import CartMixin, get_cart
from pretix.presale.views.cart import get_or_create_cart_id

from .models import NumberedRoom, parse_guid
from .terms import step_label, step_text
from .views import _memberships, build_block

STEP_IDENTIFIER = "numbered-spaces"


class NumberedSpacesStep(CartMixin, TemplateFlowStep):
    """
    Offers rooms with numbered tables during checkout. pretix' own cross-selling step can not show
    products that need a seat, so this step sits right after it and follows the same category
    settings ("cross-selling category", "normal + cross-selling category" and their conditions).
    """
    priority = 41
    identifier = STEP_IDENTIFIER
    template_name = "pretix_tables/checkout_step.html"
    icon = "th"

    @property
    def label(self):
        return step_label(self.event)

    def _item_ids(self, request):
        """IDs of numbered products that the cross-selling settings offer for the current cart."""
        if not hasattr(request, "_numbered_spaces_item_ids"):
            ids = set()
            event = self.event
            numbered = set(NumberedRoom.objects.filter(item__event=event).values_list("item_id", flat=True))
            if numbered and not event.has_subevents:
                service = CrossSellingService(
                    event, request.sales_channel, list(get_cart(request)), getattr(request, "customer", None)
                )
                for _category, items_qs, _info in service._applicable_categories(0):
                    ids |= numbered & set(items_qs.values_list("pk", flat=True))
            request._numbered_spaces_item_ids = ids
        return request._numbered_spaces_item_ids

    def is_applicable(self, request):
        return bool(self._item_ids(request))

    def is_completed(self, request, warn=False):
        return True

    def _block(self):
        items, _btn = prepare_item_list_for_shop(
            self.event, channel=self.request.sales_channel, require_seat=True, allow_cross_sell=True,
            base_qs=self.event.items.filter(pk__in=self._item_ids(self.request)),
            memberships=_memberships(self.event, self.request),
        )
        return build_block(self.event, self.request, items)

    def get(self, request):
        self.request = request
        block = self._block()
        if not block:
            return redirect(self.get_prev_url(request) if request.GET.get("dir") == "prev"
                            else self.get_next_url(request))
        return self.render(cart=self.get_cart(), intro=step_text(self.event), **block)

    def post(self, request):
        self.request = request
        items = []
        for room in NumberedRoom.objects.filter(item_id__in=self._item_ids(request)).prefetch_related("tables"):
            sizes = {t.pk: t.size for t in room.tables.all()}
            for guid in request.POST.getlist(room.field_name):
                parsed = parse_guid(guid)
                if not parsed or not 1 <= parsed[1] <= sizes.get(parsed[0], 0):
                    continue
                items.append({
                    "item": room.item_id, "variation": None, "count": 1, "seat": guid,
                    "price": "", "voucher": None, "subevent": None,
                })
        if items:
            try:
                cm = CartManager(
                    event=self.event, cart_id=get_or_create_cart_id(request),
                    sales_channel=request.sales_channel,
                    invoice_address=self.invoice_address if self.invoice_address.pk else False,
                )
                cm.add_new_items(items)
                cm.commit()
            except CartError as e:
                messages.error(request, str(e))
                return redirect(self.get_step_url(request))
            except LockTimeoutException:
                messages.error(request, _("We were not able to process your request completely as the server "
                                          "was too busy. Please try again."))
                return redirect(self.get_step_url(request))
        return redirect(self.get_next_url(request))
