from django.utils.translation import gettext_lazy as _, pgettext_lazy
from pretix.base.exporter import ListExporter
from pretix.base.models import InvoiceAddress

from .services import reservations


class TableReservationsExporter(ListExporter):
    identifier = "numbered_table_reservations"
    verbose_name = _("Table reservations")
    category = pgettext_lazy("export_category", "Order data")
    description = _("Download a list with one row per reserved seat: room, table, seat, order and name.")

    def get_filename(self):
        return f"{self.event.slug}_table_reservations"

    def iterate_list(self, form_data):
        yield [
            _("Room"), _("Table"), _("Seat"), _("Order code"), _("Position"), _("Status"),
            _("Attendee name"), _("Invoice name"), _("Email"),
        ]
        rows = reservations(self.event)
        invoice_names = {
            ia.order_id: ia.name or ia.company
            for ia in InvoiceAddress.objects.filter(order_id__in={pos.order_id for _t, _n, pos in rows})
        }
        for table, number, pos in rows:
            yield [
                str(table.room), table.name, number, pos.order.code, pos.positionid,
                pos.order.get_status_display(), pos.attendee_name or "", invoice_names.get(pos.order_id, ""),
                pos.attendee_email or pos.order.email or "",
            ]
