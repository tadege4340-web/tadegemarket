
import os
import uuid
import time
import logging
import html
import re

from pathlib import Path
from decimal import Decimal, InvalidOperation
from functools import wraps
from datetime import datetime, timezone
from urllib.parse import urlsplit, unquote

import requests

from flask import (
    Flask, render_template, jsonify, request, redirect, url_for,
    flash, session, abort, make_response, send_from_directory
)

from sqlalchemy import or_, func
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.exceptions import HTTPException

from config import Config

from db_models import (
    db,
    User,
    Category,
    Product,
    CartItem,
    Order,
    OrderItem,
    Payment,
    SupportConversation,
    SupportMessage,
    Notification
)


# =========================================================
# ENVIRONMENT AND APPLICATION CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)
except ImportError:
    pass

app = Flask(__name__)
app.config.from_object(Config)

app.config["SECRET_KEY"] = (
    os.getenv("SECRET_KEY")
    or app.config.get("SECRET_KEY")
    or "change-this-secret-key-before-deployment"
)

app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.getenv("COOKIE_SECURE", "false").lower() == "true"
)

UPLOAD_FOLDER = BASE_DIR / "static" / "uploads" / "products"
PRIVATE_RECEIPT_FOLDER = (
    BASE_DIR / "private_uploads" / "payment_receipts"
)

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
PRIVATE_RECEIPT_FOLDER.mkdir(parents=True, exist_ok=True)

app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)

db.init_app(app)


# =========================================================
# PAYMENT RECIPIENT CONFIGURATION
# =========================================================

CBE_BIRR_ACCOUNT_NAME = os.getenv(
    "CBE_BIRR_ACCOUNT_NAME", ""
).strip()

CBE_BIRR_ACCOUNT_NUMBER = os.getenv(
    "CBE_BIRR_ACCOUNT_NUMBER", ""
).strip()

TELEBIRR_ACCOUNT_NAME = os.getenv(
    "TELEBIRR_ACCOUNT_NAME", ""
).strip()

TELEBIRR_PHONE_NUMBER = os.getenv(
    "TELEBIRR_PHONE_NUMBER", ""
).strip()


# =========================================================
# CHAPA CONFIGURATION
# =========================================================

CHAPA_SECRET_KEY = os.getenv(
    "CHAPA_SECRET_KEY", ""
).strip()

CHAPA_API_BASE = os.getenv(
    "CHAPA_API_BASE",
    os.getenv(
        "CHAPA_BASE_URL",
        "https://api.chapa.co/v1"
    )
).rstrip("/")

CHAPA_CURRENCY = "ETB"
CHAPA_TIMEOUT = 25

APP_PUBLIC_URL = os.getenv(
    "APP_PUBLIC_URL", ""
).strip().rstrip("/")


def chapa_headers():
    return {
        "Authorization": f"Bearer {CHAPA_SECRET_KEY}",
        "Content-Type": "application/json"
    }


def public_url(endpoint, **values):
    path = url_for(endpoint, **values)

    if APP_PUBLIC_URL:
        return APP_PUBLIC_URL + path

    return url_for(endpoint, _external=True, **values)


def chapa_initialize(payload):
    if not CHAPA_SECRET_KEY:
        raise RuntimeError(
            "Chapa secret key is not configured."
        )

    response = requests.post(
        f"{CHAPA_API_BASE}/transaction/initialize",
        json=payload,
        headers=chapa_headers(),
        timeout=CHAPA_TIMEOUT
    )

    response.raise_for_status()

    try:
        return response.json()
    except ValueError as error:
        raise RuntimeError(
            "Chapa returned an invalid JSON response."
        ) from error


def chapa_verify(tx_ref):
    if not CHAPA_SECRET_KEY:
        raise RuntimeError(
            "Chapa secret key is not configured."
        )

    response = requests.get(
        f"{CHAPA_API_BASE}/transaction/verify/{tx_ref}",
        headers=chapa_headers(),
        timeout=CHAPA_TIMEOUT
    )

    response.raise_for_status()

    try:
        return response.json()
    except ValueError as error:
        raise RuntimeError(
            "Chapa returned an invalid verification response."
        ) from error


# =========================================================
# CURRENCY CONFIGURATION
# =========================================================

SUPPORTED_CURRENCIES = {
    "ETB": "Ethiopian Birr",
    "USD": "US Dollar",
    "EUR": "Euro",
    "GBP": "British Pound",
    "KES": "Kenyan Shilling"
}

CURRENCY_OPTIONS = {
    "ETB": {
        "name": "Ethiopian Birr",
        "flag": "🇪🇹"
    },
    "USD": {
        "name": "US Dollar",
        "flag": "🇺🇸"
    },
    "EUR": {
        "name": "Euro",
        "flag": "🇪🇺"
    },
    "GBP": {
        "name": "British Pound",
        "flag": "🇬🇧"
    },
    "KES": {
        "name": "Kenyan Shilling",
        "flag": "🇰🇪"
    }
}

DEFAULT_CURRENCY = "ETB"

_exchange_rates_cache = {
    "rates": {"ETB": 1},
    "updated": 0
}


def get_currency():
    currency = session.get(
        "currency",
        DEFAULT_CURRENCY
    )

    if currency not in SUPPORTED_CURRENCIES:
        return DEFAULT_CURRENCY

    return currency


def get_exchange_rates():
    if time.time() - _exchange_rates_cache["updated"] < 1800:
        return _exchange_rates_cache["rates"]

    api_key = os.getenv(
        "EXCHANGE_RATE_API_KEY", ""
    ).strip()

    if not api_key:
        return {"ETB": 1}

    try:
        response = requests.get(
            f"https://v6.exchangerate-api.com/v6/{api_key}/latest/ETB",
            timeout=10
        )

        response.raise_for_status()
        data = response.json()

        if data.get("result") == "success":
            rates = dict(
                data.get("conversion_rates", {})
            )

            rates["ETB"] = 1

            _exchange_rates_cache["rates"] = rates
            _exchange_rates_cache["updated"] = time.time()

            return rates

    except (requests.RequestException, ValueError):
        app.logger.exception(
            "Unable to retrieve exchange rates."
        )

    return {"ETB": 1}


@app.template_filter("currency")
@app.template_filter("money")
def currency_filter(amount):
    try:
        amount = Decimal(str(amount))

        if not amount.is_finite():
            amount = Decimal("0")

    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")

    currency = get_currency()

    if currency == "ETB":
        return f"Br {amount:,.2f}"

    rate = get_exchange_rates().get(currency)

    if rate is None:
        return f"Br {amount:,.2f}"

    try:
        converted = amount * Decimal(str(rate))
        return f"{currency} {converted:,.2f}"

    except (InvalidOperation, TypeError, ValueError):
        return f"Br {amount:,.2f}"


# =========================================================
# COMMON USER HELPERS
# =========================================================

def get_current_user():
    user_id = session.get("user_id")

    if not user_id:
        return None

    return db.session.get(User, user_id)


@app.context_processor
def inject_global_data():
    user = get_current_user()
    unread_count = 0

    if user:
        if user.role == "admin":
            unread_count = SupportMessage.query.join(
                SupportConversation,
                SupportMessage.conversation_id
                == SupportConversation.id
            ).filter(
                SupportMessage.sender_id
                == SupportConversation.customer_id,
                SupportMessage.is_read.is_(False)
            ).count()

        else:
            unread_count = SupportMessage.query.join(
                SupportConversation,
                SupportMessage.conversation_id
                == SupportConversation.id
            ).filter(
                SupportConversation.customer_id == user.id,
                SupportMessage.sender_id != user.id,
                SupportMessage.is_read.is_(False)
            ).count()

    return {
        "current_currency": get_currency(),
        "supported_currencies": SUPPORTED_CURRENCIES,
        "currency_options": CURRENCY_OPTIONS,
        "current_user": user,
        "global_unread_support": unread_count
    }


def is_ajax_request():
    return (
        request.headers.get(
            "X-Requested-With", ""
        ).lower() == "xmlhttprequest"
    )


def ajax_json(
    success,
    message,
    status_code=200,
    **extra
):
    response = {
        "success": success,
        "message": message
    }

    response.update(extra)

    return jsonify(response), status_code


# =========================================================
# AUTHENTICATION
# =========================================================

def login_required(function):
    @wraps(function)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            if is_ajax_request():
                return jsonify({
                    "success": False,
                    "message": (
                        "Your session has expired. "
                        "Please log in again."
                    ),
                    "login_url": url_for("login")
                }), 401

            flash("Please log in first.", "warning")
            return redirect(url_for("login"))

        user = get_current_user()

        if not user:
            session.clear()

            if is_ajax_request():
                return jsonify({
                    "success": False,
                    "message": "Your session has expired.",
                    "login_url": url_for("login")
                }), 401

            flash("Please log in again.", "warning")
            return redirect(url_for("login"))

        return function(*args, **kwargs)

    return decorated_function


def admin_required(function):
    @wraps(function)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            if is_ajax_request():
                return jsonify({
                    "success": False,
                    "message": "Your session has expired.",
                    "login_url": url_for("login")
                }), 401

            flash("Please log in first.", "warning")
            return redirect(url_for("login"))

        user = get_current_user()

        if not user or user.role != "admin":
            if is_ajax_request():
                return jsonify({
                    "success": False,
                    "message": "Administrator access required."
                }), 403

            flash(
                "Administrator access required.",
                "danger"
            )

            return redirect(url_for("home"))

        return function(*args, **kwargs)

    return decorated_function


# =========================================================
# ADMIN TEMPLATE HELPERS
# =========================================================

def render_admin_page(
    partial_template,
    full_template,
    **context
):
    """
    AJAX request:
        Return only the requested partial.

    Normal request:
        Load the admin dashboard and place the requested
        partial inside its main content area.
    """

    if is_ajax_request():
        try:
            app.jinja_env.get_template(partial_template)

            return render_template(
                partial_template,
                **context
            )

        except Exception:
            app.logger.exception(
                "Unable to render admin partial: %s",
                partial_template
            )

            return make_response(
                f"""
                <div class="alert alert-warning">
                    <h5>Admin page template is missing</h5>
                    <p>Please create:
                    <strong>{html.escape(partial_template)}</strong></p>
                </div>
                """,
                500
            )

    # The dashboard template is the common admin shell.
    # initial_content_template tells it which page to load.
    if full_template == "admin_dashboard.html":
        return render_template(
            full_template,
            initial_content_template=partial_template,
            **context
        )

    return render_template(
        full_template,
        initial_content_template=partial_template,
        **context
    )


def render_add_product_page(categories):
    if is_ajax_request():
        try:
            app.jinja_env.get_template("_add_product.html")

            return render_template(
                "_add_product.html",
                categories=categories
            )

        except Exception:
            app.logger.exception(
                "Missing _add_product.html"
            )

            return make_response(
                """
                <div class="alert alert-warning">
                    <h4>Add Product template is missing</h4>
                    <p>Create templates/_add_product.html.</p>
                </div>
                """,
                500
            )

    return render_template(
        "admin_dashboard.html",
        initial_content_template="_add_product.html",
        categories=categories
    )


