"""Lint rules for the Podman Quadlet units that run STAGE and LIVE (ARC.5, ADR-0005)."""

import mimetypes
import re
from pathlib import Path

import pytest

from apps.core.media import CACHE_CONTROL, MEDIA_FOLDERS, MEDIA_TYPES, RASTER_CSP, SVG_CSP

ROOT = Path(__file__).resolve().parents[2]
QUADLET_DIR = ROOT / "deploy" / "quadlet"

SECRET_LIKE = re.compile(r"SECRET|PASSW|TOKEN|CREDENTIAL|PRIVATE|(^|_)KEY($|_)", re.IGNORECASE)
PINNED_IMAGE = re.compile(r"^[\w.\-/]+:[\w.\-]+@sha256:[0-9a-f]{64}$")
APP_IMAGE = "localhost/mpmv-app:current"
APP_UNITS = ("mpmv-app.container", "mpmv-worker.container", "mpmv-migrate.container")
LONG_RUNNING = ("mpmv-app.container", "mpmv-worker.container", "mpmv-caddy.container")

Unit = dict[str, list[tuple[str, str]]]


def parse_unit(text: str) -> Unit:
    """Parse a systemd/Quadlet unit; keys may repeat (Environment=, Volume=...)."""
    unit: Unit = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            unit.setdefault(section, [])
            continue
        key, _, value = line.partition("=")
        unit.setdefault(section, []).append((key.strip(), value.strip()))
    return unit


def values(unit: Unit, section: str, key: str) -> list[str]:
    return [value for k, value in unit.get(section, []) if k == key]


def load(name: str) -> Unit:
    return parse_unit((QUADLET_DIR / name).read_text())


def container_units() -> list[str]:
    return sorted(path.name for path in QUADLET_DIR.glob("*.container"))


def test_parser_keeps_repeated_keys() -> None:
    unit = parse_unit("[Container]\n# c\nEnvironment=A=1\nEnvironment=B=2\n")
    assert values(unit, "Container", "Environment") == ["A=1", "B=2"]


def test_expected_units_exist() -> None:
    names = {path.name for path in QUADLET_DIR.iterdir()}
    assert {*APP_UNITS, "mpmv-caddy.container", "mpmv.network"} <= names


@pytest.mark.parametrize("name", APP_UNITS)
def test_app_units_run_the_current_release_image(name: str) -> None:
    unit = load(name)
    assert values(unit, "Container", "Image") == [APP_IMAGE]
    assert values(unit, "Container", "Pull") == ["never"]


@pytest.mark.parametrize("name", APP_UNITS)
def test_app_units_read_secrets_from_an_environment_file(name: str) -> None:
    unit = load(name)
    assert values(unit, "Container", "EnvironmentFile")


def test_every_container_follows_the_hardening_rules() -> None:
    names = container_units()
    assert names
    for name in names:
        unit = load(name)
        container_name = values(unit, "Container", "ContainerName")
        assert container_name, name
        assert container_name[0].startswith("mpmv-"), name
        assert values(unit, "Container", "Network") == ["mpmv.network"], name
        assert values(unit, "Container", "NoNewPrivileges") == ["true"], name
        assert values(unit, "Container", "ReadOnly") == ["true"], name
        for user in values(unit, "Container", "User"):
            assert user.split(":")[0] not in {"root", "0"}, name
        image = values(unit, "Container", "Image")[0]
        assert image == APP_IMAGE or PINNED_IMAGE.match(image), f"{name}: unpinned {image}"


def test_no_secret_like_inline_environment() -> None:
    for name in container_units():
        for assignment in values(load(name), "Container", "Environment"):
            variable = assignment.split("=", 1)[0]
            assert not SECRET_LIKE.search(variable), f"{name}: {variable} belongs in the env file"


def test_app_has_a_health_check_and_is_not_published() -> None:
    unit = load("mpmv-app.container")
    assert any("mpmv.healthcheck" in cmd for cmd in values(unit, "Container", "HealthCmd"))
    assert not values(unit, "Container", "PublishPort"), "only Caddy faces the network"


def test_worker_runs_the_worker_role() -> None:
    unit = load("mpmv-worker.container")
    assert values(unit, "Container", "Exec") == ["worker"]


def test_migrations_run_as_a_one_shot_step() -> None:
    unit = load("mpmv-migrate.container")
    assert values(unit, "Container", "Exec") == ["migrate"]
    assert values(unit, "Service", "Type") == ["oneshot"]
    assert "Install" not in unit, "migrations run only from the deploy script"


