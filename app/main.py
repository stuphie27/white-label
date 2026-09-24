from pathlib import Path
import json
import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "stuphie_online.db"
BRANDS_PATH = BASE_DIR / "config" / "brand_profiles.json"

app = FastAPI(title="Stuphie Online")

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "app" / "static"),
    name="static",
)

templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_brands():
    with open(BRANDS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["brands"]


def get_brand_profile(brand_id):
    """
    Return the white-label brand profile for an event.

    Customer-facing pages must use the event's saved brand_id,
    never the staff dashboard's currently selected brand.
    """
    brands = load_brands()

    for brand in brands:
        if brand.get("brand_id") == brand_id:
            return brand

    # Safe fallback for older/unbranded events.
    for brand in brands:
        if brand.get("brand_id") == "sophies":
            return brand

    return brands[0] if brands else {}


def get_event_brand(event):
    """
    Resolve the customer-facing brand belonging to this event.
    """
    try:
        brand_id = event["brand_id"]
    except (KeyError, IndexError, TypeError):
        brand_id = "sophies"

    return get_brand_profile(brand_id)


def customer_cookie_name(public_slug):
    """
    Event-specific Stuphie customer session cookie.
    """
    return f"stuphie_gallery_{public_slug}"


def legacy_customer_cookie_name(public_slug):
    """
    Temporary compatibility with early DSI test sessions.
    Can be removed once migration is complete.
    """
    return f"dsi_gallery_{public_slug}"


def get_customer_session_token(request, public_slug):
    """
    Prefer the Stuphie cookie, but fall back to the legacy DSI cookie
    so existing test favourites and baskets are preserved.
    """
    return (
        request.cookies.get(
            customer_cookie_name(public_slug)
        )
        or request.cookies.get(
            legacy_customer_cookie_name(public_slug)
        )
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "app": "Stuphie Online"
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    conn = db()

    current_brand_id = conn.execute(
        "SELECT current_brand_id FROM staff_settings WHERE id = 1"
    ).fetchone()["current_brand_id"]

    current_event = conn.execute(
        """
        SELECT *
        FROM events
        ORDER BY event_date DESC, id DESC
        LIMIT 1
        """
    ).fetchone()

    stats = {
        "photos": conn.execute(
            "SELECT COUNT(*) AS c FROM photos"
        ).fetchone()["c"],
        "preview_ready": conn.execute(
            "SELECT COUNT(*) AS c FROM photos WHERE preview_status='ready'"
        ).fetchone()["c"],
        "waiting_upload": conn.execute(
            "SELECT COUNT(*) AS c FROM photos WHERE upload_status='pending'"
        ).fetchone()["c"],
        "uploaded": conn.execute(
            "SELECT COUNT(*) AS c FROM photos WHERE upload_status='uploaded'"
        ).fetchone()["c"],
        "failed": conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM photos
            WHERE preview_status='failed'
               OR upload_status='failed'
            """
        ).fetchone()["c"],
        "sessions": conn.execute(
            "SELECT COUNT(*) AS c FROM customer_sessions"
        ).fetchone()["c"],
        "favourites": conn.execute(
            "SELECT COUNT(*) AS c FROM favourites"
        ).fetchone()["c"],
        "baskets": conn.execute(
            "SELECT COUNT(*) AS c FROM baskets WHERE status='open'"
        ).fetchone()["c"],
        "orders": conn.execute(
            "SELECT COUNT(*) AS c FROM orders"
        ).fetchone()["c"],
        "paid_orders": conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE payment_status='paid'"
        ).fetchone()["c"],
        "deliveries": conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM deliveries
            WHERE status IN ('pending','processing','failed')
            """
        ).fetchone()["c"],
        "sales_total": conn.execute(
            """
            SELECT COALESCE(SUM(total),0) AS total
            FROM orders
            WHERE payment_status='paid'
            """
        ).fetchone()["total"],
    }

    conn.close()

    brands = load_brands()

    current_brand = next(
        (
            brand
            for brand in brands
            if brand["brand_id"] == current_brand_id
        ),
        brands[0] if brands else {},
    )

    return templates.TemplateResponse(
        request=request,
        name="staff/dashboard.html",
        context={
            "current_brand_id": current_brand_id,
            "current_brand": current_brand,
            "current_event": current_event,
            "brands": brands,
            "stats": stats,
        },
    )


from fastapi.responses import RedirectResponse


@app.post("/staff/brand/{brand_id}")
def switch_staff_brand(brand_id: str):
    brands = load_brands()
    valid_brand_ids = {brand["brand_id"] for brand in brands}

    if brand_id not in valid_brand_ids:
        return RedirectResponse(url="/", status_code=303)

    conn = db()

    conn.execute(
        """
        UPDATE staff_settings
        SET current_brand_id = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
        """,
        (brand_id,),
    )

    conn.commit()
    conn.close()

    return RedirectResponse(url="/", status_code=303)


import re
from fastapi import Form


def make_slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


@app.get("/events/new", response_class=HTMLResponse)
def new_event_form(request: Request):
    conn = db()

    current_brand_id = conn.execute(
        "SELECT current_brand_id FROM staff_settings WHERE id = 1"
    ).fetchone()["current_brand_id"]

    conn.close()

    brands = load_brands()
    current_brand = next(
        brand for brand in brands
        if brand["brand_id"] == current_brand_id
    )

    return templates.TemplateResponse(
        request=request,
        name="staff/create_event.html",
        context={
            "current_brand": current_brand,
            "error": None,
        },
    )


@app.post("/events/new", response_class=HTMLResponse)
def create_event(
    request: Request,
    event_name: str = Form(...),
    event_date: str = Form(...),
    source_folder: str = Form(...),
    public_slug: str = Form(""),
    visibility: str = Form("private"),
):
    conn = db()

    current_brand_id = conn.execute(
        "SELECT current_brand_id FROM staff_settings WHERE id = 1"
    ).fetchone()["current_brand_id"]

    brands = load_brands()
    current_brand = next(
        brand for brand in brands
        if brand["brand_id"] == current_brand_id
    )

    event_name = event_name.strip()
    source_folder = source_folder.strip()

    if not event_name:
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="staff/create_event.html",
            context={
                "current_brand": current_brand,
                "error": "Event name is required.",
            },
            status_code=400,
        )

    if not source_folder:
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="staff/create_event.html",
            context={
                "current_brand": current_brand,
                "error": "Photo source folder is required.",
            },
            status_code=400,
        )

    # Real event photographs must live on an externally mounted drive.
    source_path = Path(source_folder).expanduser()

    if not source_path.is_absolute() or not str(source_path).startswith("/Volumes/"):
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="staff/create_event.html",
            context={
                "current_brand": current_brand,
                "error": "Please choose a photo folder on the external event drive. Stuphie will not store event data on this Mac.",
            },
            status_code=400,
        )

    if not source_path.exists() or not source_path.is_dir():
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="staff/create_event.html",
            context={
                "current_brand": current_brand,
                "error": "The selected photo folder cannot be found. Check that the event drive is connected.",
            },
            status_code=400,
        )

    # /Volumes/DRIVE NAME/...
    parts = source_path.parts

    if len(parts) < 3:
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="staff/create_event.html",
            context={
                "current_brand": current_brand,
                "error": "Stuphie could not identify the external event drive.",
            },
            status_code=400,
        )

    volume_root = Path("/Volumes") / parts[2]

    safe_event_folder = re.sub(
        r"[^A-Za-z0-9 _.-]+",
        "",
        event_name
    ).strip()

    if not safe_event_folder:
        safe_event_folder = "Event"

    event_root_path = volume_root / "Stuphie Online" / safe_event_folder

    # Everything generated for this event stays on its event drive.
    required_folders = [
        event_root_path,
        event_root_path / "previews",
        event_root_path / "deliveries",
        event_root_path / "deliveries" / "social",
        event_root_path / "deliveries" / "full-resolution",
        event_root_path / "processing",
        event_root_path / "logs",
        event_root_path / "event-data",
    ]

    try:
        for folder in required_folders:
            folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="staff/create_event.html",
            context={
                "current_brand": current_brand,
                "error": f"Stuphie could not create its event folders on the external drive: {exc}",
            },
            status_code=400,
        )

    event_root = str(event_root_path)

    slug = make_slug(public_slug or event_name)

    if not slug:
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="staff/create_event.html",
            context={
                "current_brand": current_brand,
                "error": "A valid public gallery address could not be created.",
            },
            status_code=400,
        )

    if visibility not in {"private", "live"}:
        visibility = "private"

    existing = conn.execute(
        "SELECT id FROM events WHERE public_slug = ?",
        (slug,),
    ).fetchone()

    if existing:
        conn.close()
        return templates.TemplateResponse(
            request=request,
            name="staff/create_event.html",
            context={
                "current_brand": current_brand,
                "error": f"The gallery address '{slug}' is already being used.",
            },
            status_code=400,
        )

    conn.execute(
        """
        INSERT INTO events (
            event_name,
            event_date,
            brand_id,
            source_folder,
            event_root,
            public_slug,
            visibility
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_name,
            event_date,
            current_brand_id,
            source_folder,
            event_root,
            slug,
            visibility,
        ),
    )

    conn.commit()
    conn.close()

    return RedirectResponse(url="/", status_code=303)


import subprocess
from fastapi.responses import JSONResponse


@app.post("/api/choose-source-folder")
def choose_source_folder():
    script = '''
    set chosenFolder to choose folder with prompt "Choose the event photo folder on the external hard drive"
    return POSIX path of chosenFolder
    '''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc)},
            status_code=500,
        )

    if result.returncode != 0:
        return {
            "ok": False,
            "cancelled": True
        }

    path = result.stdout.strip().rstrip("/")

    if not path.startswith("/Volumes/"):
        return JSONResponse(
            {
                "ok": False,
                "error": "Please choose a folder on the external event hard drive."
            },
            status_code=400,
        )

    return {
        "ok": True,
        "path": path
    }


from fastapi.responses import FileResponse, Response


def get_event_by_slug(public_slug: str):
    conn = db()

    event = conn.execute(
        """
        SELECT *
        FROM events
        WHERE public_slug = ?
        LIMIT 1
        """,
        (public_slug,),
    ).fetchone()

    conn.close()
    return event


def open_event_database(event):
    if not event:
        raise RuntimeError("Event not found.")

    event_root = event["event_root"]

    if not event_root:
        raise RuntimeError("Event storage location is missing.")

    event_root_path = Path(event_root)

    if not event_root_path.exists():
        raise RuntimeError(
            "The event hard drive is not connected."
        )

    event_db_path = (
        event_root_path
        / "event-data"
        / "event.db"
    )

    if not event_db_path.exists():
        raise RuntimeError(
            "The event database cannot be found."
        )

    conn = sqlite3.connect(event_db_path)
    conn.row_factory = sqlite3.Row

    return conn


@app.get("/gallery/{public_slug}", response_class=HTMLResponse)
def customer_gallery(
    request: Request,
    public_slug: str,
    folder: str = "",
):
    event = get_event_by_slug(public_slug)

    if not event:
        return HTMLResponse(
            "Gallery not found.",
            status_code=404,
        )

    try:
        event_conn = open_event_database(event)
    except RuntimeError as exc:
        return HTMLResponse(
            f"Gallery temporarily unavailable: {exc}",
            status_code=503,
        )

    event_info = event_conn.execute(
        """
        SELECT *
        FROM event_info
        WHERE id = 1
        """
    ).fetchone()

    all_photos = event_conn.execute(
        """
        SELECT
            id,
            original_filename,
            preview_path,
            preview_status,
            upload_status,
            remote_preview_key,
            relative_folder
        FROM photos
        ORDER BY relative_folder, id
        """
    ).fetchall()

    event_conn.close()

    current_folder = folder.strip("/")

    subfolders = set()
    photos = []

    for photo in all_photos:
        rel = (photo["relative_folder"] or "").strip("/")

        if current_folder:
            prefix = current_folder + "/"

            if rel == current_folder:
                if (
                    photo["preview_status"] == "ready"
                    and photo["upload_status"] == "uploaded"
                    and photo["remote_preview_key"]
                ):
                    photos.append(photo)
                continue

            if not rel.startswith(prefix):
                continue

            remainder = rel[len(prefix):]
        else:
            remainder = rel

            if rel == "":
                if (
                    photo["preview_status"] == "ready"
                    and photo["upload_status"] == "uploaded"
                    and photo["remote_preview_key"]
                ):
                    photos.append(photo)
                continue

        if remainder:
            first_part = remainder.split("/", 1)[0]

            if current_folder:
                child_path = current_folder + "/" + first_part
            else:
                child_path = first_part

            subfolders.add(child_path)

    subfolders = sorted(subfolders)

    breadcrumbs = []

    if current_folder:
        running = []

        for part in current_folder.split("/"):
            running.append(part)

            breadcrumbs.append({
                "name": part,
                "path": "/".join(running),
            })

    cookie_name = customer_cookie_name(public_slug)
    session_token = get_customer_session_token(
        request,
        public_slug,
    )

    favourite_ids = set()
    favourite_count = 0
    basket_count = 0
    session = None
    new_token = None

    event_conn = open_event_database(event)

    if session_token:
        session = event_conn.execute(
            """
            SELECT *
            FROM customer_sessions
            WHERE session_token = ?
            LIMIT 1
            """,
            (session_token,),
        ).fetchone()

    if session:
        rows = event_conn.execute(
            """
            SELECT photo_id
            FROM favourites
            WHERE session_id = ?
            """,
            (session["id"],),
        ).fetchall()

        favourite_ids = {
            row["photo_id"]
            for row in rows
        }

        favourite_count = len(favourite_ids)
        basket_count = get_open_basket_count(
            event_conn,
            session["id"],
        )

    event_conn.close()

    brand = get_event_brand(event)

    response = templates.TemplateResponse(
        request=request,
        name="customer/gallery.html",
        context={
            "event": event_info,
            "brand": brand,
            "photos": photos,
            "subfolders": subfolders,
            "current_folder": current_folder,
            "breadcrumbs": breadcrumbs,
            "favourite_ids": favourite_ids,
            "favourite_count": favourite_count,
            "basket_count": basket_count,
        },
    )

    return response


@app.get("/gallery/{public_slug}/preview/{photo_id}")
def customer_preview(
    public_slug: str,
    photo_id: int,
):
    event = get_event_by_slug(public_slug)

    if not event:
        return HTMLResponse(
            "Gallery not found.",
            status_code=404,
        )

    try:
        event_conn = open_event_database(event)
    except RuntimeError as exc:
        return HTMLResponse(
            f"Preview unavailable: {exc}",
            status_code=503,
        )

    photo = event_conn.execute(
        """
        SELECT
            preview_path,
            preview_status,
            upload_status,
            remote_preview_key
        FROM photos
        WHERE id = ?
        LIMIT 1
        """,
        (photo_id,),
    ).fetchone()

    event_conn.close()

    if not photo:
        return HTMLResponse(
            "Photograph not found.",
            status_code=404,
        )

    if photo["preview_status"] != "ready":
        return HTMLResponse(
            "Preview not ready.",
            status_code=404,
        )

    if (
        photo["upload_status"] == "uploaded"
        and photo["remote_preview_key"]
    ):
        try:
            from app.services.upload_processor import (
                load_spaces_config,
                spaces_client,
            )

            spaces_config = load_spaces_config()
            client = spaces_client(spaces_config)

            cloud_object = client.get_object(
                Bucket=spaces_config["STUPHIE_SPACES_BUCKET"],
                Key=photo["remote_preview_key"],
            )

            cloud_bytes = cloud_object["Body"].read()

            return Response(
                content=cloud_bytes,
                media_type="image/jpeg",
                headers={
                    "Cache-Control": "private, max-age=300",
                    "X-Stuphie-Preview-Source": "cloud",
                },
            )

        except Exception as exc:
            print(
                f"CLOUD PREVIEW FALLBACK photo={photo_id}: {exc}",
                flush=True,
            )

    preview_value = photo["preview_path"]

    if preview_value:
        preview_path = Path(preview_value)

        if preview_path.exists():
            return FileResponse(
                preview_path,
                media_type="image/jpeg",
                headers={
                    "Cache-Control": "private, max-age=300",
                    "X-Stuphie-Preview-Source": "local",
                },
            )

    return HTMLResponse(
        "Preview file is unavailable.",
        status_code=404,
    )


import secrets
from urllib.parse import urlencode


def get_or_create_customer_session(event_conn, session_token=None):
    session = None

    if session_token:
        session = event_conn.execute(
            """
            SELECT *
            FROM customer_sessions
            WHERE session_token = ?
            LIMIT 1
            """,
            (session_token,),
        ).fetchone()

    if session:
        event_conn.execute(
            """
            UPDATE customer_sessions
            SET last_seen_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (session["id"],),
        )
        event_conn.commit()
        return session, session_token, False

    token = secrets.token_urlsafe(32)

    cursor = event_conn.execute(
        """
        INSERT INTO customer_sessions (
            session_token
        )
        VALUES (?)
        """,
        (token,),
    )

    event_conn.commit()

    session = event_conn.execute(
        """
        SELECT *
        FROM customer_sessions
        WHERE id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()

    return session, token, True


@app.post("/gallery/{public_slug}/favourite/{photo_id}")
def toggle_favourite(
    request: Request,
    public_slug: str,
    photo_id: int,
    folder: str = Form(""),
):
    event = get_event_by_slug(public_slug)

    if not event:
        return HTMLResponse("Gallery not found.", status_code=404)

    try:
        event_conn = open_event_database(event)
    except RuntimeError as exc:
        return HTMLResponse(
            f"Gallery temporarily unavailable: {exc}",
            status_code=503,
        )

    cookie_name = customer_cookie_name(public_slug)
    token = get_customer_session_token(
        request,
        public_slug,
    )

    session, token, is_new = get_or_create_customer_session(
        event_conn,
        token,
    )

    photo = event_conn.execute(
        """
        SELECT id
        FROM photos
        WHERE id = ?
        LIMIT 1
        """,
        (photo_id,),
    ).fetchone()

    if not photo:
        event_conn.close()
        return HTMLResponse(
            "Photograph not found.",
            status_code=404,
        )

    favourite = event_conn.execute(
        """
        SELECT id
        FROM favourites
        WHERE session_id = ?
          AND photo_id = ?
        LIMIT 1
        """,
        (
            session["id"],
            photo_id,
        ),
    ).fetchone()

    if favourite:
        event_conn.execute(
            """
            DELETE FROM favourites
            WHERE id = ?
            """,
            (favourite["id"],),
        )
    else:
        event_conn.execute(
            """
            INSERT INTO favourites (
                session_id,
                photo_id
            )
            VALUES (?, ?)
            """,
            (
                session["id"],
                photo_id,
            ),
        )

    event_conn.commit()
    event_conn.close()

    query = urlencode({"folder": folder}) if folder else ""

    url = f"/gallery/{public_slug}"
    if query:
        url += f"?{query}"

    response = RedirectResponse(
        url=url,
        status_code=303,
    )

    if is_new:
        response.set_cookie(
            key=cookie_name,
            value=token,
            max_age=60 * 60 * 24 * 35,
            httponly=True,
            samesite="lax",
        )

    return response


@app.get("/gallery/{public_slug}/favourites", response_class=HTMLResponse)
def customer_favourites(
    request: Request,
    public_slug: str,
):
    event = get_event_by_slug(public_slug)

    if not event:
        return HTMLResponse("Gallery not found.", status_code=404)

    try:
        event_conn = open_event_database(event)
    except RuntimeError as exc:
        return HTMLResponse(
            f"Gallery temporarily unavailable: {exc}",
            status_code=503,
        )

    event_info = event_conn.execute(
        "SELECT * FROM event_info WHERE id = 1"
    ).fetchone()

    cookie_name = customer_cookie_name(public_slug)
    token = get_customer_session_token(
        request,
        public_slug,
    )

    photos = []
    basket_count = 0

    if token:
        session = event_conn.execute(
            """
            SELECT *
            FROM customer_sessions
            WHERE session_token = ?
            LIMIT 1
            """,
            (token,),
        ).fetchone()

        if session:
            basket_count = get_open_basket_count(
                event_conn,
                session["id"],
            )

            photos = event_conn.execute(
                """
                SELECT
                    p.id,
                    p.original_filename,
                    p.preview_path,
                    p.relative_folder
                FROM favourites f
                JOIN photos p ON p.id = f.photo_id
                WHERE f.session_id = ?
                  AND p.preview_status = 'ready'
                ORDER BY f.created_at DESC
                """,
                (session["id"],),
            ).fetchall()

    event_conn.close()

    brand = get_event_brand(event)

    return templates.TemplateResponse(
        request=request,
        name="customer/favourites.html",
        context={
            "event": event_info,
            "brand": brand,
            "photos": photos,
            "basket_count": basket_count,
        },
    )


@app.post("/api/gallery/{public_slug}/favourite/{photo_id}")
def api_toggle_favourite(
    request: Request,
    public_slug: str,
    photo_id: int,
):
    event = get_event_by_slug(public_slug)

    if not event:
        return JSONResponse(
            {"ok": False, "error": "Gallery not found."},
            status_code=404,
        )

    try:
        event_conn = open_event_database(event)
    except RuntimeError as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc)},
            status_code=503,
        )

    cookie_name = customer_cookie_name(public_slug)
    token = get_customer_session_token(
        request,
        public_slug,
    )

    session, token, is_new = get_or_create_customer_session(
        event_conn,
        token,
    )

    # First favourite requires customer identity so the session
    # can be recovered later from its secure return link.
    if not session["customer_email"]:
        event_conn.close()

        response = JSONResponse({
            "ok": True,
            "needs_details": True,
            "photo_id": photo_id,
        })

        if is_new:
            response.set_cookie(
                key=cookie_name,
                value=token,
                max_age=60 * 60 * 24 * 365,
                httponly=True,
                samesite="lax",
            )

        return response

    photo = event_conn.execute(
        """
        SELECT id
        FROM photos
        WHERE id = ?
        LIMIT 1
        """,
        (photo_id,),
    ).fetchone()

    if not photo:
        event_conn.close()
        return JSONResponse(
            {"ok": False, "error": "Photograph not found."},
            status_code=404,
        )

    favourite = event_conn.execute(
        """
        SELECT id
        FROM favourites
        WHERE session_id = ?
          AND photo_id = ?
        LIMIT 1
        """,
        (
            session["id"],
            photo_id,
        ),
    ).fetchone()

    selected = False

    if favourite:
        event_conn.execute(
            """
            DELETE FROM favourites
            WHERE id = ?
            """,
            (favourite["id"],),
        )
    else:
        event_conn.execute(
            """
            INSERT INTO favourites (
                session_id,
                photo_id
            )
            VALUES (?, ?)
            """,
            (
                session["id"],
                photo_id,
            ),
        )
        selected = True

    event_conn.commit()

    favourite_count = event_conn.execute(
        """
        SELECT COUNT(*)
        FROM favourites
        WHERE session_id = ?
        """,
        (session["id"],),
    ).fetchone()[0]

    event_conn.close()

    response = JSONResponse({
        "ok": True,
        "selected": selected,
        "count": favourite_count,
    })

    if is_new:
        response.set_cookie(
            key=cookie_name,
            value=token,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite="lax",
        )

    return response


@app.post("/api/gallery/{public_slug}/identify")
def identify_customer(
    request: Request,
    public_slug: str,
    customer_name: str = Form(...),
    customer_email: str = Form(...),
    photo_id: int = Form(...),
):
    event = get_event_by_slug(public_slug)

    if not event:
        return JSONResponse(
            {"ok": False, "error": "Gallery not found."},
            status_code=404,
        )

    customer_name = customer_name.strip()
    customer_email = customer_email.strip().lower()

    if not customer_name:
        return JSONResponse(
            {"ok": False, "error": "Please enter your name."},
            status_code=400,
        )

    if (
        not customer_email
        or "@" not in customer_email
        or "." not in customer_email.split("@")[-1]
    ):
        return JSONResponse(
            {"ok": False, "error": "Please enter a valid email address."},
            status_code=400,
        )

    try:
        event_conn = open_event_database(event)
    except RuntimeError as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc)},
            status_code=503,
        )

    cookie_name = customer_cookie_name(public_slug)
    token = get_customer_session_token(
        request,
        public_slug,
    )

    session, token, is_new = get_or_create_customer_session(
        event_conn,
        token,
    )

    event_conn.execute(
        """
        UPDATE customer_sessions
        SET
            customer_name = ?,
            customer_email = ?,
            last_seen_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            customer_name,
            customer_email,
            session["id"],
        ),
    )

    photo = event_conn.execute(
        """
        SELECT id
        FROM photos
        WHERE id = ?
        LIMIT 1
        """,
        (photo_id,),
    ).fetchone()

    if not photo:
        event_conn.close()
        return JSONResponse(
            {"ok": False, "error": "Photograph not found."},
            status_code=404,
        )

    event_conn.execute(
        """
        INSERT OR IGNORE INTO favourites (
            session_id,
            photo_id
        )
        VALUES (?, ?)
        """,
        (
            session["id"],
            photo_id,
        ),
    )

    event_conn.commit()

    favourite_count = event_conn.execute(
        """
        SELECT COUNT(*)
        FROM favourites
        WHERE session_id = ?
        """,
        (session["id"],),
    ).fetchone()[0]

    event_conn.close()

    secure_path = (
        f"/gallery/{public_slug}/open/{token}"
    )

    response = JSONResponse({
        "ok": True,
        "selected": True,
        "count": favourite_count,
        "secure_path": secure_path,
    })

    response.set_cookie(
        key=cookie_name,
        value=token,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="lax",
    )

    return response