def render_edit_product_page(product, categories):
    if is_ajax_request():
        try:
            app.jinja_env.get_template("_edit_product.html")

            return render_template(
                "_edit_product.html",
                product=product,
                categories=categories
            )

        except Exception:
            app.logger.exception(
                "Missing _edit_product.html"
            )

            return make_response(
                """
                <div class="alert alert-warning">
                    <h4>Edit Product partial is missing</h4>
                    <p>Create templates/_edit_product.html.</p>
                </div>
                """,
                500
            )

    return render_template(
        "admin_dashboard.html",
        initial_content_template="_edit_product.html",
        product=product,
        categories=categories
    )


def render_admin_support_conversation_page(
    conversation,
    customer,
    messages
):
    context = {
        "conversation": conversation,
        "customer": customer,
        "messages": messages
    }

    if is_ajax_request():
        try:
            app.jinja_env.get_template(
                "_admin_support_conversation.html"
            )

            return render_template(
                "_admin_support_conversation.html",
                **context
            )

        except Exception:
            app.logger.exception(
                "Missing _admin_support_conversation.html"
            )

            return make_response(
                """
                <div class="alert alert-warning">
                    <h4>Support conversation partial is missing</h4>
                    <p>Create templates/_admin_support_conversation.html.</p>
                </div>
                """,
                500
            )

    return render_template(
        "admin_dashboard.html",
        ajax_request=False,
        initial_content_template=(
            "_admin_support_conversation.html"
        ),
        **context
    )


def admin_form_response(
    success,
    message,
    redirect_url=None,
    status_code=200,
    **extra
):
    if is_ajax_request():
        return ajax_json(
            success,
            message,
            status_code,
            redirect=redirect_url,
            **extra
        )

    flash(
        message,
        "success" if success else "danger"
    )

    if success:
        return redirect(
            redirect_url or url_for("admin_products")
        )

    return redirect(
        request.referrer or url_for("add_product")
    )


# =========================================================
# IMAGE HELPERS
# =========================================================

def allowed_file(filename):
    return (
        bool(filename)
        and "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


def validate_uploaded_image(file):
    if not file or not file.filename:
        raise ValueError("Please select an image.")

    if not allowed_file(file.filename):
        raise ValueError(
            "Only JPG, JPEG, PNG and WEBP images are allowed."
        )

    try:
        from PIL import Image

        file.seek(0)
        image = Image.open(file)

        if image.format not in {"JPEG", "PNG", "WEBP"}:
            raise ValueError("Unsupported image format.")

        image.verify()
        file.seek(0)

    except ValueError:
        file.seek(0)
        raise

    except Exception as error:
        file.seek(0)

        raise ValueError(
            "The uploaded file is not a valid image."
        ) from error


# =========================================================
# SUPABASE PRODUCT IMAGE STORAGE
# =========================================================

SUPABASE_URL = os.getenv(
    "SUPABASE_URL", ""
).strip().rstrip("/")

SUPABASE_SECRET_KEY = os.getenv(
    "SUPABASE_SECRET_KEY", ""
).strip()

SUPABASE_BUCKET = os.getenv(
    "SUPABASE_BUCKET", "market"
).strip()


def supabase_configured():
    return bool(
        SUPABASE_URL
        and SUPABASE_SECRET_KEY
        and SUPABASE_BUCKET
    )


def supabase_headers(content_type=None):
    headers = {
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "apikey": SUPABASE_SECRET_KEY
    }

    if content_type:
        headers["Content-Type"] = content_type

    return headers


def get_image_content_type(extension):
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp"
    }.get(extension.lower(), "application/octet-stream")


def save_product_image(file):
    if not file or not file.filename:
        return None

    validate_uploaded_image(file)

    if not supabase_configured():
        raise RuntimeError(
            "Supabase image storage is not configured."
        )

    extension = secure_filename(
        file.filename.rsplit(".", 1)[1].lower()
    )

    object_path = f"products/{uuid.uuid4().hex}.{extension}"

    upload_url = (
        f"{SUPABASE_URL}/storage/v1/object/"
        f"{SUPABASE_BUCKET}/{object_path}"
    )

    file.seek(0)
    image_data = file.read()

    if not image_data:
        raise ValueError("The selected image is empty.")

    response = requests.post(
        upload_url,
        data=image_data,
        headers={
            **supabase_headers(
                get_image_content_type(extension)
            ),
            "x-upsert": "false"
        },
        timeout=30
    )

    response.raise_for_status()

    return (
        f"{SUPABASE_URL}/storage/v1/object/public/"
        f"{SUPABASE_BUCKET}/{object_path}"
    )


def delete_product_image(image_reference):
    if not image_reference:
        return

    image_reference = str(image_reference).strip()

    if image_reference.startswith(("https://", "http://")):
        if not supabase_configured():
            return

        try:
            parsed = urlsplit(image_reference)
            configured = urlsplit(SUPABASE_URL)

            prefix = (
                f"/storage/v1/object/public/"
                f"{SUPABASE_BUCKET}/"
            )

            if (
                parsed.scheme != "https"
                or parsed.hostname != configured.hostname
                or not parsed.path.startswith(prefix)
            ):
                return

            object_path = unquote(
                parsed.path[len(prefix):]
            )

            if not object_path or ".." in object_path.split("/"):
                return

            response = requests.delete(
                f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}",
                json={"prefixes": [object_path]},
                headers=supabase_headers("application/json"),
                timeout=20
            )

            response.raise_for_status()

        except requests.RequestException:
            app.logger.exception(
                "Unable to delete Supabase product image."
            )

        return

    # Support deleting old local product images.
    safe_filename = os.path.basename(
        image_reference.replace("\\", "/")
    )

    path = UPLOAD_FOLDER / safe_filename

    if path.is_file():
        try:
            path.unlink()
        except OSError:
            app.logger.exception(
                "Unable to delete legacy product image."
            )


# =========================================================
# PRIVATE PAYMENT RECEIPT HELPERS
# =========================================================

def save_payment_receipt(file):
    validate_uploaded_image(file)

    extension = secure_filename(
        file.filename.rsplit(".", 1)[1].lower()
    )

    filename = f"{uuid.uuid4().hex}.{extension}"
    save_path = PRIVATE_RECEIPT_FOLDER / filename

    try:
        file.save(str(save_path))

    except Exception:
        if save_path.exists():
            save_path.unlink()

        raise

    return filename


def delete_payment_receipt(filename):
    if not filename:
        return

    safe_filename = os.path.basename(
        str(filename).replace("\\", "/")
    )

    path = PRIVATE_RECEIPT_FOLDER / safe_filename

    if path.is_file():
        try:
            path.unlink()

        except OSError:
            app.logger.exception(
                "Unable to delete payment receipt."
            )


@app.route("/payment/receipt/<int:payment_id>")
@login_required
def payment_receipt(payment_id):
    user = get_current_user()

    payment = db.get_or_404(
        Payment,
        payment_id
    )

    if not payment.order:
        abort(404)

    if (
        user.role != "admin"
        and payment.order.user_id != user.id
    ):
        abort(403)

    if not payment.receipt_filename:
        abort(404)

    filename = os.path.basename(
        payment.receipt_filename
    )

    receipt_path = PRIVATE_RECEIPT_FOLDER / filename

    if not receipt_path.is_file():
        abort(404)

    response = send_from_directory(
        str(PRIVATE_RECEIPT_FOLDER),
        filename,
        as_attachment=False,
        conditional=True
    )

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"

    return response


# =========================================================
# GENERAL HELPERS
# =========================================================

def safe_previous_url():
    next_page = request.form.get(
        "next", ""
    ).strip()

    if next_page:
        parsed = urlsplit(next_page)

        if (
            parsed.scheme
            or parsed.netloc
            or not parsed.path.startswith("/")
            or parsed.path.startswith("//")
        ):
            return url_for("home")

        return parsed.path + (
            "?" + parsed.query
            if parsed.query else ""
        )

    referrer = request.referrer

    if not referrer:
        return url_for("home")

    parsed = urlsplit(referrer)

    if (
        parsed.netloc
        and parsed.netloc != request.host
    ):
        return url_for("home")

    if (
        not parsed.path.startswith("/")
        or parsed.path.startswith("//")
    ):
        return url_for("home")

    return parsed.path + (
        "?" + parsed.query
        if parsed.query else ""
    )


def get_cart_items(user_id):
    return CartItem.query.filter_by(
        user_id=user_id
    ).order_by(
        CartItem.id.asc()
    ).all()


def get_cart_total(user_id):
    total = Decimal("0.00")

    for item in get_cart_items(user_id):
        if item.product:
            total += (
                Decimal(str(item.product.price))
                * item.quantity
            )

    return total


def get_cart_count(user_id):
    return db.session.query(
        func.coalesce(
            func.sum(CartItem.quantity),
            0
        )
    ).filter(
        CartItem.user_id == user_id
    ).scalar() or 0


def normalize_phone(phone):
    phone = "".join(
        character for character in str(phone)
        if character.isdigit()
    )

    if phone.startswith("251") and len(phone) == 12:
        phone = "0" + phone[3:]

    return phone


def split_full_name(full_name):
    parts = full_name.strip().split()

    if not parts:
        return "Customer", "ShopNova"

    if len(parts) == 1:
        return parts[0], "Customer"

    return parts[0], " ".join(parts[1:])


def valid_category(category_id):
    if not category_id:
        return None

    return db.session.get(
        Category,
        category_id
    )


def get_payment_by_reference(tx_ref):
    return Payment.query.filter_by(
        tx_ref=tx_ref
    ).first()


def get_payment_for_user(tx_ref, user):
    payment = get_payment_by_reference(tx_ref)

    if not payment or not payment.order:
        return None

    if (
        user.role != "admin"
        and payment.order.user_id != user.id
    ):
        abort(403)

    return payment


def parse_product_form():
    name = request.form.get(
        "name", ""
    ).strip()

    description = request.form.get(
        "description", ""
    ).strip()

    tag = request.form.get(
        "tag", ""
    ).strip()

    icon = request.form.get(
        "icon", "📦"
    ).strip() or "📦"

    if not name:
        raise ValueError("Product name is required.")

    if len(name) > 150:
        raise ValueError(
            "Product name must not exceed 150 characters."
        )

    if len(tag) > 50:
        raise ValueError(
            "Product tag must not exceed 50 characters."
        )

    if len(description) > 10000:
        raise ValueError(
            "Product description is too long."
        )

    try:
        price = Decimal(
            request.form.get("price", "0")
        )

        stock = int(
            request.form.get("stock", "0")
        )

        category_id = request.form.get(
            "category_id",
            type=int
        )

    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(
            "Enter a valid price, stock and category."
        )

    if not price.is_finite() or price <= 0:
        raise ValueError(
            "Price must be greater than zero."
        )

    if stock < 0:
        raise ValueError(
            "Stock cannot be negative."
        )

    if category_id and not valid_category(category_id):
        raise ValueError(
            "Please select a valid category."
        )

    return {
        "name": name,
        "description": description,
        "tag": tag,
        "icon": icon,
        "price": price,
        "stock": stock,
        "category_id": category_id
    }


# =========================================================
# ORDER STOCK AND CART HELPERS
# =========================================================