@pytest.mark.parametrize("name", LONG_RUNNING)
def test_long_running_services_restart_and_start_at_boot(name: str) -> None:
    unit = load(name)
    assert values(unit, "Service", "Restart") in (["always"], ["on-failure"])
    assert values(unit, "Install", "WantedBy") == ["default.target"]


def test_network_and_volume_names_are_prefixed() -> None:
    assert values(load("mpmv.network"), "Network", "NetworkName") == ["mpmv-net"]
    for path in QUADLET_DIR.glob("*.volume"):
        names = values(parse_unit(path.read_text()), "Volume", "VolumeName")
        assert names, path.name
        assert names[0].startswith("mpmv-"), path.name


def env_example() -> dict[str, str]:
    lines = (ROOT / "deploy" / "server" / "mpmv.env.example").read_text().splitlines()
    pairs = (line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))
    return {key.strip(): value.strip() for key, value in pairs}


def test_the_app_trusts_the_client_ip_header_only_from_the_proxy_network() -> None:
    """CORE.5: behind Caddy every request comes from Caddy's address. The app must read the
    client IP from the header Caddy overwrites, and only from the mpmv-net subnet."""
    env = env_example()
    subnets = values(load("mpmv.network"), "Network", "Subnet")
    assert subnets
    assert env["MPMV_TRUSTED_PROXIES"].split(",") == subnets
    header = env["MPMV_TRUSTED_PROXY_HEADER"]
    caddyfile = (ROOT / "deploy" / "caddy" / "Caddyfile").read_text()
    assert re.search(rf"^\s*header_up {re.escape(header)} \{{client_ip\}}$", caddyfile, re.M)


DB_ROOT_CA = "/etc/mpmv/pg-root-ca.crt"


@pytest.mark.parametrize("name", APP_UNITS)
def test_app_units_read_the_environment_then_the_database_connection(name: str) -> None:
    """ARC.13: the database setup writes DATABASE_URL into its own file; it is read last."""
    files = values(load(name), "Container", "EnvironmentFile")
    assert files == ["%h/.config/mpmv/mpmv.env", "%h/.config/mpmv/database.env"]


@pytest.mark.parametrize("name", APP_UNITS)
def test_app_units_mount_the_database_root_ca_read_only(name: str) -> None:
    """MQ-11: sslmode=verify-full against the internal CA, whose root the container mounts."""
    mounts = [v.split(":") for v in values(load(name), "Container", "Volume")]
    ca = [m for m in mounts if len(m) == 3 and m[1] == DB_ROOT_CA]
    assert len(ca) == 1, mounts
    assert "ro" in ca[0][2].split(",")


def test_database_url_lives_only_in_the_database_env_example() -> None:
    server = ROOT / "deploy" / "server"
    assert "DATABASE_URL" not in env_example(), "with two env files the later one wins"
    database = (server / "database.env.example").read_text()
    assert re.search(r"^DATABASE_URL=$", database, re.M)
    assert f"sslrootcert={DB_ROOT_CA}" in database
    assert "sslmode=verify-full" in database


CADDYFILE = ROOT / "deploy" / "caddy" / "Caddyfile"


def caddy_sites() -> list[str]:
    """Top-level block headers of the Caddyfile (an empty string is the global options block)."""
    heads = []
    for line in CADDYFILE.read_text().splitlines():
        if line and not line[0].isspace() and line.rstrip().endswith("{"):
            heads.append(line.rstrip()[:-1].strip())
    return heads


def test_caddy_serves_only_the_configured_site_name() -> None:
    """ARC.13: the host name comes from the environment; any other name or an IP gets no site."""
    assert caddy_sites() == [
        "",
        "(security_headers)",
        "{$MPMV_SITE_ADDRESS}",
        "http://{$MPMV_SITE_ADDRESS}",
        "http://",
    ]
    text = CADDYFILE.read_text()
    catch_all = re.search(r"^http:// \{\n(.*?)\n\}$", text, re.M | re.S)
    assert catch_all, "a catch-all for plain HTTP"
    body = [line for line in catch_all.group(1).splitlines() if not line.lstrip().startswith("#")]
    assert body == ["\tbind fd/3 fd/4 {", "\t\tprotocols h1", "\t}", "\tabort"], (
        "catch-all must serve nothing"
    )
    assert re.search(r"^\t\tstrict_sni_host on$", text, re.M)
    assert "tls internal" not in text, "Let's Encrypt only"
    assert "on_demand" not in text, "Let's Encrypt only, for the configured name"