@app.get("/gallery/{public_slug}/open/{session_token}")
def reopen_customer_session(
    public_slug: str,
    session_token: str,
):
    event = get_event_by_slug(public_slug)

    if not event:
        return HTMLResponse(
            "Gallery not found.",
            status_code=404,
        )

    try:
        event_conn = open_event_database(event)
    except RuntimeError as exc:
        return HTMLResponse(
            f"Gallery temporarily unavailable: {exc}",
            status_code=503,
        )

    session = event_conn.execute(
        """
        SELECT id
        FROM customer_sessions
        WHERE session_token = ?
        LIMIT 1
        """,
        (session_token,),
    ).fetchone()

    event_conn.close()

    if not session:
        return HTMLResponse(
            "This saved-gallery link is invalid.",
            status_code=404,
        )

    response = RedirectResponse(
        url=f"/gallery/{public_slug}/favourites",
        status_code=303,
    )

    response.set_cookie(
        key=customer_cookie_name(public_slug),
        value=session_token,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="lax",
    )

    return response


def get_open_basket_count(event_conn, session_id: int) -> int:
    """
    Return the number of items in this customer's open event basket.
    Works for every Stuphie white-label brand.
    """
    row = event_conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM basket_items bi
        JOIN baskets b
          ON b.id = bi.basket_id
        WHERE b.session_id = ?
          AND b.status = 'open'
        """,
        (session_id,),
    ).fetchone()

    return row["c"] if row else 0


def get_or_create_open_basket(event_conn, session_id: int):
    basket = event_conn.execute(
        """
        SELECT *
        FROM baskets
        WHERE session_id = ?
          AND status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()

    if basket:
        return basket

    cursor = event_conn.execute(
        """
        INSERT INTO baskets (
            session_id,
            status
        )
        VALUES (?, 'open')
        """,
        (session_id,),
    )

    event_conn.commit()

    return event_conn.execute(
        """
        SELECT *
        FROM baskets
        WHERE id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()


@app.post("/api/gallery/{public_slug}/basket/{photo_id}")
def add_to_basket(
    request: Request,
    public_slug: str,
    photo_id: int,
    product_type: str = Form(...),
):
    event = get_event_by_slug(public_slug)

    if not event:
        return JSONResponse(
            {"ok": False, "error": "Gallery not found."},
            status_code=404,
        )

    if product_type not in {"social", "full_resolution"}:
        return JSONResponse(
            {"ok": False, "error": "Invalid product type."},
            status_code=400,
        )

    try:
        event_conn = open_event_database(event)
    except RuntimeError as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc)},
            status_code=503,
        )

    cookie_name = customer_cookie_name(public_slug)
    token = get_customer_session_token(
        request,
        public_slug,
    )

    session, token, is_new = get_or_create_customer_session(
        event_conn,
        token,
    )

    photo = event_conn.execute(
        """
        SELECT id
        FROM photos
        WHERE id = ?
        LIMIT 1
        """,
        (photo_id,),
    ).fetchone()

    if not photo:
        event_conn.close()
        return JSONResponse(
            {"ok": False, "error": "Photograph not found."},
            status_code=404,
        )

    basket = get_or_create_open_basket(
        event_conn,
        session["id"],
    )

    # One product version per photo.
    event_conn.execute(
        """
        DELETE FROM basket_items
        WHERE basket_id = ?
          AND photo_id = ?
        """,
        (
            basket["id"],
            photo_id,
        ),
    )

    unit_price = (
        get_social_price(public_slug)
        if product_type == "social"
        else 22.00
    )

    event_conn.execute(
        """
        INSERT INTO basket_items (
            basket_id,
            photo_id,
            product_type,
            unit_price
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            basket["id"],
            photo_id,
            product_type,
            unit_price,
        ),
    )

    event_conn.execute(
        """
        UPDATE baskets
        SET updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (basket["id"],),
    )

    event_conn.commit()

    basket_count = event_conn.execute(
        """
        SELECT COUNT(*)
        FROM basket_items
        WHERE basket_id = ?
        """,
        (basket["id"],),
    ).fetchone()[0]

    event_conn.close()

    response = JSONResponse({
        "ok": True,
        "count": basket_count,
        "product_type": product_type,
    })

    if is_new:
        response.set_cookie(
            key=cookie_name,
            value=token,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite="lax",
        )

    return response


SOCIAL_BUNDLES = {
    5: 60.00,
    10: 120.00,
    15: 180.00,
    20: 240.00,
    25: 300.00,
}

FULLRES_BUNDLES = {
    5: 100.00,
    10: 190.00,
    15: 280.00,
    20: 370.00,
    25: 460.00,
}


def get_social_price(public_slug=None):
    if public_slug == "white-label-test-22-26":
        return 1.00

    return 14.00


def calculate_product_total(
    count,
    product_type,
    public_slug=None,
):

    if product_type == "social":
        single_price = get_social_price(
            public_slug
        )

        if public_slug == "white-label-test-22-26":
            bundles = {}
        else:
            bundles = SOCIAL_BUNDLES

    else:
        single_price = 22.00
        bundles = FULLRES_BUNDLES

    if count <= 0:
        return {
            "count": 0,
            "total": 0.00,
            "standard_total": 0.00,
            "saving": 0.00,
            "next_deal": None,
        }

    best_total = count * single_price

    for bundle_count, bundle_price in bundles.items():
        if count >= bundle_count:
            remainder = count - bundle_count
            candidate = (
                bundle_price
                + (remainder * single_price)
            )

            if candidate < best_total:
                best_total = candidate

    standard_total = count * single_price
    saving = standard_total - best_total

    next_deal = None

    for bundle_count, bundle_price in bundles.items():
        if bundle_count > count:
            next_deal = {
                "target": bundle_count,
                "needed": bundle_count - count,
                "price": bundle_price,
            }
            break

    return {
        "count": count,
        "total": round(best_total, 2),
        "standard_total": round(
            standard_total,
            2,
        ),
        "saving": round(saving, 2),
        "next_deal": next_deal,
    }


@app.get("/gallery/{public_slug}/basket", response_class=HTMLResponse)
def customer_basket(
    request: Request,
    public_slug: str,
):
    event = get_event_by_slug(public_slug)

    if not event:
        return HTMLResponse(
            "Gallery not found.",
            status_code=404,
        )

    try:
        event_conn = open_event_database(event)
    except RuntimeError as exc:
        return HTMLResponse(
            f"Basket temporarily unavailable: {exc}",
            status_code=503,
        )

    event_info = event_conn.execute(
        "SELECT * FROM event_info WHERE id = 1"
    ).fetchone()

    cookie_name = customer_cookie_name(public_slug)
    token = get_customer_session_token(
        request,
        public_slug,
    )

    items = []

    if token:
        session = event_conn.execute(
            """
            SELECT *
            FROM customer_sessions
            WHERE session_token = ?
            LIMIT 1
            """,
            (token,),
        ).fetchone()

        if session:
            basket = event_conn.execute(
                """
                SELECT *
                FROM baskets
                WHERE session_id = ?
                  AND status = 'open'
                ORDER BY id DESC
                LIMIT 1
                """,
                (session["id"],),
            ).fetchone()

            if basket:
                items = event_conn.execute(
                    """
                    SELECT
                        bi.id,
                        bi.photo_id,
                        bi.product_type,
                        bi.unit_price,
                        p.original_filename,
                        p.preview_path
                    FROM basket_items bi
                    JOIN photos p
                      ON p.id = bi.photo_id
                    WHERE bi.basket_id = ?
                    ORDER BY bi.created_at, bi.id
                    """,
                    (basket["id"],),
                ).fetchall()

    social_count = sum(
        1 for item in items
        if item["product_type"] == "social"
    )

    fullres_count = sum(
        1 for item in items
        if item["product_type"] == "full_resolution"
    )

    social_pricing = calculate_product_total(
        social_count,
        "social",
        public_slug,
    )

    fullres_pricing = calculate_product_total(
        fullres_count,
        "full_resolution",
    )

    total = (
        social_pricing["total"]
        + fullres_pricing["total"]
    )

    total_saving = (
        social_pricing["saving"]
        + fullres_pricing["saving"]
    )

    event_conn.close()

    brand = get_event_brand(event)

    return templates.TemplateResponse(
        request=request,
        name="customer/basket.html",
        context={
            "event": event_info,
            "brand": brand,
            "items": items,
            "basket_count": len(items),
            "social_pricing": social_pricing,
            "fullres_pricing": fullres_pricing,
            "total": total,
            "total_saving": total_saving,
        },
    )


@app.post("/api/gallery/{public_slug}/basket/{photo_id}/product")
def change_basket_product(
    request: Request,
    public_slug: str,
    photo_id: int,
    product_type: str = Form(...),
):
    if product_type not in {
        "social",
        "full_resolution",
    }:
        return JSONResponse(
            {"ok": False, "error": "Invalid product type."},
            status_code=400,
        )

    event = get_event_by_slug(public_slug)

    if not event:
        return JSONResponse(
            {"ok": False, "error": "Gallery not found."},
            status_code=404,
        )

    event_conn = open_event_database(event)

    token = get_customer_session_token(
        request,
        public_slug,
    )

    if not token:
        event_conn.close()
        return JSONResponse(
            {"ok": False, "error": "Basket session not found."},
            status_code=400,
        )

    session = event_conn.execute(
        """
        SELECT id
        FROM customer_sessions
        WHERE session_token = ?
        LIMIT 1
        """,
        (token,),
    ).fetchone()

    if not session:
        event_conn.close()
        return JSONResponse(
            {"ok": False, "error": "Basket session not found."},
            status_code=400,
        )

    basket = event_conn.execute(
        """
        SELECT id
        FROM baskets
        WHERE session_id = ?
          AND status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (session["id"],),
    ).fetchone()

    if not basket:
        event_conn.close()
        return JSONResponse(
            {"ok": False, "error": "Basket not found."},
            status_code=404,
        )

    unit_price = (
        get_social_price(public_slug)
        if product_type == "social"
        else 22.00
    )

    event_conn.execute(
        """
        UPDATE basket_items
        SET
            product_type = ?,
            unit_price = ?
        WHERE basket_id = ?
          AND photo_id = ?
        """,
        (
            product_type,
            unit_price,
            basket["id"],
            photo_id,
        ),
    )

    event_conn.commit()
    event_conn.close()

    return JSONResponse({"ok": True})