def validate_order_stock(order):
    order_items = OrderItem.query.filter_by(
        order_id=order.id
    ).order_by(
        OrderItem.product_id.asc()
    ).all()

    if not order_items:
        raise ValueError(
            "This order has no products."
        )

    required_quantities = {}

    for order_item in order_items:
        required_quantities[order_item.product_id] = (
            required_quantities.get(
                order_item.product_id,
                0
            ) + order_item.quantity
        )

    products_to_update = []

    for product_id in sorted(required_quantities):
        required_quantity = required_quantities[product_id]

        product = Product.query.filter_by(
            id=product_id
        ).with_for_update().first()

        if not product:
            raise ValueError(
                "An ordered product no longer exists."
            )

        if product.stock < required_quantity:
            raise ValueError(
                f"Insufficient stock for {product.name}. "
                "Please contact the administrator."
            )

        products_to_update.append(
            (product, required_quantity)
        )

    return (
        order_items,
        products_to_update,
        required_quantities
    )


def deduct_order_stock_and_update_cart(order):
    (
        order_items,
        products_to_update,
        required_quantities
    ) = validate_order_stock(order)

    for product, required_quantity in products_to_update:
        product.stock -= required_quantity

    for product_id, required_quantity in required_quantities.items():
        cart_item = CartItem.query.filter_by(
            user_id=order.user_id,
            product_id=product_id
        ).with_for_update().first()

        if not cart_item:
            continue

        remaining_quantity = (
            cart_item.quantity - required_quantity
        )

        if remaining_quantity <= 0:
            db.session.delete(cart_item)

        else:
            cart_item.quantity = remaining_quantity

    return order_items


# =========================================================
# CHAPA PAYMENT VERIFICATION
# =========================================================

def complete_successful_payment(
    payment,
    verification_data
):
    """
    This function is called only after server-side Chapa
    verification.

    The payment and order are locked before changing their
    status. A paid payment is never processed twice.
    """

    if not isinstance(verification_data, dict):
        raise ValueError(
            "Invalid payment verification data."
        )

    locked_payment = Payment.query.filter_by(
        id=payment.id
    ).with_for_update().first()

    if not locked_payment:
        raise ValueError(
            "Payment record was not found."
        )

    if locked_payment.status == "paid":
        return True

    if locked_payment.provider != "chapa":
        raise ValueError(
            "This is not a Chapa payment."
        )

    if locked_payment.status == "failed":
        return False

    order = Order.query.filter_by(
        id=locked_payment.order_id
    ).with_for_update().first()

    if not order:
        raise ValueError(
            "Payment order was not found."
        )

    if order.status == "cancelled":
        raise ValueError(
            "This order has been cancelled."
        )

    verified_status = str(
        verification_data.get("status", "")
    ).lower()

    if verified_status != "success":
        return False

    verified_tx_ref = verification_data.get("tx_ref")

    if str(verified_tx_ref or "") != str(
        locked_payment.tx_ref
    ):
        raise ValueError(
            "Payment reference does not match."
        )

    verified_currency = str(
        verification_data.get("currency", "")
    ).upper()

    if verified_currency != str(
        locked_payment.currency
    ).upper():
        raise ValueError(
            "Payment currency does not match."
        )

    try:
        verified_amount = Decimal(
            str(verification_data.get("amount"))
        )

        expected_amount = Decimal(
            str(locked_payment.amount)
        )

        order_amount = Decimal(
            str(order.total_amount)
        )

    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(
            "Unable to verify payment amount."
        )

    if (
        not verified_amount.is_finite()
        or verified_amount != expected_amount
        or order_amount != expected_amount
    ):
        raise ValueError(
            "Payment amount does not match the order."
        )

    if locked_payment.currency != CHAPA_CURRENCY:
        raise ValueError(
            "Unexpected payment currency."
        )

    # Do not allow another payment to process the same order.
    another_paid_payment = Payment.query.filter(
        Payment.order_id == order.id,
        Payment.id != locked_payment.id,
        Payment.status == "paid"
    ).first()

    if another_paid_payment:
        raise ValueError(
            "This order already has a verified payment."
        )

    # Stock is deducted only after successful verification.
    deduct_order_stock_and_update_cart(order)

    locked_payment.status = "paid"

    locked_payment.provider_reference = (
        verification_data.get("reference")
        or locked_payment.provider_reference
    )

    locked_payment.verified_at = datetime.now(
        timezone.utc
    )

    order.status = "processing"

    db.session.commit()

    return True


def verify_payment_record(payment):
    if not payment:
        raise ValueError(
            "Payment record not found."
        )

    if payment.provider != "chapa":
        raise ValueError(
            "Only Chapa payments can be verified automatically."
        )

    if payment.status == "paid":
        return True

    if payment.status == "failed":
        return False

    verification_response = chapa_verify(
        payment.tx_ref
    )

    if not isinstance(verification_response, dict):
        raise ValueError(
            "Invalid response from Chapa."
        )

    if str(
        verification_response.get("status", "")
    ).lower() != "success":
        return False

    verification_data = verification_response.get("data")

    if not isinstance(verification_data, dict):
        raise ValueError(
            "Chapa verification data is missing."
        )

    return complete_successful_payment(
        payment,
        verification_data
    )


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    search = request.args.get(
        "search", ""
    ).strip()

    category_id = request.args.get(
        "category_id",
        type=int
    )

    query = Product.query.filter_by(
        is_active=True
    )

    if search:
        query = query.filter(
            Product.name.ilike(f"%{search}%")
        )

    if category_id:
        query = query.filter(
            Product.category_id == category_id
        )

    products = query.order_by(
        Product.created_at.desc()
    ).all()

    categories = Category.query.order_by(
        Category.name.asc()
    ).all()

    cart_count = 0

    if session.get("user_id"):
        cart_count = get_cart_count(
            session["user_id"]
        )

    return render_template(
        "index.html",
        products=products,
        categories=categories,
        search=search,
        selected_category=category_id,
        cart_count=cart_count
    )


# =========================================================
# ABOUT AND CONTACT
# =========================================================

@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get(
            "name", ""
        ).strip()

        email = request.form.get(
            "email", ""
        ).strip()

        message = request.form.get(
            "message", ""
        ).strip()

        if not name or not email or not message:
            flash(
                "Please complete all fields.",
                "danger"
            )

            return redirect(url_for("contact"))

        flash(
            "Your message has been submitted.",
            "success"
        )

        return redirect(url_for("contact"))

    return render_template("contact.html")


# =========================================================
# CURRENCY
# =========================================================

@app.route("/set-currency", methods=["POST"])
def set_currency():
    currency = request.form.get(
        "currency",
        DEFAULT_CURRENCY
    ).upper()

    if currency not in SUPPORTED_CURRENCIES:
        flash(
            "Unsupported currency.",
            "danger"
        )

        return redirect(url_for("home"))

    session["currency"] = currency

    flash(
        f"Currency changed to {currency}.",
        "success"
    )

    return redirect(safe_previous_url())


@app.route("/currency-rates")
def currency_rates():
    return jsonify(get_exchange_rates())


# =========================================================
# REGISTRATION
# =========================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get(
            "full_name", ""
        ).strip()

        username = request.form.get(
            "username", ""
        ).strip()

        email = request.form.get(
            "email", ""
        ).strip().lower()

        password = request.form.get(
            "password", ""
        )

        confirm_password = request.form.get(
            "confirm_password", ""
        )

        if not all([
            full_name,
            username,
            email,
            password
        ]):
            flash(
                "All required fields must be completed.",
                "danger"
            )

            return render_template("register.html")

        if len(full_name) > 150 or len(username) > 80:
            flash(
                "Your name or username is too long.",
                "danger"
            )

            return render_template("register.html")

        if not re.match(
            r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
            email
        ):
            flash(
                "Enter a valid email address.",
                "danger"
            )

            return render_template("register.html")

        if len(password) < 8:
            flash(
                "Password must contain at least 8 characters.",
                "danger"
            )

            return render_template("register.html")

        if password != confirm_password:
            flash(
                "Passwords do not match.",
                "danger"
            )

            return render_template("register.html")

        existing_user = User.query.filter(
            or_(
                User.username.ilike(username),
                User.email.ilike(email)
            )
        ).first()

        if existing_user:
            flash(
                "Username or email already exists.",
                "danger"
            )

            return render_template("register.html")

        user = User(
            full_name=full_name,
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            role="customer"
        )

        try:
            db.session.add(user)
            db.session.commit()

            flash(
                "Registration successful. Please log in.",
                "success"
            )

            return redirect(url_for("login"))

        except IntegrityError:
            db.session.rollback()

            flash(
                "Username or email already exists.",
                "danger"
            )

        except Exception:
            db.session.rollback()

            app.logger.exception(
                "Registration failed."
            )

            flash(
                "Registration failed. Please try again.",
                "danger"
            )

    return render_template("register.html")


# =========================================================
# LOGIN AND LOGOUT
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username_or_email = (
            request.form.get("email")
            or request.form.get("username")
            or request.form.get("login")
            or ""
        ).strip()

        password = request.form.get(
            "password", ""
        )

        if not username_or_email or not password:
            flash(
                "Enter your email/username and password.",
                "danger"
            )

            return render_template("login.html")

        user = User.query.filter(
            or_(
                User.username.ilike(username_or_email),
                User.email.ilike(username_or_email)
            )
        ).first()

        if (
            user
            and user.password_hash
            and check_password_hash(
                user.password_hash,
                password
            )
        ):
            selected_currency = session.get(
                "currency"
            )

            session.clear()

            if selected_currency in SUPPORTED_CURRENCIES:
                session["currency"] = selected_currency

            session["user_id"] = user.id
            session["username"] = user.username
            session["role"] = user.role

            flash(
                f"Welcome, {user.full_name}!",
                "success"
            )

            if user.role == "admin":
                return redirect(
                    url_for("admin_dashboard")
                )

            return redirect(
                url_for("dashboard")
            )

        flash(
            "Invalid username/email or password.",
            "danger"
        )

    return render_template("login.html")


@app.route("/logout")
def logout():
    selected_currency = session.get(
        "currency"
    )

    session.clear()

    if selected_currency in SUPPORTED_CURRENCIES:
        session["currency"] = selected_currency

    flash(
        "You have logged out successfully.",
        "success"
    )

    return redirect(url_for("home"))


# =========================================================
# CUSTOMER DASHBOARD
# =========================================================

@app.route("/dashboard")

