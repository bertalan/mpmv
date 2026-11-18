"""The customer's order pages, mounted at `/account/orders/` without a language prefix (ORD.7)."""

from django.urls import path

from . import customer_views as views

urlpatterns = [
    path("", views.order_list, name="customer_orders"),
    path("<uuid:public_id>/", views.order_detail, name="customer_order"),
    # ORD.31: put a past order back in the cart (POST only).
    path("<uuid:public_id>/order-again/", views.order_again, name="customer_order_again"),
    # ORD.20 part 3: the customer's answer to the cancel-the-rest offer (POST only).
    path(
        "<uuid:public_id>/packages/<uuid:package>/cancel-rest/",
        views.cancel_rest,
        name="customer_order_cancel_rest",
    ),
    path(
        "<uuid:public_id>/packages/<uuid:package>/keep-rest/",
        views.keep_rest,
        name="customer_order_keep_rest",
    ),
]
