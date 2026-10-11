from django.utils.translation import gettext_lazy
from pretix.base.plugins import PluginConfig

from . import __version__


class PluginApp(PluginConfig):
    default = True
    name = "pretix_tables"
    verbose_name = "Numbered seats"

    class PretixPluginMeta:
        name = gettext_lazy("Numbered seats")
        author = "Luca Eichler"
        description = gettext_lazy(
            "Sell products and variations as numbered seats, e.g. at a table. "
            "Buyers pick their seats in a popup."
        )
        visible = True
        version = __version__
        category = "FEATURE"
        compatibility = "pretix>=2026.8.0.dev0"

    def ready(self):
        from . import signals  # NOQA