@app.post("/api/gallery/{public_slug}/basket/{photo_id}/remove")
def remove_basket_item(
    request: Request,
    public_slug: str,
    photo_id: int,
):
    event = get_event_by_slug(public_slug)

    if not event:
        return JSONResponse(
            {"ok": False, "error": "Gallery not found."},
            status_code=404,
        )

    event_conn = open_event_database(event)

    token = get_customer_session_token(
        request,
        public_slug,
    )

    if not token:
        event_conn.close()
        return JSONResponse(
            {"ok": False, "error": "Basket session not found."},
            status_code=400,
        )

    session = event_conn.execute(
        """
        SELECT id
        FROM customer_sessions
        WHERE session_token = ?
        LIMIT 1
        """,
        (token,),
    ).fetchone()

    if not session:
        event_conn.close()
        return JSONResponse(
            {"ok": False, "error": "Basket session not found."},
            status_code=400,
        )

    basket = event_conn.execute(
        """
        SELECT id
        FROM baskets
        WHERE session_id = ?
          AND status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (session["id"],),
    ).fetchone()

    if basket:
        event_conn.execute(
            """
            DELETE FROM basket_items
            WHERE basket_id = ?
              AND photo_id = ?
            """,
            (
                basket["id"],
                photo_id,
            ),
        )

        event_conn.commit()

    event_conn.close()

    return JSONResponse({"ok": True})


@app.get("/staff/brand-preview", response_class=HTMLResponse)
def staff_brand_preview(request: Request):
    conn = db()

    current_brand_id = conn.execute(
        """
        SELECT current_brand_id
        FROM staff_settings
        WHERE id = 1
        """
    ).fetchone()["current_brand_id"]

    conn.close()

    brand = get_brand_profile(current_brand_id)

    return templates.TemplateResponse(
        request=request,
        name="customer/brand_preview.html",
        context={
            "brand": brand,
        },
    )


@app.get("/gallery/{public_slug}/checkout", response_class=HTMLResponse)
def customer_checkout(
    request: Request,
    public_slug: str,
):
    event = get_event_by_slug(public_slug)

    if not event:
        return HTMLResponse(
            "Gallery not found.",
            status_code=404,
        )

    try:
        event_conn = open_event_database(event)
    except RuntimeError as exc:
        return HTMLResponse(
            f"Checkout temporarily unavailable: {exc}",
            status_code=503,
        )

    event_info = event_conn.execute(
        "SELECT * FROM event_info WHERE id = 1"
    ).fetchone()

    token = get_customer_session_token(
        request,
        public_slug,
    )

    session = None
    basket = None
    items = []

    if token:
        session = event_conn.execute(
            """
            SELECT *
            FROM customer_sessions
            WHERE session_token = ?
            LIMIT 1
            """,
            (token,),
        ).fetchone()

    if session:
        basket = event_conn.execute(
            """
            SELECT *
            FROM baskets
            WHERE session_id = ?
              AND status = 'open'
            ORDER BY id DESC
            LIMIT 1
            """,
            (session["id"],),
        ).fetchone()

    if basket:
        items = event_conn.execute(
            """
            SELECT
                bi.id,
                bi.photo_id,
                bi.product_type,
                bi.unit_price,
                p.original_filename
            FROM basket_items bi
            JOIN photos p
              ON p.id = bi.photo_id
            WHERE bi.basket_id = ?
            ORDER BY bi.created_at, bi.id
            """,
            (basket["id"],),
        ).fetchall()

    social_count = sum(
        1 for item in items
        if item["product_type"] == "social"
    )

    fullres_count = sum(
        1 for item in items
        if item["product_type"] == "full_resolution"
    )

    social_pricing = calculate_product_total(
        social_count,
        "social",
        public_slug,
    )

    fullres_pricing = calculate_product_total(
        fullres_count,
        "full_resolution",
    )

    total = (
        social_pricing["total"]
        + fullres_pricing["total"]
    )

    total_saving = (
        social_pricing["saving"]
        + fullres_pricing["saving"]
    )

    customer_name = (
        session["customer_name"]
        if session and session["customer_name"]
        else ""
    )

    customer_email = (
        session["customer_email"]
        if session and session["customer_email"]
        else ""
    )

    event_conn.close()

    brand = get_event_brand(event)

    return templates.TemplateResponse(
        request=request,
        name="customer/checkout.html",
        context={
            "event": event_info,
            "brand": brand,
            "items": items,
            "basket_count": len(items),
            "social_pricing": social_pricing,
            "fullres_pricing": fullres_pricing,
            "total": total,
            "total_saving": total_saving,
            "customer_name": customer_name,
            "customer_email": customer_email,
        },
    )


def load_sumup_config():
    config_path = (
        Path.home()
        / ".config"
        / "stuphie-online"
        / "sumup.env"
    )

    if not config_path.exists():
        raise RuntimeError(
            "SumUp configuration is missing."
        )

    values = {}

    for raw_line in config_path.read_text().splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    required = [
        "SUMUP_API_KEY",
        "SUMUP_CURRENCY",
        "SUMUP_MERCHANT_CODE",
    ]

    missing = [
        key
        for key in required
        if not values.get(key)
    ]

    if missing:
        raise RuntimeError(
            "Missing SumUp configuration: "
            + ", ".join(missing)
        )

    return values


def verify_sumup_order_payment(event_conn, order):
    import urllib.request
    import urllib.error
    from decimal import Decimal

    checkout_id = order["sumup_checkout_id"]

    if not checkout_id:
        return {
            "verified": False,
            "status": "NO_CHECKOUT",
        }

    sumup = load_sumup_config()

    api_request = urllib.request.Request(
        "https://api.sumup.com/v0.1/checkouts/"
        + checkout_id,
        method="GET",
        headers={
            "Authorization":
                "Bearer " + sumup["SUMUP_API_KEY"],
        },
    )

    try:
        with urllib.request.urlopen(
            api_request,
            timeout=30,
        ) as response:
            checkout = json.loads(
                response.read().decode("utf-8")
            )

    except Exception as exc:
        return {
            "verified": False,
            "status": "VERIFY_ERROR",
            "error": str(exc),
        }

    if checkout.get("id") != checkout_id:
        return {
            "verified": False,
            "status": "CHECKOUT_MISMATCH",
        }

    expected_sumup_reference = (
        order["sumup_checkout_reference"]
        or order["order_reference"]
    )

    if (
        checkout.get("checkout_reference")
        != expected_sumup_reference
    ):
        return {
            "verified": False,
            "status": "REFERENCE_MISMATCH",
        }

    expected_amount = Decimal(
        str(order["total"])
    ).quantize(Decimal("0.01"))

    received_amount = Decimal(
        str(checkout.get("amount", "0"))
    ).quantize(Decimal("0.01"))

    if received_amount != expected_amount:
        return {
            "verified": False,
            "status": "AMOUNT_MISMATCH",
        }

    if (
        checkout.get("currency")
        != sumup["SUMUP_CURRENCY"]
    ):
        return {
            "verified": False,
            "status": "CURRENCY_MISMATCH",
        }

    checkout_status = checkout.get("status")

    if checkout_status != "PAID":
        return {
            "verified": False,
            "status": checkout_status or "UNKNOWN",
        }

    transactions = checkout.get("transactions") or []

    successful = [
        transaction
        for transaction in transactions
        if transaction.get("status") == "SUCCESSFUL"
    ]

    if not successful:
        return {
            "verified": False,
            "status": "NO_SUCCESSFUL_TRANSACTION",
        }

    transaction = successful[-1]

    transaction_amount = Decimal(
        str(transaction.get("amount", "0"))
    ).quantize(Decimal("0.01"))

    if transaction_amount != expected_amount:
        return {
            "verified": False,
            "status": "TRANSACTION_AMOUNT_MISMATCH",
        }

    if (
        transaction.get("currency")
        != sumup["SUMUP_CURRENCY"]
    ):
        return {
            "verified": False,
            "status": "TRANSACTION_CURRENCY_MISMATCH",
        }

    payment_reference = (
        transaction.get("transaction_code")
        or transaction.get("id")
        or checkout.get("transaction_id")
    )

    if not payment_reference:
        return {
            "verified": False,
            "status": "NO_PAYMENT_REFERENCE",
        }

    event_conn.execute(
        """
        UPDATE orders
        SET
            payment_status = 'paid',
            payment_reference = ?,
            paid_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND payment_status = 'pending'
        """,
        (
            payment_reference,
            order["id"],
        ),
    )

    event_conn.execute(
        """
        UPDATE baskets
        SET status = 'paid'
        WHERE id = ?
          AND status = 'open'
        """,
        (order["basket_id"],),
    )

    event_conn.commit()

    return {
        "verified": True,
        "status": "PAID",
        "payment_reference": payment_reference,
    }


@app.get("/gallery/{public_slug}/order/thank-you")
def customer_order_thank_you(
    request: Request,
    public_slug: str,
):
    event = get_event_by_slug(public_slug)

    if not event:
        raise HTTPException(
            status_code=404,
            detail="Gallery not found.",
        )

    brand = get_event_brand(event)

    event_conn = open_event_database(event)

    token = get_customer_session_token(
        request,
        public_slug,
    )

    order = None

    if token:
        session = event_conn.execute(
            """
            SELECT id
            FROM customer_sessions
            WHERE session_token = ?
            LIMIT 1
            """,
            (token,),
        ).fetchone()

        if session:
            order = event_conn.execute(
                """
                SELECT *
                FROM orders
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (session["id"],),
            ).fetchone()

    event_conn.close()

    return templates.TemplateResponse(
        request=request,
        name="customer/thank_you.html",
        context={
            "event": event,
            "brand": brand,
            "order": order,
        },
    )