@app.route("/user_dashboard")
@login_required
def dashboard():
    user = get_current_user()

    if user.role == "admin":
        return redirect(url_for("admin_dashboard"))

    # Total customer orders
    total_orders = Order.query.filter_by(
        user_id=user.id
    ).count()

    # Cart count
    cart_count = get_cart_count(user.id)

    # Latest active products
    products = Product.query.filter_by(
        is_active=True
    ).order_by(
        Product.created_at.desc()
    ).limit(8).all()

    # Total active products
    total_products = Product.query.filter_by(
        is_active=True
    ).count()

    # Customer support conversations
    support_conversations = SupportConversation.query.filter_by(
        customer_id=user.id
    ).order_by(
        SupportConversation.created_at.desc()
    ).all()

    # Unread support messages
    unread_messages = SupportMessage.query.join(
        SupportConversation,
        SupportMessage.conversation_id == SupportConversation.id
    ).filter(
        SupportConversation.customer_id == user.id,
        SupportMessage.sender_id != user.id,
        SupportMessage.is_read.is_(False)
    ).count()

    # Customer notifications from database
    notifications = Notification.query.filter_by(
        user_id=user.id
    ).order_by(
        Notification.created_at.desc()
    ).all()

    # Unread notification count
    unread_notifications = Notification.query.filter_by(
        user_id=user.id,
        is_read=False
    ).count()

    return render_template(
        "user_dashboard.html",
        user=user,
        total_orders=total_orders,
        cart_count=cart_count,
        total_products=total_products,
        products=products,
        support_conversations=support_conversations,
        unread_messages=unread_messages,
        notifications=notifications,
        unread_notifications=unread_notifications
    )



# =========================================================
# ADMIN DASHBOARD
# =========================================================

@app.route("/admin")
@admin_required
def admin_dashboard():
    user = get_current_user()

    total_users = User.query.filter(
        User.role != "admin"
    ).count()

    total_products = Product.query.count()
    total_orders = Order.query.count()
    total_categories = Category.query.count()

    active_products = Product.query.filter_by(
        is_active=True
    ).count()

    inactive_products = Product.query.filter_by(
        is_active=False
    ).count()

    total_messages = SupportConversation.query.count()

    open_support = SupportConversation.query.filter_by(
        status="open"
    ).count()

    unread_support_messages = SupportMessage.query.join(
        SupportConversation,
        SupportMessage.conversation_id
        == SupportConversation.id
    ).filter(
        SupportMessage.sender_id
        == SupportConversation.customer_id,
        SupportMessage.is_read.is_(False)
    ).count()

    paid_order_ids = db.session.query(
        Payment.order_id
    ).filter(
        Payment.status == "paid"
    ).distinct()

    total_revenue = db.session.query(
        func.coalesce(
            func.sum(Order.total_amount),
            0
        )
    ).filter(
        Order.id.in_(paid_order_ids)
    ).scalar() or 0

    paid_order_item_query = (
        db.session.query(
            OrderItem.product_id,
            func.sum(
                OrderItem.quantity
            ).label("total_sold"),
            func.sum(
                OrderItem.quantity * OrderItem.unit_price
            ).label("total_revenue")
        )
        .join(
            Order,
            Order.id == OrderItem.order_id
        )
        .join(
            Payment,
            Payment.order_id == Order.id
        )
        .filter(
            Payment.status == "paid"
        )
        .group_by(
            OrderItem.product_id
        )
        .subquery()
    )

    top_products_query = (
        db.session.query(
            Product,
            paid_order_item_query.c.total_sold,
            paid_order_item_query.c.total_revenue
        )
        .join(
            paid_order_item_query,
            Product.id
            == paid_order_item_query.c.product_id
        )
        .order_by(
            paid_order_item_query.c.total_sold.desc()
        )
        .limit(4)
        .all()
    )

    top_products = []

    for product, total_sold, product_revenue in top_products_query:
        top_products.append({
            "name": product.name,
            "sales": total_sold,
            "revenue": product_revenue,
            "icon": product.icon or "📦"
        })

    recent_orders = Order.query.order_by(
        Order.created_at.desc()
    ).limit(5).all()

    context = {
        "user": user,
        "total_users": total_users,
        "total_products": total_products,
        "total_orders": total_orders,
        "total_categories": total_categories,
        "active_products": active_products,
        "inactive_products": inactive_products,
        "total_messages": total_messages,
        "open_support": open_support,
        "unread_support_messages": unread_support_messages,
        "total_revenue": total_revenue,
        "top_products": top_products,
        "recent_orders": recent_orders,
        "current_year": datetime.now().year
    }

    return render_admin_page(
        "_admin_dashboard.html",
        "admin_dashboard.html",
        **context
    )


# =========================================================
# PUBLIC PRODUCTS
# =========================================================

@app.route("/products")
def products():
    search = request.args.get(
        "search", ""
    ).strip()

    category_id = request.args.get(
        "category",
        type=int
    )

    query = Product.query.filter_by(
        is_active=True
    )

    if search:
        query = query.filter(
            Product.name.ilike(f"%{search}%")
        )

    if category_id:
        query = query.filter(
            Product.category_id == category_id
        )

    all_products = query.order_by(
        Product.created_at.desc()
    ).all()

    categories = Category.query.order_by(
        Category.name.asc()
    ).all()

    return render_template(
        "products.html",
        products=all_products,
        categories=categories,
        search=search,
        selected_category=category_id
    )


# =========================================================
# PRODUCTS API
# =========================================================

@app.route("/api/products")
def products_api():
    search = request.args.get(
        "search", ""
    ).strip()

    category_id = request.args.get(
        "category_id",
        type=int
    )

    query = Product.query.filter_by(
        is_active=True
    )

    if search:
        query = query.filter(
            Product.name.ilike(f"%{search}%")
        )

    if category_id:
        query = query.filter(
            Product.category_id == category_id
        )

    all_products = query.order_by(
        Product.created_at.desc()
    ).all()

    return jsonify({
        "success": True,
        "total": len(all_products),
        "products": [
            {
                "id": product.id,
                "name": product.name,
                "description": product.description or "",
                "price": float(product.price),
                "stock": product.stock,
                "category_id": product.category_id,
                "category": product.category_name,

                # SUPABASE AND LOCAL IMAGE SUPPORT
                "image": (
                    product.image
                    if product.image and product.image.startswith(
                        ("https://", "http://")
                    )
                    else (
                        url_for(
                            "static",
                            filename=(
                                f"uploads/products/{product.image}"
                            )
                        )
                        if product.image else None
                    )
                ),

                "icon": product.icon or "📦",
                "tag": product.tag or "",
                "is_active": product.is_active
            }
            for product in all_products
        ]
    })

# =========================================================
# ADMIN PRODUCT MANAGEMENT
# =========================================================

@app.route("/admin/products")
@admin_required
def admin_products():
    search = request.args.get(
        "search", ""
    ).strip()

    query = Product.query

    if search:
        query = query.filter(
            Product.name.ilike(f"%{search}%")
        )

    all_products = query.order_by(
        Product.created_at.desc()
    ).all()

    return render_admin_page(
        "_admin_products.html",
        "admin_dashboard.html",
        products=all_products,
        search=search,
        user=get_current_user(),
        current_year=datetime.now().year
    )


# =========================================================
# ADD PRODUCT
# =========================================================

@app.route(
    "/admin/products/add",
    methods=["GET", "POST"]
)
@admin_required
def add_product():
    categories = Category.query.order_by(
        Category.name.asc()
    ).all()

    if request.method == "GET":
        return render_add_product_page(categories)

    image_name = None

    try:
        product_data = parse_product_form()

        image_name = save_product_image(
            request.files.get("image")
        )

        product = Product(
            name=product_data["name"],
            description=product_data["description"],
            price=product_data["price"],
            stock=product_data["stock"],
            category_id=product_data["category_id"],
            image=image_name,
            icon=product_data["icon"],
            tag=product_data["tag"],
            is_active=True
        )

        db.session.add(product)
        db.session.commit()

        return admin_form_response(
            True,
            "Product added successfully.",
            url_for("admin_products"),
            201,
            product_id=product.id
        )

    except ValueError as error:
        db.session.rollback()
        delete_product_image(image_name)

        if is_ajax_request():
            return ajax_json(
                False,
                str(error),
                400
            )

        flash(str(error), "danger")

        return render_add_product_page(categories)

    except IntegrityError:
        db.session.rollback()
        delete_product_image(image_name)

        app.logger.exception(
            "Product database constraint failed."
        )

        message = (
            "A product with conflicting information already exists."
        )

        if is_ajax_request():
            return ajax_json(
                False,
                message,
                409
            )

        flash(message, "danger")

        return render_add_product_page(categories)

    except Exception:
        db.session.rollback()
        delete_product_image(image_name)

        app.logger.exception(
            "Unable to add product."
        )

        message = (
            "Unable to add product. Please try again."
        )

        if is_ajax_request():
            return ajax_json(
                False,
                message,
                500
            )

        flash(message, "danger")

        return render_add_product_page(categories)


# =========================================================
# EDIT PRODUCT
# =========================================================

@app.route(
    "/admin/products/edit/<int:product_id>",
    methods=["GET", "POST"]
)
@admin_required
def edit_product(product_id):
    product = db.get_or_404(
        Product,
        product_id
    )

    categories = Category.query.order_by(
        Category.name.asc()
    ).all()

    if request.method == "GET":
        return render_edit_product_page(
            product,
            categories
        )

    new_image = None
    old_image = product.image

    try:
        product_data = parse_product_form()
        image_file = request.files.get("image")

        if image_file and image_file.filename:
            new_image = save_product_image(
                image_file
            )

        product.name = product_data["name"]
        product.description = product_data["description"]
        product.price = product_data["price"]
        product.stock = product_data["stock"]
        product.category_id = product_data["category_id"]
        product.tag = product_data["tag"]
        product.icon = product_data["icon"]

        if new_image:
            product.image = new_image

        db.session.commit()

        if new_image and old_image:
            delete_product_image(old_image)

        return admin_form_response(
            True,
            "Product updated successfully.",
            url_for("admin_products")
        )

    except ValueError as error:
        db.session.rollback()
        delete_product_image(new_image)

        if is_ajax_request():
            return ajax_json(
                False,
                str(error),
                400
            )

        flash(str(error), "danger")

    except IntegrityError:
        db.session.rollback()
        delete_product_image(new_image)

        app.logger.exception(
            "Product update database error."
        )

        message = (
            "A product with conflicting information already exists."
        )

        if is_ajax_request():
            return ajax_json(
                False,
                message,
                409
            )

        flash(message, "danger")

    except Exception:
        db.session.rollback()
        delete_product_image(new_image)

        app.logger.exception(
            "Product update failed."
        )

        message = "Unable to update product."

        if is_ajax_request():
            return ajax_json(
                False,
                message,
                500
            )

        flash(message, "danger")

    return render_edit_product_page(
        product,
        categories
    )


# =========================================================
# DELETE PRODUCT
# =========================================================

@app.route(
    "/admin/products/delete/<int:product_id>",
    methods=["POST"]
)
@admin_required
def delete_product(product_id):
    product = db.get_or_404(
        Product,
        product_id
    )

    if product.order_items:
        product.is_active = False

        try:
            db.session.commit()

            return admin_form_response(
                True,
                "Product deactivated because it has order history.",
                url_for("admin_products")
            )

        except Exception:
            db.session.rollback()

            app.logger.exception(
                "Product deactivation failed."
            )

            return admin_form_response(
                False,
                "Unable to deactivate product.",
                status_code=500
            )

    image_name = product.image

    try:
        db.session.delete(product)
        db.session.commit()

        delete_product_image(image_name)

        return admin_form_response(
            True,
            "Product deleted successfully.",
            url_for("admin_products")
        )

    except Exception:
        db.session.rollback()

        app.logger.exception(
            "Product deletion failed."
        )

        return admin_form_response(
            False,
            "Unable to delete product.",
            status_code=500
        )


