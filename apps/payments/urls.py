from django.urls import path

from .views import payment_webhook

urlpatterns = [
    path("", payment_webhook, name="payments_webhook"),
]
