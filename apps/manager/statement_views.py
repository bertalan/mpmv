"""Manager shop space: the statements of reasons the platform sent the vendor (DSA art. 17).

A read-only page of the selected shop's statements, newest first, each with an anchor named after
the notice it follows (`#notice-<reference>`), so an email can link to it before the statement
exists. The statements are immutable (CMP.5); this page only shows them.
"""

from typing import Any

from apps.compliance.models import StatementOfReasons

from .views import ManagerView

SECTION = "shop.statements"


class VendorStatementsView(ManagerView):
    """The statements of reasons of the selected shop."""

    section = SECTION
    template_name = "manager/shop/statements.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["statements"] = (
            self.scoped(StatementOfReasons).select_related("notice").order_by("-sent_at", "-pk")
        )
        return context