@app.post("/api/gallery/{public_slug}/checkout/verify")
def verify_checkout_payment(
    request: Request,
    public_slug: str,
):
    event = get_event_by_slug(public_slug)

    if not event:
        return JSONResponse(
            {"ok": False, "error": "Gallery not found."},
            status_code=404,
        )

    try:
        event_conn = open_event_database(event)
    except RuntimeError as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc)},
            status_code=503,
        )

    token = get_customer_session_token(
        request,
        public_slug,
    )

    if not token:
        event_conn.close()
        return JSONResponse(
            {
                "ok": False,
                "error": "Checkout session not found."
            },
            status_code=400,
        )

    session = event_conn.execute(
        """
        SELECT *
        FROM customer_sessions
        WHERE session_token = ?
        LIMIT 1
        """,
        (token,),
    ).fetchone()

    if not session:
        event_conn.close()
        return JSONResponse(
            {
                "ok": False,
                "error": "Checkout session not found."
            },
            status_code=400,
        )

    order = event_conn.execute(
        """
        SELECT *
        FROM orders
        WHERE session_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (session["id"],),
    ).fetchone()

    if not order:
        event_conn.close()
        return JSONResponse(
            {
                "ok": False,
                "error": "Order not found."
            },
            status_code=404,
        )

    if order["payment_status"] == "paid":
        result = {
            "verified": True,
            "status": "PAID",
            "payment_reference":
                order["payment_reference"],
        }
    else:
        result = verify_sumup_order_payment(
            event_conn,
            order,
        )

    event_conn.close()

    return {
        "ok": True,
        "order_reference":
            order["order_reference"],
        **result,
    }


@app.post("/api/gallery/{public_slug}/checkout/payment")
def create_sumup_payment(
    request: Request,
    public_slug: str,
):
    import urllib.request
    import urllib.error

    event = get_event_by_slug(public_slug)

    if not event:
        return JSONResponse(
            {"ok": False, "error": "Gallery not found."},
            status_code=404,
        )

    try:
        event_conn = open_event_database(event)
    except RuntimeError as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc)},
            status_code=503,
        )

    token = get_customer_session_token(
        request,
        public_slug,
    )

    if not token:
        event_conn.close()
        return JSONResponse(
            {
                "ok": False,
                "error": "Your checkout session has expired."
            },
            status_code=400,
        )

    session = event_conn.execute(
        """
        SELECT *
        FROM customer_sessions
        WHERE session_token = ?
        LIMIT 1
        """,
        (token,),
    ).fetchone()

    if not session:
        event_conn.close()
        return JSONResponse(
            {
                "ok": False,
                "error": "Your checkout session has expired."
            },
            status_code=400,
        )

    order = event_conn.execute(
        """
        SELECT *
        FROM orders
        WHERE session_id = ?
          AND payment_status = 'pending'
        ORDER BY id DESC
        LIMIT 1
        """,
        (session["id"],),
    ).fetchone()

    if not order:
        event_conn.close()
        return JSONResponse(
            {
                "ok": False,
                "error": "No pending order could be found."
            },
            status_code=400,
        )

    # Reuse an existing SumUp checkout if we already have one.
    if (
        order["sumup_checkout_id"]
        and order["sumup_checkout_url"]
    ):
        payment_url = order["sumup_checkout_url"]
        event_conn.close()

        return {
            "ok": True,
            "order_reference": order["order_reference"],
            "payment_url": payment_url,
        }

    try:
        sumup = load_sumup_config()

        sumup_reference = (
            order["order_reference"]
            + "-"
            + secrets.token_hex(4).upper()
        )

        payload = {
            "checkout_reference": sumup_reference,
            "amount": float(order["total"]),
            "currency": sumup["SUMUP_CURRENCY"],
            "merchant_code": sumup["SUMUP_MERCHANT_CODE"],
            "description": (
                "Stuphie order "
                + order["order_reference"]
            ),
            "hosted_checkout": {
                "enabled": True
            },
        }

        data = json.dumps(payload).encode("utf-8")

        api_request = urllib.request.Request(
            "https://api.sumup.com/v0.1/checkouts",
            data=data,
            method="POST",
            headers={
                "Authorization":
                    "Bearer "
                    + sumup["SUMUP_API_KEY"],
                "Content-Type":
                    "application/json",
            },
        )

        with urllib.request.urlopen(
            api_request,
            timeout=30,
        ) as response:
            checkout = json.loads(
                response.read().decode("utf-8")
            )

    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        print(
            "SUMUP CHECKOUT ERROR",
            "status=" + str(exc.code),
            "body=" + error_body,
            flush=True,
        )

        event_conn.close()

        return JSONResponse(
            {
                "ok": False,
                "error":
                    "SumUp could not create the payment checkout.",
                "detail": error_body,
            },
            status_code=502,
        )

    except Exception as exc:
        event_conn.close()

        return JSONResponse(
            {
                "ok": False,
                "error":
                    "Payment service is temporarily unavailable.",
                "detail": str(exc),
            },
            status_code=502,
        )

    checkout_id = checkout.get("id")
    payment_url = checkout.get(
        "hosted_checkout_url"
    )

    if not checkout_id or not payment_url:
        event_conn.close()

        return JSONResponse(
            {
                "ok": False,
                "error":
                    "SumUp did not return a payment URL."
            },
            status_code=502,
        )

    event_conn.execute(
        """
        UPDATE orders
        SET
            sumup_checkout_id = ?,
            sumup_checkout_url = ?,
            sumup_checkout_reference = ?
        WHERE id = ?
        """,
        (
            checkout_id,
            payment_url,
            sumup_reference,
            order["id"],
        ),
    )

    event_conn.commit()
    event_conn.close()

    return {
        "ok": True,
        "order_reference":
            order["order_reference"],
        "payment_url": payment_url,
    }


@app.post("/api/gallery/{public_slug}/checkout/details")
def save_checkout_details(
    request: Request,
    public_slug: str,
    customer_name: str = Form(...),
    customer_email: str = Form(...),
):
    event = get_event_by_slug(public_slug)

    if not event:
        return JSONResponse(
            {"ok": False, "error": "Gallery not found."},
            status_code=404,
        )

    customer_name = customer_name.strip()
    customer_email = customer_email.strip().lower()

    if not customer_name:
        return JSONResponse(
            {"ok": False, "error": "Please enter your name."},
            status_code=400,
        )

    if (
        not customer_email
        or "@" not in customer_email
        or "." not in customer_email.split("@")[-1]
    ):
        return JSONResponse(
            {
                "ok": False,
                "error": "Please enter a valid email address."
            },
            status_code=400,
        )

    try:
        event_conn = open_event_database(event)
    except RuntimeError as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc)},
            status_code=503,
        )

    token = get_customer_session_token(
        request,
        public_slug,
    )

    session, token, is_new = get_or_create_customer_session(
        event_conn,
        token,
    )

    event_conn.execute(
        """
        UPDATE customer_sessions
        SET
            customer_name = ?,
            customer_email = ?,
            last_seen_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            customer_name,
            customer_email,
            session["id"],
        ),
    )

    event_conn.commit()

    basket = event_conn.execute(
        """
        SELECT *
        FROM baskets
        WHERE session_id = ?
          AND status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (session["id"],),
    ).fetchone()

    if not basket:
        event_conn.close()

        return JSONResponse(
            {
                "ok": False,
                "error": "Your basket is empty."
            },
            status_code=400,
        )

    items = event_conn.execute(
        """
        SELECT
            bi.photo_id,
            bi.product_type,
            bi.unit_price,
            p.original_filename
        FROM basket_items bi
        JOIN photos p
          ON p.id = bi.photo_id
        WHERE bi.basket_id = ?
        ORDER BY bi.created_at, bi.id
        """,
        (basket["id"],),
    ).fetchall()

    if not items:
        event_conn.close()

        return JSONResponse(
            {
                "ok": False,
                "error": "Your basket is empty."
            },
            status_code=400,
        )

    social_count = sum(
        1
        for item in items
        if item["product_type"] == "social"
    )

    fullres_count = sum(
        1
        for item in items
        if item["product_type"] == "full_resolution"
    )

    social_pricing = calculate_product_total(
        social_count,
        "social",
        public_slug,
    )

    fullres_pricing = calculate_product_total(
        fullres_count,
        "full_resolution",
    )

    subtotal = round(
        sum(float(item["unit_price"]) for item in items),
        2,
    )

    total = round(
        social_pricing["total"]
        + fullres_pricing["total"],
        2,
    )

    discount_total = round(
        subtotal - total,
        2,
    )

    existing_order = event_conn.execute(
        """
        SELECT *
        FROM orders
        WHERE basket_id = ?
          AND session_id = ?
          AND payment_status = 'pending'
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            basket["id"],
            session["id"],
        ),
    ).fetchone()

    if existing_order:
        order_id = existing_order["id"]
        order_reference = existing_order["order_reference"]

        event_conn.execute(
            """
            UPDATE orders
            SET
                customer_name = ?,
                customer_email = ?,
                subtotal = ?,
                discount_total = ?,
                total = ?,
                sumup_checkout_id = NULL,
                sumup_checkout_url = NULL,
                sumup_checkout_reference = NULL
            WHERE id = ?
            """,
            (
                customer_name,
                customer_email,
                subtotal,
                discount_total,
                total,
                order_id,
            ),
        )

        event_conn.execute(
            """
            DELETE FROM order_items
            WHERE order_id = ?
            """,
            (order_id,),
        )

    else:
        order_reference = (
            "ORD-"
            + secrets.token_hex(5).upper()
        )

        cursor = event_conn.execute(
            """
            INSERT INTO orders (
                order_reference,
                session_id,
                basket_id,
                customer_name,
                customer_email,
                subtotal,
                discount_total,
                total,
                payment_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                order_reference,
                session["id"],
                basket["id"],
                customer_name,
                customer_email,
                subtotal,
                discount_total,
                total,
            ),
        )

        order_id = cursor.lastrowid

    for item in items:
        event_conn.execute(
            """
            INSERT INTO order_items (
                order_id,
                photo_id,
                product_type,
                line_price,
                photo_reference
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                order_id,
                item["photo_id"],
                item["product_type"],
                float(item["unit_price"]),
                item["original_filename"],
            ),
        )

    event_conn.commit()
    event_conn.close()

    response = JSONResponse({
        "ok": True,
        "order_reference": order_reference,
        "total": total,
    })

    if is_new:
        response.set_cookie(
            key=customer_cookie_name(public_slug),
            value=token,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            samesite="lax",
        )

    return response