# =========================================================
# ADMIN PAYMENTS
# =========================================================

@app.route("/admin/payments")
@admin_required
def admin_payments():
    payments = Payment.query.order_by(
        Payment.created_at.desc()
    ).all()

    pending_payments = Payment.query.filter_by(
        status="pending"
    ).count()

    paid_payments = Payment.query.filter_by(
        status="paid"
    ).count()

    failed_payments = Payment.query.filter_by(
        status="failed"
    ).count()

    context = {
        "payments": payments,
        "pending_payments": pending_payments,
        "paid_payments": paid_payments,
        "failed_payments": failed_payments,
        "user": get_current_user(),
        "current_year": datetime.now().year
    }

    return render_admin_page(
        "_admin_payments.html",
        "admin_dashboard.html",
        **context
    )


# =========================================================
# ADMIN NOTIFICATIONS API
# =========================================================

@app.route("/api/admin/notifications")
@admin_required
def admin_notifications():
    unread_support = SupportMessage.query.join(
        SupportConversation,
        SupportMessage.conversation_id
        == SupportConversation.id
    ).filter(
        SupportMessage.sender_id
        == SupportConversation.customer_id,
        SupportMessage.is_read.is_(False)
    ).count()

    pending_payments = Payment.query.filter_by(
        status="pending"
    ).count()

    pending_orders = Order.query.filter_by(
        status="pending"
    ).count()

    return jsonify({
        "success": True,
        "unread_count": unread_support,
        "pending_payments": pending_payments,
        "pending_orders": pending_orders
    })


# =========================================================
# CART
# =========================================================

@app.route(
    "/cart/add/<int:product_id>",
    methods=["POST"]
)
@login_required
def add_to_cart(product_id):
    user = get_current_user()

    if user.role == "admin":
        flash(
            "Only customers can add products to the cart.",
            "warning"
        )

        return redirect(url_for("home"))

    product = db.get_or_404(
        Product,
        product_id
    )

    if not product.is_active:
        flash(
            "This product is unavailable.",
            "danger"
        )

        return redirect(url_for("products"))

    quantity = request.form.get(
        "quantity",
        default=1,
        type=int
    )

    if not quantity or quantity < 1:
        quantity = 1

    if product.stock < quantity:
        flash(
            "Not enough stock is available.",
            "warning"
        )

        return redirect(
            request.referrer or url_for("products")
        )

    try:
        locked_product = Product.query.filter_by(
            id=product_id
        ).with_for_update().first()

        if (
            not locked_product
            or not locked_product.is_active
            or locked_product.stock < quantity
        ):
            db.session.rollback()

            flash(
                "Requested quantity is not available.",
                "warning"
            )

            return redirect(
                request.referrer or url_for("products")
            )

        cart_item = CartItem.query.filter_by(
            user_id=user.id,
            product_id=product.id
        ).with_for_update().first()

        if cart_item:
            new_quantity = (
                cart_item.quantity + quantity
            )

            if new_quantity > locked_product.stock:
                db.session.rollback()

                flash(
                    "Requested quantity exceeds available stock.",
                    "warning"
                )

                return redirect(
                    request.referrer or url_for("products")
                )

            cart_item.quantity = new_quantity

        else:
            db.session.add(
                CartItem(
                    user_id=user.id,
                    product_id=product.id,
                    quantity=quantity
                )
            )

        db.session.commit()

        flash(
            "Product added to your cart.",
            "success"
        )

    except IntegrityError:
        db.session.rollback()

        app.logger.exception(
            "Cart insert conflict."
        )

        flash(
            "Your cart changed at the same time. Please try again.",
            "warning"
        )

    except Exception:
        db.session.rollback()

        app.logger.exception(
            "Add to cart failed."
        )

        flash(
            "Unable to add product to cart.",
            "danger"
        )

    return redirect(
        request.referrer or url_for("cart")
    )


@app.route("/cart")
@login_required
def cart():
    user = get_current_user()

    if user.role == "admin":
        return redirect(
            url_for("admin_dashboard")
        )

    cart_items = get_cart_items(user.id)
    total = get_cart_total(user.id)

    return render_template(
        "cart.html",
        cart_items=cart_items,
        total=total,
        cart_total=total,
        cart_count=get_cart_count(user.id)
    )


@app.route(
    "/cart/update/<int:item_id>",
    methods=["POST"]
)
@login_required
def update_cart(item_id):
    user = get_current_user()

    cart_item = CartItem.query.filter_by(
        id=item_id,
        user_id=user.id
    ).first_or_404()

    quantity = request.form.get(
        "quantity",
        type=int
    )

    if not quantity or quantity < 1:
        flash(
            "Quantity must be at least one.",
            "danger"
        )

        return redirect(url_for("cart"))

    try:
        product = Product.query.filter_by(
            id=cart_item.product_id
        ).with_for_update().first()

        if (
            not product
            or not product.is_active
            or quantity > product.stock
        ):
            db.session.rollback()

            flash(
                "Requested quantity is not available.",
                "warning"
            )

            return redirect(url_for("cart"))

        cart_item.quantity = quantity

        db.session.commit()

        flash(
            "Cart updated successfully.",
            "success"
        )

    except Exception:
        db.session.rollback()

        app.logger.exception(
            "Cart update failed."
        )

        flash(
            "Unable to update cart.",
            "danger"
        )

    return redirect(url_for("cart"))


@app.route(
    "/cart/remove/<int:item_id>",
    methods=["POST"]
)
@login_required
def remove_from_cart(item_id):
    user = get_current_user()

    cart_item = CartItem.query.filter_by(
        id=item_id,
        user_id=user.id
    ).first_or_404()

    try:
        db.session.delete(cart_item)
        db.session.commit()

        flash(
            "Product removed from cart.",
            "success"
        )

    except Exception:
        db.session.rollback()

        app.logger.exception(
            "Cart removal failed."
        )

        flash(
            "Unable to remove product.",
            "danger"
        )

    return redirect(url_for("cart"))


# =========================================================
# CHECKOUT PAGE
# =========================================================

@app.route(
    "/checkout",
    methods=["GET"]
)
@login_required
def checkout():
    user = get_current_user()

    if user.role == "admin":
        return redirect(
            url_for("admin_dashboard")
        )

    cart_items = get_cart_items(user.id)

    if not cart_items:
        flash(
            "Your cart is empty.",
            "warning"
        )

        return redirect(url_for("products"))

    for item in cart_items:
        if not item.product or not item.product.is_active:
            flash(
                "A product in your cart is unavailable.",
                "danger"
            )

            return redirect(url_for("cart"))

        if item.quantity > item.product.stock:
            flash(
                f"Insufficient stock for {item.product.name}.",
                "warning"
            )

            return redirect(url_for("cart"))

    total = get_cart_total(user.id)

    return render_template(
        "checkout.html",
        user=user,
        cart_items=cart_items,
        total=total,
        cart_total=total,
        cbe_birr_account_name=CBE_BIRR_ACCOUNT_NAME,
        cbe_birr_account_number=CBE_BIRR_ACCOUNT_NUMBER,
        telebirr_account_name=TELEBIRR_ACCOUNT_NAME,
        telebirr_phone_number=TELEBIRR_PHONE_NUMBER
    )


# =========================================================
# CREATE ORDER AND PAYMENT
# =========================================================

