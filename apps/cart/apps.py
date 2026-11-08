from django.apps import AppConfig


class CartConfig(AppConfig):
    name = "apps.cart"
    label = "cart"

    # Audit policy (SEC.4, docs/dev/audit-trail.md): a cart is the shopper's working list, with
    # display prices only; the order placed from it is snapshotted and audited (ORD.2, ORD.4).
    audited_models = ()
    unaudited_models = {
        "cart": "a shopper's working cart; no money is committed until the order is placed",
        "cartline": "a cart line with a display price; the order snapshots the real prices",
    }

    def ready(self) -> None:
        from django.contrib.auth.signals import user_logged_in

        from .session import merge_guest_cart_on_login

        user_logged_in.connect(merge_guest_cart_on_login, dispatch_uid="cart.merge_on_login")