def test_caddy_noindex_header_is_behind_a_flag_that_is_off_by_default() -> None:
    text = CADDYFILE.read_text()
    flag = re.search(r"^\t@noindex expression `\{env\.MPMV_NOINDEX\} in \[(.*)\]`$", text, re.M)
    assert flag, "X-Robots-Tag must depend on MPMV_NOINDEX"
    assert '""' not in flag.group(1), "an empty or unset flag means off (LIVE)"
    assert re.search(r'^\theader @noindex X-Robots-Tag "noindex, nofollow"$', text, re.M)
    example = (ROOT / "deploy" / "server" / "caddy.env.example").read_text()
    assert re.search(r"^MPMV_NOINDEX=$", example, re.M)
    assert re.search(r"^MPMV_SITE_ADDRESS=$", example, re.M)


MEDIA_UNITS = ("mpmv-app.container", "mpmv-worker.container")


def mounts_of(name: str) -> list[list[str]]:
    return [v.split(":") for v in values(load(name), "Container", "Volume")]


def test_media_volume_is_named_and_prefixed() -> None:
    """ARC.13: uploads and renditions survive container replacement, until object storage."""
    unit = load("mpmv-media.volume")
    assert values(unit, "Volume", "VolumeName") == ["mpmv-media"]
    assert values(unit, "Volume", "User") == ["10001"], "the image user (Containerfile)"
    assert values(unit, "Volume", "Group") == ["10001"]


@pytest.mark.parametrize("name", MEDIA_UNITS)
def test_app_and_worker_write_media_only_to_the_media_volume(name: str) -> None:
    """The root file system stays read-only; MEDIA_ROOT is the one writable mount, no exec."""
    media = [m for m in mounts_of(name) if m[0] == "mpmv-media.volume"]
    assert len(media) == 1, mounts_of(name)
    assert media[0][1] == "/app/media"
    options = set(media[0][2].split(",")) if len(media[0]) == 3 else set()
    assert "ro" not in options
    assert {"noexec", "nosuid", "nodev"} <= options
    assert "U" not in options, "ownership comes from the volume unit, no chown on every start"


def test_caddy_mounts_media_read_only() -> None:
    media = [m for m in mounts_of("mpmv-caddy.container") if m[0] == "mpmv-media.volume"]
    assert len(media) == 1
    assert media[0][1] == "/srv/media"
    assert set(media[0][2].split(",")) == {"ro", "noexec", "nosuid", "nodev"}


PUBLIC_MEDIA = re.compile(r"^\t@public_media path_regexp public_media (\S+)$", re.M)


def public_media_regex() -> re.Pattern[str]:
    found = PUBLIC_MEDIA.search(CADDYFILE.read_text())
    assert found, "public media must be an explicit allow-list"
    # Caddy uses Go RE2; this pattern is also valid Python syntax.
    return re.compile(found.group(1))


@pytest.mark.parametrize(
    "path",
    [
        "/media/images/demo-home-hero.width-1600.webp",
        "/media/images/logo.svg",
        "/media/original_images/photo.JPG",
        "/media/original_images/photo.avif",
    ],
)
def test_caddy_serves_image_files_from_the_image_folders(path: str) -> None:
    assert public_media_regex().match(path)


@pytest.mark.parametrize(
    "path",
    [
        "/media/documents/contract.pdf",
        "/media/documents/image.png",
        "/media/images/page.html",
        "/media/images/noext",
        "/media/images/a.webp.html",
        "/media/images/sub/dir.webp",
        "/media/images/",
        "/media/kyc/id.jpg",
        "/media/original_images/../documents/x.png",
    ],
)
def test_caddy_never_serves_other_media_paths(path: str) -> None:
    """lane-b review of #152: documents and private uploads must not become public by path."""
    assert not public_media_regex().match(path)


def test_caddy_serves_media_as_inert_static_files() -> None:
    """SEC.4: nothing under /media/ is executed, sniffed or able to run scripts."""
    text = CADDYFILE.read_text()
    block = re.search(r"^\thandle @public_media \{\n(.*?)\n\t\}$", text, re.M | re.S)
    assert block, "allowed media must be served by Caddy from the volume"
    body = block.group(1)
    assert re.search(r"^\t\turi strip_prefix /media$", body, re.M)
    assert re.search(r"^\t\troot \* /srv/media$", body, re.M)
    assert re.search(r"^\t\tfile_server$", body, re.M), "no browse, no templates"
    for directive in ("browse", "templates", "php"):
        assert directive not in body
    assert re.search(r"^\t\theader X-Content-Type-Options nosniff$", body, re.M)
    assert re.search(rf"^\t\theader Cache-Control \"{re.escape(CACHE_CONTROL)}\"$", body, re.M)
    assert re.search(r"^\thandle /media/\* \{\n\t\trespond 404\n\t\}$", text, re.M), (
        "every other media path must be a 404, never the app or file_server"
    )
    assert re.search(r"^\thandle \{\n\t\treverse_proxy mpmv-app:8000 \{$", text, re.M)