@app.route(
    "/checkout",
    methods=["POST"]
)
@login_required
def create_checkout_payment():
    user = get_current_user()

    if user.role == "admin":
        return redirect(
            url_for("admin_dashboard")
        )

    payment_method = request.form.get(
        "payment_method",
        "chapa"
    ).strip().lower()

    allowed_payment_methods = {
        "chapa",
        "cbe_birr",
        "telebirr"
    }

    if payment_method not in allowed_payment_methods:
        flash(
            "Select a valid payment method.",
            "danger"
        )

        return redirect(url_for("checkout"))

    if payment_method == "chapa" and not CHAPA_SECRET_KEY:
        flash(
            "Chapa payment is not configured.",
            "danger"
        )

        return redirect(url_for("checkout"))

    if payment_method == "cbe_birr" and (
        not CBE_BIRR_ACCOUNT_NAME
        or not CBE_BIRR_ACCOUNT_NUMBER
    ):
        flash(
            "CBE Birr payment is not configured.",
            "danger"
        )

        return redirect(url_for("checkout"))

    if payment_method == "telebirr" and (
        not TELEBIRR_ACCOUNT_NAME
        or not TELEBIRR_PHONE_NUMBER
    ):
        flash(
            "Telebirr payment is not configured.",
            "danger"
        )

        return redirect(url_for("checkout"))

    cart_items = CartItem.query.filter_by(
        user_id=user.id
    ).order_by(
        CartItem.id.asc()
    ).with_for_update().all()

    if not cart_items:
        flash(
            "Your cart is empty.",
            "warning"
        )

        return redirect(url_for("cart"))

    recipient_name = request.form.get(
        "recipient_name",
        user.full_name
    ).strip()

    recipient_phone = normalize_phone(
        request.form.get("recipient_phone", "")
    )

    delivery_address = request.form.get(
        "delivery_address", ""
    ).strip()

    delivery_city = request.form.get(
        "delivery_city", ""
    ).strip()

    delivery_notes = request.form.get(
        "delivery_notes", ""
    ).strip()

    if not all([
        recipient_name,
        recipient_phone,
        delivery_address,
        delivery_city
    ]):
        db.session.rollback()

        flash(
            "Please complete all delivery information.",
            "danger"
        )

        return redirect(url_for("checkout"))

    if len(recipient_name) > 150:
        db.session.rollback()

        flash(
            "Recipient name is too long.",
            "danger"
        )

        return redirect(url_for("checkout"))

    if (
        len(delivery_address) > 500
        or len(delivery_city) > 100
    ):
        db.session.rollback()

        flash(
            "Delivery information is too long.",
            "danger"
        )

        return redirect(url_for("checkout"))

    if len(delivery_notes) > 1000:
        db.session.rollback()

        flash(
            "Delivery notes are too long.",
            "danger"
        )

        return redirect(url_for("checkout"))

    if (
        len(recipient_phone) != 10
        or not recipient_phone.startswith(("09", "07"))
    ):
        db.session.rollback()

        flash(
            "Enter a valid Ethiopian mobile number.",
            "danger"
        )

        return redirect(url_for("checkout"))

    if not user.email:
        db.session.rollback()

        flash(
            "Your account needs a valid email address before checkout.",
            "danger"
        )

        return redirect(url_for("checkout"))

    total = Decimal("0.00")
    receipt_filename = None

    try:
        product_ids = sorted({
            item.product_id
            for item in cart_items
        })

        locked_products = {}

        for product_id in product_ids:
            product = Product.query.filter_by(
                id=product_id
            ).with_for_update().first()

            if not product or not product.is_active:
                raise ValueError(
                    "A product in your cart is unavailable."
                )

            locked_products[product_id] = product

        for item in cart_items:
            product = locked_products.get(
                item.product_id
            )

            if not product:
                raise ValueError(
                    "A product in your cart is unavailable."
                )

            if item.quantity > product.stock:
                raise ValueError(
                    f"Insufficient stock for {product.name}."
                )

            total += (
                Decimal(str(product.price))
                * item.quantity
            )

        if not total.is_finite() or total <= 0:
            raise ValueError(
                "Invalid order amount."
            )

        provider_reference = ""

        if payment_method in {
            "cbe_birr",
            "telebirr"
        }:
            provider_reference = (
                request.form.get("provider_reference")
                or request.form.get("transaction_reference")
                or ""
            ).strip()

            receipt_file = (
                request.files.get("receipt")
                or request.files.get("receipt_image")
            )

            if not provider_reference:
                raise ValueError(
                    "Enter your transfer transaction reference."
                )

            if len(provider_reference) > 255:
                raise ValueError(
                    "Transaction reference is too long."
                )

            duplicate_reference = Payment.query.filter(
                Payment.provider == payment_method,
                func.lower(
                    Payment.provider_reference
                ) == provider_reference.lower()
            ).first()

            if duplicate_reference:
                raise ValueError(
                    "This transaction reference has already been submitted."
                )

            if not receipt_file or not receipt_file.filename:
                raise ValueError(
                    "Upload your payment receipt image."
                )

            receipt_filename = save_payment_receipt(
                receipt_file
            )

        payment_currency = CHAPA_CURRENCY

        first_name, last_name = split_full_name(
            recipient_name
        )

        tx_ref = (
            "SHOPNOVA-"
            + uuid.uuid4().hex.upper()
        )

        order = Order(
            user_id=user.id,
            total_amount=total,
            status="pending"
        )

        db.session.add(order)
        db.session.flush()

        for item in cart_items:
            product = locked_products[
                item.product_id
            ]

            db.session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    quantity=item.quantity,
                    unit_price=Decimal(
                        str(product.price)
                    )
                )
            )

        payment = Payment(
            order_id=order.id,
            provider=payment_method,
            tx_ref=tx_ref,
            provider_reference=(
                provider_reference
                if payment_method != "chapa"
                else None
            ),
            amount=total,
            currency=payment_currency,
            status=(
                "initiated"
                if payment_method == "chapa"
                else "pending"
            ),
            recipient_name=recipient_name,
            recipient_phone=recipient_phone,
            delivery_address=delivery_address,
            delivery_city=delivery_city,
            delivery_notes=delivery_notes,
            receipt_filename=receipt_filename,
            receipt_uploaded_at=(
                datetime.now(timezone.utc)
                if receipt_filename else None
            ),
            admin_note=None
        )

        db.session.add(payment)
        db.session.commit()

    except ValueError as error:
        db.session.rollback()

        delete_payment_receipt(
            receipt_filename
        )

        flash(
            str(error),
            "warning"
        )

        return redirect(url_for("checkout"))

    except IntegrityError:
        db.session.rollback()

        delete_payment_receipt(
            receipt_filename
        )

        app.logger.exception(
            "Checkout database constraint failed."
        )

        flash(
            "The order could not be created. Please try again.",
            "danger"
        )

        return redirect(url_for("checkout"))

    except Exception:
        db.session.rollback()

        delete_payment_receipt(
            receipt_filename
        )

        app.logger.exception(
            "Unable to create order."
        )

        flash(
            "Unable to create your order. Please try again.",
            "danger"
        )

        return redirect(url_for("checkout"))

    # -----------------------------------------------------
    # MANUAL PAYMENT: CBE BIRR AND TELEBIRR
    # -----------------------------------------------------

    if payment_method in {
        "cbe_birr",
        "telebirr"
    }:
        flash(
            "Your order was submitted. Your payment receipt is "
            "waiting for administrator verification. Your order "
            "will not be processed until payment is independently verified.",
            "success"
        )

        return redirect(
            url_for(
                "order_details",
                order_id=order.id
            )
        )

    # -----------------------------------------------------
    # CHAPA CHECKOUT INITIALIZATION
    # -----------------------------------------------------

    payload = {
        "amount": str(total),
        "currency": payment_currency,
        "email": user.email,
        "first_name": first_name,
        "last_name": last_name,
        "phone_number": recipient_phone,
        "tx_ref": tx_ref,
        "callback_url": public_url(
            "chapa_callback",
            tx_ref=tx_ref
        ),
        "return_url": public_url(
            "chapa_return"
        ),
        "customization": {
            "title": "ShopNova",
            "description": (
                f"Payment for ShopNova order #{order.id}"
            )
        },
        "meta": {
            "order_id": order.id,
            "customer_id": user.id
        }
    }

    try:
        chapa_response = chapa_initialize(
            payload
        )

        if not isinstance(chapa_response, dict):
            raise RuntimeError(
                "Invalid response from Chapa."
            )

        if str(
            chapa_response.get("status", "")
        ).lower() != "success":
            raise RuntimeError(
                chapa_response.get(
                    "message",
                    "Chapa could not initialize payment."
                )
            )

        response_data = (
            chapa_response.get("data") or {}
        )

        checkout_url = response_data.get(
            "checkout_url"
        )

        if not checkout_url:
            raise RuntimeError(
                "Chapa did not return a checkout URL."
            )

        parsed_checkout = urlsplit(
            checkout_url
        )

        allowed_chapa_hosts = {
            "chapa.co",
            "checkout.chapa.co"
        }

        if (
            parsed_checkout.scheme != "https"
            or parsed_checkout.hostname
            not in allowed_chapa_hosts
        ):
            raise RuntimeError(
                "Chapa returned an invalid checkout URL."
            )

        payment = get_payment_by_reference(
            tx_ref
        )

        if not payment:
            raise RuntimeError(
                "Payment record was not found."
            )

        payment.checkout_url = checkout_url
        payment.status = "pending"

        db.session.commit()

        return redirect(checkout_url)

    except Exception:
        db.session.rollback()

        payment = get_payment_by_reference(
            tx_ref
        )

        if payment and payment.status != "paid":
            payment.status = "failed"

            try:
                db.session.commit()

            except Exception:
                db.session.rollback()

                app.logger.exception(
                    "Unable to update failed payment status."
                )

        app.logger.exception(
            "Chapa payment initialization failed."
        )

        flash(
            "Payment initialization failed. Your order was saved, "
            "but payment was not started. Please contact support.",
            "danger"
        )

        return redirect(
            url_for("my_orders")
        )


# =========================================================
# CHAPA CALLBACK
# =========================================================

@app.route(
    "/payment/chapa/callback/<tx_ref>",
    methods=["GET", "POST"]
)
def chapa_callback(tx_ref):
    payment = get_payment_by_reference(
        tx_ref
    )

    if not payment:
        return jsonify({
            "success": False,
            "message": "Payment reference not found."
        }), 404

    if payment.provider != "chapa":
        return jsonify({
            "success": False,
            "message": "Invalid payment provider."
        }), 400

    try:
        verified = verify_payment_record(
            payment
        )

        if verified:
            return jsonify({
                "success": True,
                "message": "Payment successfully verified.",
                "order_id": payment.order_id
            }), 200

        return jsonify({
            "success": False,
            "message": "Payment is not yet confirmed."
        }), 202

    except Exception:
        db.session.rollback()

        app.logger.exception(
            "Chapa callback verification failed."
        )

        return jsonify({
            "success": False,
            "message": "Unable to verify payment."
        }), 500


# =========================================================
# CHAPA RETURN PAGE
# =========================================================

@app.route("/payment/chapa/return")
@login_required
def chapa_return():
    tx_ref = (
        request.args.get("tx_ref")
        or request.args.get("trx_ref")
        or request.args.get("reference")
    )

    if not tx_ref:
        flash(
            "Payment reference was not provided.",
            "warning"
        )

        return redirect(url_for("my_orders"))

    user = get_current_user()

    payment = get_payment_for_user(
        tx_ref,
        user
    )

    if not payment:
        flash(
            "Payment record was not found.",
            "danger"
        )

        return redirect(url_for("my_orders"))

    try:
        verified = verify_payment_record(
            payment
        )

        if verified:
            flash(
                "Payment verified successfully. Your order is being processed.",
                "success"
            )

        else:
            flash(
                "Your payment has not been confirmed yet. Check your order later.",
                "warning"
            )

    except Exception:
        db.session.rollback()

        app.logger.exception(
            "Payment return verification failed."
        )

        flash(
            "Unable to verify your payment at this time.",
            "warning"
        )

    return redirect(
        url_for(
            "order_details",
            order_id=payment.order_id
        )
    )


# =========================================================
# ADMIN: APPROVE MANUAL PAYMENT
# =========================================================

@app.route(
    "/admin/payments/<int:payment_id>/approve",
    methods=["POST"]
)
@admin_required
def approve_manual_payment(payment_id):
    try:
        payment = Payment.query.filter_by(
            id=payment_id
        ).with_for_update().first()

        if not payment:
            return admin_form_response(
                False,
                "Payment was not found.",
                status_code=404
            )

        if payment.provider not in {
            "cbe_birr",
            "telebirr"
        }:
            return admin_form_response(
                False,
                "Only CBE Birr and Telebirr payments can be approved manually.",
                status_code=400
            )

        if payment.status != "pending":
            return admin_form_response(
                False,
                "This payment is no longer awaiting verification.",
                status_code=400
            )

        if not payment.provider_reference:
            return admin_form_response(
                False,
                "The customer's transaction reference is missing.",
                status_code=400
            )

        if not payment.receipt_filename:
            return admin_form_response(
                False,
                "The customer's receipt is missing.",
                status_code=400
            )

        receipt_path = (
            PRIVATE_RECEIPT_FOLDER
            / os.path.basename(
                payment.receipt_filename
            )
        )

        if not receipt_path.is_file():
            return admin_form_response(
                False,
                "The uploaded receipt file could not be found.",
                status_code=400
            )

        order = Order.query.filter_by(
            id=payment.order_id
        ).with_for_update().first()

        if not order:
            return admin_form_response(
                False,
                "The associated order was not found.",
                status_code=404
            )

        if order.status == "cancelled":
            return admin_form_response(
                False,
                "A cancelled order cannot be approved.",
                status_code=400
            )

        try:
            payment_amount = Decimal(
                str(payment.amount)
            )

            order_amount = Decimal(
                str(order.total_amount)
            )

        except (InvalidOperation, TypeError, ValueError):
            return admin_form_response(
                False,
                "Invalid payment amount.",
                status_code=400
            )

        if (
            payment.currency != "ETB"
            or not payment_amount.is_finite()
            or payment_amount != order_amount
        ):
            return admin_form_response(
                False,
                "Payment amount or currency does not match the order.",
                status_code=400
            )

        admin_note = request.form.get(
            "admin_note", ""
        ).strip()

        if len(admin_note) > 2000:
            return admin_form_response(
                False,
                "Admin note is too long.",
                status_code=400
            )

        # IMPORTANT:
        # The administrator must independently verify the
        # actual transaction in the merchant account.
        # An uploaded receipt is not proof of payment.

        another_paid_payment = Payment.query.filter(
            Payment.order_id == order.id,
            Payment.id != payment.id,
            Payment.status == "paid"
        ).first()

        if another_paid_payment:
            return admin_form_response(
                False,
                "This order already has another verified payment.",
                status_code=400
            )

        # The payment row and order row are locked.
        # A second approval cannot deduct stock again because
        # the payment status changes from pending to paid.
        deduct_order_stock_and_update_cart(order)

        payment.status = "paid"

        payment.verified_at = datetime.now(
            timezone.utc
        )

        payment.admin_note = (
            admin_note
            or "Manually verified by administrator."
        )

        order.status = "processing"

        db.session.commit()

        return admin_form_response(
            True,
            f"Payment #{payment.id} approved. "
            f"Order #{order.id} is processing.",
            url_for(
                "admin_order_details",
                order_id=order.id
            )
        )

    except HTTPException:
        db.session.rollback()
        raise

    except Exception:
        db.session.rollback()

        app.logger.exception(
            "Manual payment approval failed."
        )

        return admin_form_response(
            False,
            "Unable to approve payment. Check the payment and stock, then try again.",
            status_code=500
        )


