# Technical Django admin access (OPS.5)

The technical Django admin lives at `/django-admin/`. It is for the platform owner only, and it
is never reachable from the public internet.

## Policy (owner decision MQ-24)

- No public network is ever allow-listed. STAGE and LIVE allow **loopback only**:
  `MPMV_ADMIN_ALLOWED_NETWORKS=127.0.0.1/32,::1/128` in the app env file (`mpmv.env`, comma
  separated) and `MPMV_ADMIN_ALLOWED_NETWORKS=127.0.0.1/32 ::1/128` in the Caddy env file
  (`caddy.env`, space separated). Both examples are in `deploy/server/`.
- Two gates apply. Caddy answers a client outside the list with the app's own 404 (matcher
  `@admin_denied` in `deploy/caddy/Caddyfile`). The app repeats the check
  (`AdminNetworkAllowlistMiddleware`), using the client address Caddy forwards in `X-Real-IP`,
  read only from `MPMV_TRUSTED_PROXIES`.
- Fail closed: an unset or empty value in the app means 404 for everybody. In `caddy.env`, leave
  the variable out or set it to the loopback value; never set it empty, because Caddy then fails
  to load the Caddyfile.
- The Wagtail admin (`/admin/`) is a separate surface with its own gates and is not affected.

## Reaching the admin: SSH tunnel

The tunnel ends on the server's own port 443, so Caddy and the app both see the request come
from `127.0.0.1`. Caddy matches the site by host name and not by port, so the local port does
not matter.

1. Add a hosts-file line on your computer that maps the site name to loopback
   (`/etc/hosts`, or `C:\Windows\System32\drivers\etc\hosts` on Windows):

   ```
   127.0.0.1   <site-name>
   ```

   `<site-name>` is the value of `MPMV_SITE_ADDRESS` of that environment.
2. Open the tunnel and keep it running:

   ```sh
   ssh -N -L 8443:127.0.0.1:443 <user>@<server>
   ```

3. Browse to `https://<site-name>:8443/django-admin/` and sign in as usual.
4. Close the tunnel (Ctrl+C) and remove the hosts-file line when you are done. While the line is
   there, the public site is unreachable from your computer under that name.

## Troubleshooting

- **404 on the admin page.** The request did not arrive from loopback: you went to the public
  address, the tunnel is down, or the hosts-file line is missing, so the name still resolves to
  the public server. Check that `ssh` is running and that the name resolves to `127.0.0.1`.
- **Certificate warning.** You used `localhost` or `127.0.0.1` in the URL. The certificate is
  issued for the site name: use `https://<site-name>:8443/` with the hosts-file line.
- **`bind: Address already in use`.** Port 8443 is taken on your computer. Use another local port
  (`-L 9443:127.0.0.1:443`) and the same port in the URL.
- **Still 404 with the tunnel up.** Check that both env files carry the loopback value, that the
  app was restarted and Caddy reloaded after the change, and that `MPMV_TRUSTED_PROXIES` covers
  the Caddy subnet (`manage check --deploy` warns about both, `core.W001` and `core.W002`).

## Deploy check

After setting both env files, restart the app and reload Caddy, then verify from your computer:

1. With the tunnel open, `https://<site-name>:8443/django-admin/` shows the admin sign-in page.
2. Without the tunnel (and without the hosts-file line), `https://<site-name>/django-admin/`
   answers 404, the same page as any unknown address.