def public_media_block() -> str:
    block = re.search(
        r"^\thandle @public_media \{\n(.*?)\n\t\}$", CADDYFILE.read_text(), re.M | re.S
    )
    assert block, "allowed media must be served by Caddy from the volume"
    return block.group(1)


def test_caddy_media_folders_match_the_app_view() -> None:
    """THM.12/ARC.13 parity: Caddy serves exactly the folders apps.core.media serves."""
    folders = re.search(r"\^/media/\(([^)]*)\)/", public_media_regex().pattern)
    assert folders, public_media_regex().pattern
    assert {f"{name}/" for name in folders.group(1).split("|")} == set(MEDIA_FOLDERS)


CANDIDATE_EXTENSIONS = {
    *MEDIA_TYPES,
    *mimetypes.types_map,
    ".htm",
    ".svgz",
    ".jpe",
    ".jfif",
    ".pjpeg",
    ".heic",
    ".tif",
    ".bmp",
    ".ico",
    ".xml",
    ".js",
    ".pdf",
}


@pytest.mark.parametrize("extension", sorted(CANDIDATE_EXTENSIONS))
def test_caddy_media_extensions_match_the_app_view(extension: str) -> None:
    """Caddy serves an extension (any case) if and only if apps.core.media does."""
    pattern = public_media_regex()
    allowed = extension in MEDIA_TYPES
    for folder in MEDIA_FOLDERS:
        for spelling in (extension, extension.upper()):
            assert bool(pattern.match(f"/media/{folder}file{spelling}")) is allowed, spelling


def header_lines(body: str, name: str) -> list[tuple[str, str]]:
    """(matcher, value) for every `header [@matcher] <name> "<value>"` line in a block."""
    found = re.findall(rf"^\t\theader (?:(@\w+) )?{name} \"([^\"]*)\"$", body, re.M)
    return [(matcher, value) for matcher, value in found]


def test_caddy_media_csp_matches_the_app_view() -> None:
    """lane-b re-check of #152: two exclusive CSP headers, SVG and everything else, with the
    same values as apps.core.media. An unmatched CSP would overwrite the SVG one, because the
    Caddyfile adapter runs a header with a matcher before the header without one."""
    body = public_media_block()
    assert sorted(header_lines(body, "Content-Security-Policy")) == sorted(
        [("@not_svg", RASTER_CSP), ("@svg", SVG_CSP)]
    )
    assert re.search(r"^\t\t@svg path_regexp svg \(\?i\)\\\.svg\$$", body, re.M)
    assert re.search(r"^\t\t@not_svg not path_regexp \(\?i\)\\\.svg\$$", body, re.M)
    assert [ext for ext, kind in MEDIA_TYPES.items() if kind == "image/svg+xml"] == [".svg"]


def test_caddy_media_svg_matchers_are_exclusive() -> None:
    """Every allowed path matches exactly one of @svg and @not_svg."""
    body = public_media_block()
    svg = re.search(r"^\t\t@svg path_regexp svg (\S+)$", body, re.M)
    assert svg
    svg_pattern = re.compile(svg.group(1))
    for folder in MEDIA_FOLDERS:
        for extension in MEDIA_TYPES:
            for spelling in (extension, extension.upper()):
                path = f"/{folder}logo{spelling}"
                assert bool(svg_pattern.search(path)) is (extension == ".svg"), path


def test_caddy_media_cache_control_matches_the_app_view() -> None:
    assert header_lines(public_media_block(), "Cache-Control") == [("", CACHE_CONTROL)]


# --- Caddy behind systemd socket activation (OPS.5 follow-up): the proxy sees the client address.

SOCKET_UNIT = ROOT / "deploy" / "systemd" / "mpmv-caddy.socket"
# (directive, address) in fd order: fd 3 is the first line. The Caddyfile binds by these numbers.
EXPECTED_LISTENERS = [
    ("ListenStream", "0.0.0.0:80"),
    ("ListenStream", "[::]:80"),
    ("ListenStream", "0.0.0.0:443"),
    ("ListenStream", "[::]:443"),
    ("ListenDatagram", "0.0.0.0:443"),
    ("ListenDatagram", "[::]:443"),
]
FIRST_FD = 3  # sd_listen_fds(3): 0-2 are stdio


def socket_listeners() -> list[tuple[str, str]]:
    unit = parse_unit(SOCKET_UNIT.read_text())
    return [(k, v) for k, v in unit["Socket"] if k.startswith("Listen")]