# =========================================================
# ADMIN: REJECT MANUAL PAYMENT
# =========================================================

@app.route(
    "/admin/payments/<int:payment_id>/reject",
    methods=["POST"]
)
@admin_required
def reject_manual_payment(payment_id):
    try:
        payment = Payment.query.filter_by(
            id=payment_id
        ).with_for_update().first()

        if not payment:
            return admin_form_response(
                False,
                "Payment was not found.",
                status_code=404
            )

        if payment.provider not in {
            "cbe_birr",
            "telebirr"
        }:
            return admin_form_response(
                False,
                "Only manual payments can be rejected here.",
                status_code=400
            )

        if payment.status != "pending":
            return admin_form_response(
                False,
                "This payment is no longer awaiting verification.",
                status_code=400
            )

        admin_note = request.form.get(
            "admin_note", ""
        ).strip()

        if len(admin_note) > 2000:
            return admin_form_response(
                False,
                "Admin note is too long.",
                status_code=400
            )

        payment.status = "failed"

        payment.admin_note = (
            admin_note
            or "Manual payment was not approved by administrator."
        )

        db.session.commit()

        return admin_form_response(
            True,
            f"Payment #{payment.id} rejected.",
            url_for(
                "admin_order_details",
                order_id=payment.order_id
            )
        )

    except HTTPException:
        db.session.rollback()
        raise

    except Exception:
        db.session.rollback()

        app.logger.exception(
            "Manual payment rejection failed."
        )

        return admin_form_response(
            False,
            "Unable to reject payment.",
            status_code=500
        )


# =========================================================
# CUSTOMER ORDERS
# =========================================================

@app.route("/my-orders")
@app.route("/orders")
@login_required
def my_orders():
    user = get_current_user()

    orders = Order.query.filter_by(
        user_id=user.id
    ).order_by(
        Order.created_at.desc()
    ).all()

    return render_template(
        "my_orders.html",
        orders=orders
    )


@app.route("/orders/<int:order_id>")
@login_required
def order_details(order_id):
    user = get_current_user()

    order = Order.query.filter_by(
        id=order_id,
        user_id=user.id
    ).first_or_404()

    payments = Payment.query.filter_by(
        order_id=order.id
    ).order_by(
        Payment.created_at.desc()
    ).all()

    return render_template(
        "order_details.html",
        order=order,
        payments=payments
    )


# =========================================================
# ADMIN ORDERS
# =========================================================

@app.route("/admin/orders")
@admin_required
def admin_orders():
    search = request.args.get(
        "search", ""
    ).strip()

    selected_status = request.args.get(
        "status", ""
    ).strip()

    query = Order.query

    if search:
        if search.isdigit():
            query = query.filter(
                Order.id == int(search)
            )

        else:
            matching_users = User.query.filter(
                User.full_name.ilike(
                    f"%{search}%"
                )
            ).all()

            user_ids = [
                user.id
                for user in matching_users
            ]

            if user_ids:
                query = query.filter(
                    Order.user_id.in_(user_ids)
                )

            else:
                query = query.filter(
                    Order.id == -1
                )

    if selected_status:
        query = query.filter(
            Order.status == selected_status
        )

    orders = query.order_by(
        Order.created_at.desc()
    ).all()

    customers = {
        user.id: user
        for user in User.query.all()
    }

    payments = {}

    for payment in Payment.query.order_by(
        Payment.created_at.desc()
    ).all():
        if payment.order_id not in payments:
            payments[payment.order_id] = payment

    return render_admin_page(
        "_admin_orders.html",
        "admin_dashboard.html",
        orders=orders,
        customers=customers,
        payments=payments,
        search=search,
        selected_status=selected_status,
        user=get_current_user(),
        current_year=datetime.now().year
    )


# FIXED:
# Removed the duplicate route decorator.
# Uses _admin_order_details.html instead of the
# missing admin_order_details.html.

@app.route("/admin/orders/<int:order_id>")
@admin_required
def admin_order_details(order_id):
    order = db.get_or_404(
        Order,
        order_id
    )

    customer = db.session.get(
        User,
        order.user_id
    )

    payments = Payment.query.filter_by(
        order_id=order.id
    ).order_by(
        Payment.created_at.desc()
    ).all()

    context = {
        "order": order,
        "customer": customer,
        "payments": payments,
        "user": get_current_user(),
        "current_year": datetime.now().year
    }

    if is_ajax_request():
        try:
            app.jinja_env.get_template(
                "_admin_order_details.html"
            )

            return render_template(
                "_admin_order_details.html",
                **context
            )

        except Exception:
            app.logger.exception(
                "Missing _admin_order_details.html"
            )

            return make_response(
                """
                <div class="alert alert-warning">
                    Create templates/_admin_order_details.html
                    for AJAX order details.
                </div>
                """,
                500
            )

    return render_template(
        "admin_dashboard.html",
        initial_content_template="_admin_order_details.html",
        **context
    )


# =========================================================
# ADMIN UPDATE ORDER STATUS
# =========================================================

@app.route(
    "/admin/orders/update-status/<int:order_id>",
    methods=["POST"]
)
@admin_required
def update_order_status(order_id):
    order = Order.query.filter_by(
        id=order_id
    ).with_for_update().first_or_404()

    new_status = request.form.get(
        "status", ""
    ).strip().lower()

    allowed_statuses = {
        "pending",
        "processing",
        "shipped",
        "delivered",
        "cancelled"
    }

    if new_status not in allowed_statuses:
        return admin_form_response(
            False,
            "Invalid order status.",
            status_code=400
        )

    if order.status == "cancelled":
        return admin_form_response(
            False,
            "A cancelled order cannot be updated.",
            status_code=400
        )

    if order.status == "delivered":
        return admin_form_response(
            False,
            "A delivered order cannot be changed.",
            status_code=400
        )

    paid_payment = Payment.query.filter_by(
        order_id=order.id,
        status="paid"
    ).first()

    if (
        new_status in {
            "processing",
            "shipped",
            "delivered"
        }
        and not paid_payment
    ):
        return admin_form_response(
            False,
            "This order cannot be processed because payment has not been verified.",
            status_code=400
        )

    if new_status == "cancelled" and paid_payment:
        return admin_form_response(
            False,
            "This order has already been paid. Handle the refund before cancelling it.",
            status_code=400
        )

    order.status = new_status

    try:
        db.session.commit()

        return admin_form_response(
            True,
            f"Order #{order.id} status updated to {new_status}.",
            url_for("admin_orders")
        )

    except Exception:
        db.session.rollback()

        app.logger.exception(
            "Order status update failed."
        )

        return admin_form_response(
            False,
            "Unable to update order status.",
            status_code=500
        )


# =========================================================
# CUSTOMER SUPPORT
# =========================================================

@app.route(
    "/support",
    methods=["GET", "POST"]
)
@login_required
def support():
    user = get_current_user()

    if user.role == "admin":
        return redirect(
            url_for("admin_support")
        )

    if request.method == "POST":
        subject = request.form.get(
            "subject", ""
        ).strip()

        message = request.form.get(
            "message", ""
        ).strip()

        if not subject or not message:
            flash(
                "Please enter a subject and message.",
                "danger"
            )

            return redirect(url_for("support"))

        if len(subject) > 200 or len(message) > 10000:
            flash(
                "Your subject or message is too long.",
                "danger"
            )

            return redirect(url_for("support"))

        conversation = SupportConversation(
            customer_id=user.id,
            subject=subject,
            status="open"
        )

        try:
            db.session.add(conversation)
            db.session.flush()

            db.session.add(
                SupportMessage(
                    conversation_id=conversation.id,
                    sender_id=user.id,
                    message=message,
                    is_read=False
                )
            )

            db.session.commit()

            flash(
                "Your support message has been submitted.",
                "success"
            )

            return redirect(
                url_for(
                    "support_conversation",
                    conversation_id=conversation.id
                )
            )

        except Exception:
            db.session.rollback()

            app.logger.exception(
                "Support conversation creation failed."
            )

            flash(
                "Unable to submit your message.",
                "danger"
            )

    conversations = SupportConversation.query.filter_by(
        customer_id=user.id
    ).order_by(
        SupportConversation.created_at.desc()
    ).all()

    return render_template(
        "support.html",
        conversations=conversations
    )


@app.route(
    "/support/<int:conversation_id>",
    methods=["GET", "POST"]
)
@login_required
def support_conversation(conversation_id):
    user = get_current_user()

    conversation = SupportConversation.query.filter_by(
        id=conversation_id,
        customer_id=user.id
    ).first_or_404()

    if request.method == "POST":
        message_text = request.form.get(
            "message", ""
        ).strip()

        if not message_text:
            flash(
                "Message cannot be empty.",
                "danger"
            )

            return redirect(
                url_for(
                    "support_conversation",
                    conversation_id=conversation.id
                )
            )

        if len(message_text) > 10000:
            flash(
                "Message is too long.",
                "danger"
            )

            return redirect(
                url_for(
                    "support_conversation",
                    conversation_id=conversation.id
                )
            )

        if conversation.status == "closed":
            flash(
                "This conversation is closed.",
                "warning"
            )

            return redirect(
                url_for(
                    "support_conversation",
                    conversation_id=conversation.id
                )
            )

        try:
            db.session.add(
                SupportMessage(
                    conversation_id=conversation.id,
                    sender_id=user.id,
                    message=message_text,
                    is_read=False
                )
            )

            db.session.commit()

            flash(
                "Message sent.",
                "success"
            )

        except Exception:
            db.session.rollback()

            app.logger.exception(
                "Customer support message failed."
            )

            flash(
                "Unable to send message.",
                "danger"
            )

        return redirect(
            url_for(
                "support_conversation",
                conversation_id=conversation.id
            )
        )

    SupportMessage.query.filter(
        SupportMessage.conversation_id == conversation.id,
        SupportMessage.sender_id != user.id,
        SupportMessage.is_read.is_(False)
    ).update(
        {"is_read": True},
        synchronize_session=False
    )

    db.session.commit()

    return render_template(
        "support_conversation.html",
        conversation=conversation,
        messages=conversation.messages
    )


# =========================================================
# ADMIN SUPPORT
# =========================================================

