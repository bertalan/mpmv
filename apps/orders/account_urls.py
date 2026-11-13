"""The customer's order pages, mounted at `/account/orders/` without a language prefix (ORD.7)."""

from django.urls import path

from . import customer_views as views

urlpatterns = [
    path("", views.order_list, name="customer_orders"),
    path("<uuid:public_id>/", views.order_detail, name="customer_order"),
]