def fds_for(directive: str, port: str) -> list[str]:
    """`fd/N` (stream) or `fdgram/N` (datagram) of the socket unit's listeners on a port."""
    prefix = "fd" if directive == "ListenStream" else "fdgram"
    return [
        f"{prefix}/{FIRST_FD + index}"
        for index, (kind, address) in enumerate(socket_listeners())
        if kind == directive and address.rsplit(":", 1)[1] == port
    ]


def binds(head: str) -> list[tuple[list[str], list[str]]]:
    """(addresses, protocols) of every `bind` in a top-level Caddyfile block."""
    block = re.search(rf"^{re.escape(head)} \{{\n(.*?)\n\}}$", CADDYFILE.read_text(), re.M | re.S)
    assert block, head
    found = re.findall(r"^\tbind ([^{\n]+) \{\n\t\tprotocols ([^\n]+)\n\t\}$", block.group(1), re.M)
    return [(addresses.split(), protocols.split()) for addresses, protocols in found]


def test_the_socket_unit_listens_on_80_and_443_per_address_family_in_a_fixed_order() -> None:
    assert socket_listeners() == EXPECTED_LISTENERS
    unit = parse_unit(SOCKET_UNIT.read_text())
    # IPv4 clients keep their IPv4 address (127.0.0.1 through an SSH tunnel), never ::ffff:...
    assert values(unit, "Socket", "BindIPv6Only") == ["ipv6-only"]
    assert values(unit, "Install", "WantedBy") == ["sockets.target"]
    assert not values(unit, "Socket", "Accept"), "one Caddy for all connections"
    assert not values(unit, "Socket", "Service"), "activates the unit of the same name"


def test_the_socket_unit_is_not_a_quadlet_unit() -> None:
    """Quadlet converts only its own unit types; the socket goes to ~/.config/systemd/user."""
    assert not list(QUADLET_DIR.glob("*.socket"))
    assert values(load("mpmv-caddy.container"), "Container", "ContainerName") == ["mpmv-caddy"]
    assert SOCKET_UNIT.stem == "mpmv-caddy", "same name as the service Quadlet generates"


def test_caddy_publishes_no_port_and_needs_its_socket_unit() -> None:
    """Rootless Podman's port forwarder replaces the client address with the container's own."""
    unit = load("mpmv-caddy.container")
    assert not values(unit, "Container", "PublishPort")
    assert values(unit, "Container", "Network") == ["mpmv.network"], "reaches the app by name"
    assert values(unit, "Unit", "Requires") == ["mpmv-caddy.socket"]
    assert "mpmv-caddy.socket" in values(unit, "Unit", "After")[0].split()


def test_the_caddyfile_binds_the_socket_fds_in_the_unit_order() -> None:
    http = [(fds_for("ListenStream", "80"), ["h1"])]
    assert fds_for("ListenStream", "80") == ["fd/3", "fd/4"]
    assert binds("http://{$MPMV_SITE_ADDRESS}") == http
    assert binds("http://") == http
    assert binds("{$MPMV_SITE_ADDRESS}") == [
        (fds_for("ListenStream", "443"), ["h1", "h2"]),
        (fds_for("ListenDatagram", "443"), ["h3"]),
    ]
    assert fds_for("ListenStream", "443") == ["fd/5", "fd/6"]
    assert fds_for("ListenDatagram", "443") == ["fdgram/7", "fdgram/8"]


def test_the_caddyfile_documents_the_fd_order_of_the_socket_unit() -> None:
    text = CADDYFILE.read_text()
    socket = SOCKET_UNIT.read_text()
    for index, (kind, address) in enumerate(EXPECTED_LISTENERS):
        fd = FIRST_FD + index
        proto = "TCP" if kind == "ListenStream" else "UDP"
        name = f"fd/{fd}" if proto == "TCP" else f"fdgram/{fd}"
        assert re.search(rf"^#   {re.escape(name)} +{proto} {re.escape(address)}", text, re.M), name
        assert re.search(rf"^#   fd {fd}  {proto} {re.escape(address)}", socket, re.M), fd


def test_caddy_opens_no_listener_of_its_own() -> None:
    """Every site binds a socket fd, and Caddy's automatic redirect, which would listen on port 80
    inside the container where nobody reaches it, is off (the site has its own redirect)."""
    text = CADDYFILE.read_text()
    assert re.search(r"^\tauto_https disable_redirects$", text, re.M)
    for head in caddy_sites():
        if head and not head.startswith("("):
            assert binds(head), f"{head} must bind the socket fds"
