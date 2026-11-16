"""Cart and checkout routes (ORD.5), in `i18n_patterns` before the CMS catch-all."""

from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("cart/", views.cart_page, name="cart"),
    path("cart/add/", views.cart_add, name="cart_add"),
    path("checkout/", views.checkout_page, name="checkout"),
    path("checkout/review/", views.review_page, name="review"),
    path("checkout/shipping/", views.shipping_choice, name="shipping"),
    path("checkout/done/", views.confirmation_page, name="confirmation"),
]
