import uuid
from django.db import models
from i18nfield.fields import I18nCharField

GUID_PREFIX = "numbered"


def parse_guid(guid):
    """Returns ``(table_id, number)`` for a seat GUID created by this plugin, else ``None``."""
    parts = (guid or "").split("-")
    if len(parts) != 3 or parts[0] != GUID_PREFIX:
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def parse_numbers(text):
    """Parses ``"1-4, 9"`` into ``{1, 2, 3, 4, 9}``. Raises ``ValueError`` on anything else."""
    numbers = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        first, _sep, last = part.partition("-")
        first, last = int(first), int(last or first)
        if first < 1 or last < first or last > 1000:
            raise ValueError(part)
        numbers.update(range(first, last + 1))
    return numbers


def format_numbers(numbers):
    """Formats ``{1, 2, 3, 4, 9}`` as ``"1-4, 9"``."""
    parts, run = [], []
    for n in sorted(numbers) + [None]:
        if run and n == run[-1] + 1:
            run.append(n)
            continue
        if run:
            parts.append(str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}")
        run = [n]
    return ", ".join(parts)


def room_map_upload_to(instance, filename: str) -> str:
    event = instance.item.event
    return "pub/%s/%s/numbered-room-%s-%s.%s" % (
        event.organizer.slug, event.slug, instance.item_id, str(uuid.uuid4()), filename.split(".")[-1]
    )


class NumberedRoom(models.Model):
    """
    A product whose stock is a set of tables with numbered seats. The product's price is the
    price per seat.
    """
    item = models.OneToOneField("pretixbase.Item", on_delete=models.CASCADE, related_name="numbered_room")
    pairs = models.BooleanField(default=False)
    quota = models.ForeignKey("pretixbase.Quota", on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="+")
    map = models.FileField(upload_to=room_map_upload_to, null=True, blank=True, max_length=255)
    # Words for a seat and a table in this room, empty means the event's wording
    term_space = I18nCharField(max_length=100, null=True, blank=True)
    term_spaces = I18nCharField(max_length=100, null=True, blank=True)
    term_table = I18nCharField(max_length=100, null=True, blank=True)
    term_tables = I18nCharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return str(self.item)

    @property
    def field_name(self):
        """Name of the POST field pretix' cart understands for seats of this product."""
        return f"seat_{self.item_id}"


class NumberedTable(models.Model):
    """
    One table in a room. Its seats are pretix ``Seat`` objects, so pretix takes care of never
    selling a seat twice.
    """
    room = models.ForeignKey(NumberedRoom, on_delete=models.CASCADE, related_name="tables")
    name = models.CharField(max_length=190)
    size = models.PositiveIntegerField()
    position = models.IntegerField(default=0)
    # Seat numbers that exist on the table but are not sold here, e.g. "3,4,9"
    blocked = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ("position", "id")

    def __str__(self):
        return self.name

    @property
    def blocked_numbers(self):
        return {int(n) for n in self.blocked.split(",") if n}

    @property
    def blocked_display(self):
        return format_numbers(self.blocked_numbers)

    @property
    def for_sale(self):
        """Number of seats that are sold through the shop."""
        return self.size - len(self.blocked_numbers)

    def guid(self, number):
        return f"{GUID_PREFIX}-{self.pk}-{number}"

    def partner(self, number):
        return number + 1 if number % 2 else number - 1

    def groups(self):
        """Seats that can only be bought together: fixed pairs or single seats."""
        if not self.room.pairs:
            return [[n] for n in range(1, self.size + 1)]
        return [[n, n + 1] for n in range(1, self.size + 1, 2)]