@app.route("/admin/support")
@admin_required
def admin_support():
    status = request.args.get(
        "status", ""
    ).strip()

    query = SupportConversation.query

    if status in {"open", "closed"}:
        query = query.filter(
            SupportConversation.status == status
        )

    conversations = query.order_by(
        SupportConversation.created_at.desc()
    ).all()

    customers = {
        user.id: user
        for user in User.query.all()
    }

    return render_admin_page(
        "_admin_support.html",
        "admin_dashboard.html",
        conversations=conversations,
        customers=customers,
        selected_status=status,
        user=get_current_user(),
        current_year=datetime.now().year
    )


@app.route(
    "/admin/support/<int:conversation_id>",
    methods=["GET", "POST"]
)
@admin_required
def admin_support_conversation(conversation_id):
    conversation = db.get_or_404(
        SupportConversation,
        conversation_id
    )

    if request.method == "POST":
        message_text = request.form.get(
            "message", ""
        ).strip()

        if not message_text:
            if is_ajax_request():
                return ajax_json(
                    False,
                    "Message cannot be empty.",
                    400
                )

            flash(
                "Message cannot be empty.",
                "danger"
            )

            return redirect(
                url_for(
                    "admin_support_conversation",
                    conversation_id=conversation.id
                )
            )

        if len(message_text) > 10000:
            if is_ajax_request():
                return ajax_json(
                    False,
                    "Message is too long.",
                    400
                )

            flash(
                "Message is too long.",
                "danger"
            )

            return redirect(
                url_for(
                    "admin_support_conversation",
                    conversation_id=conversation.id
                )
            )

        admin_user = get_current_user()

        try:
            db.session.add(
                SupportMessage(
                    conversation_id=conversation.id,
                    sender_id=admin_user.id,
                    message=message_text,
                    is_read=False
                )
            )

            db.session.commit()

            return admin_form_response(
                True,
                "Reply sent successfully.",
                url_for(
                    "admin_support_conversation",
                    conversation_id=conversation.id
                )
            )

        except Exception:
            db.session.rollback()

            app.logger.exception(
                "Admin support reply failed."
            )

            return admin_form_response(
                False,
                "Unable to send reply.",
                status_code=500
            )

    SupportMessage.query.filter(
        SupportMessage.conversation_id == conversation.id,
        SupportMessage.sender_id != session["user_id"],
        SupportMessage.is_read.is_(False)
    ).update(
        {"is_read": True},
        synchronize_session=False
    )

    db.session.commit()

    return render_admin_support_conversation_page(
        conversation,
        conversation.customer,
        conversation.messages
    )


@app.route(
    "/admin/support/<int:conversation_id>/status",
    methods=["POST"]
)
@admin_required
def update_support_status(conversation_id):
    conversation = db.get_or_404(
        SupportConversation,
        conversation_id
    )

    new_status = request.form.get(
        "status", ""
    ).lower()

    if new_status not in {"open", "closed"}:
        return admin_form_response(
            False,
            "Invalid support status.",
            status_code=400
        )

    conversation.status = new_status

    try:
        db.session.commit()

        return admin_form_response(
            True,
            "Support conversation status updated.",
            url_for("admin_support")
        )

    except Exception:
        db.session.rollback()

        app.logger.exception(
            "Support status update failed."
        )

        return admin_form_response(
            False,
            "Unable to update support status.",
            status_code=500
        )


# =========================================================
# ADMIN CUSTOMERS
# =========================================================

@app.route("/admin/customers")
@admin_required
def admin_customers():
    customers = User.query.filter(
        User.role != "admin"
    ).order_by(
        User.created_at.desc()
    ).all()

    return render_admin_page(
        "_admin_customers.html",
        "admin_dashboard.html",
        customers=customers,
        user=get_current_user(),
        current_year=datetime.now().year
    )


# =========================================================
# ADMIN CATEGORIES
# =========================================================

@app.route("/admin/categories")
@admin_required
def admin_categories():
    categories = Category.query.order_by(
        Category.name.asc()
    ).all()

    return render_admin_page(
        "_admin_categories.html",
        "admin_dashboard.html",
        categories=categories,
        user=get_current_user(),
        current_year=datetime.now().year
    )


@app.route(
    "/admin/categories/add",
    methods=["GET", "POST"]
)
@admin_required
def add_category():
    if request.method == "POST":
        name = request.form.get(
            "name", ""
        ).strip()

        if not name:
            return admin_form_response(
                False,
                "Category name is required.",
                status_code=400
            )

        if len(name) > 100:
            return admin_form_response(
                False,
                "Category name is too long.",
                status_code=400
            )

        slug = re.sub(
            r"[^a-z0-9]+",
            "-",
            name.lower()
        ).strip("-")

        if not slug:
            return admin_form_response(
                False,
                "Category name must contain valid characters.",
                status_code=400
            )

        category = Category(
            name=name,
            slug=slug
        )

        try:
            db.session.add(category)
            db.session.commit()

            return admin_form_response(
                True,
                "Category added successfully.",
                url_for("admin_categories")
            )

        except IntegrityError:
            db.session.rollback()

            return admin_form_response(
                False,
                "Category name or slug already exists.",
                status_code=409
            )

        except Exception:
            db.session.rollback()

            app.logger.exception(
                "Category creation failed."
            )

            return admin_form_response(
                False,
                "Unable to add category.",
                status_code=500
            )

    if is_ajax_request():
        return render_template("add_category.html")

    return render_template(
        "admin_dashboard.html",
        initial_content_template="_admin_categories.html",
        categories=Category.query.order_by(
            Category.name.asc()
        ).all(),
        user=get_current_user(),
        current_year=datetime.now().year
    )


@app.route(
    "/admin/categories/delete/<int:category_id>",
    methods=["POST"]
)
@admin_required
def delete_category(category_id):
    category = db.get_or_404(
        Category,
        category_id
    )

    if category.products:
        return admin_form_response(
            False,
            "This category contains products. Move or remove those products first.",
            status_code=400
        )

    try:
        db.session.delete(category)
        db.session.commit()

        return admin_form_response(
            True,
            "Category deleted successfully.",
            url_for("admin_categories")
        )

    except Exception:
        db.session.rollback()

        app.logger.exception(
            "Category deletion failed."
        )

        return admin_form_response(
            False,
            "Unable to delete category.",
            status_code=500
        )


# =========================================================
# PAYMENT STATUS API
# =========================================================

@app.route("/api/payment-status/<tx_ref>")
@login_required
def payment_status_api(tx_ref):
    user = get_current_user()

    payment = get_payment_for_user(
        tx_ref,
        user
    )

    if not payment:
        return jsonify({
            "success": False,
            "message": "Payment not found."
        }), 404

    return jsonify({
        "success": True,
        "tx_ref": payment.tx_ref,
        "status": payment.status,
        "order_id": payment.order_id,
        "order_status": payment.order.status
    })


# =========================================================
# HEALTH CHECK
# =========================================================

@app.route("/health")
def health():
    return jsonify({
        "application": "ShopNova",
        "status": "running"
    })


# =========================================================
# FAVICON
# =========================================================

@app.route("/favicon.ico")
def favicon():
    return "", 204


# =========================================================
# ERROR HANDLERS
# =========================================================

def error_response(
    error_code,
    message,
    status_code
):
    if is_ajax_request():
        return (
            f"""
            <div class="alert alert-danger">
                <h5>Error {error_code}</h5>
                <p>{html.escape(message)}</p>
            </div>
            """,
            status_code
        )

    try:
        app.jinja_env.get_template("error.html")

        return render_template(
            "error.html",
            error_code=error_code,
            message=message
        ), status_code

    except Exception:
        page = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport"
                  content="width=device-width, initial-scale=1">
            <title>Error {error_code} | ShopNova</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    background: #f4f6f9;
                    margin: 0;
                    padding: 50px 20px;
                    text-align: center;
                }}
                .error-box {{
                    background: white;
                    max-width: 550px;
                    margin: auto;
                    padding: 35px;
                    border-radius: 12px;
                    box-shadow: 0 5px 25px rgba(0,0,0,.08);
                }}
                h1 {{ color: #17365d; }}
                a {{
                    display: inline-block;
                    margin-top: 20px;
                    background: #17365d;
                    color: white;
                    padding: 12px 22px;
                    text-decoration: none;
                    border-radius: 6px;
                }}
            </style>
        </head>
        <body>
            <div class="error-box">
                <h1>Error {error_code}</h1>
                <p>{html.escape(message)}</p>
                <a href="/">Return to ShopNova</a>
            </div>
        </body>
        </html>
        """

        return make_response(
            page,
            status_code
        )


@app.errorhandler(403)
def forbidden_error(error):
    return error_response(
        403,
        "You do not have permission to access this page.",
        403
    )


@app.errorhandler(404)
def not_found_error(error):
    return error_response(
        404,
        "The requested page could not be found.",
        404
    )


@app.errorhandler(413)
def file_too_large_error(error):
    return error_response(
        413,
        "The uploaded file is too large. Maximum file size is 5 MB.",
        413
    )


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()

    app.logger.exception(
        "Internal server error."
    )

    return error_response(
        500,
        "An unexpected server error occurred.",
        500
    )


# =========================================================
# CUSTOMER UNREAD COUNTS API
# =========================================================

@app.route("/api/unread-counts", methods=["GET"])
@login_required
def unread_counts():
    try:
        current_user = get_current_user()

        unread_notifications = Notification.query.filter_by(
            user_id=current_user.id,
            is_read=False
        ).count()

        return jsonify({
            "success": True,
            "unread_notifications": unread_notifications,
            "total": unread_notifications
        })

    except Exception:
        db.session.rollback()
        app.logger.exception("Error retrieving customer unread counts")

        return jsonify({
            "success": False,
            "message": "Unable to retrieve unread counts."
        }), 500


# =========================================================
# CUSTOMER NOTIFICATION: MARK ONE AS READ
# =========================================================

@app.route(
    "/api/notifications/<int:notification_id>/read",
    methods=["POST"]
)
@login_required
def mark_notification_read(notification_id):
    try:
        current_user = get_current_user()

        notification = Notification.query.filter_by(
            id=notification_id,
            user_id=current_user.id
        ).first()

        if not notification:
            return jsonify({
                "success": False,
                "message": "Notification not found."
            }), 404

        notification.is_read = True
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Notification marked as read."
        })

    except Exception:
        db.session.rollback()
        app.logger.exception("Error marking notification as read")

        return jsonify({
            "success": False,
            "message": "Unable to update notification."
        }), 500


# =========================================================
# CUSTOMER NOTIFICATION: MARK ALL AS READ
# =========================================================

@app.route("/api/notifications/read-all", methods=["POST"])
@login_required
def mark_all_notifications_read():
    try:
        current_user = get_current_user()

        Notification.query.filter_by(
            user_id=current_user.id,
            is_read=False
        ).update(
            {"is_read": True},
            synchronize_session=False
        )

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "All notifications marked as read."
        })

    except Exception:
        db.session.rollback()
        app.logger.exception("Error marking all notifications as read")

        return jsonify({
            "success": False,
            "message": "Unable to update notifications."
        }), 500


# =========================================================
# RUN APPLICATION
# =========================================================

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
        use_reloader=False
    )
