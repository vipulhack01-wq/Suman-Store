import os
import uuid
import secrets
import click
import hashlib
import hmac
import smtplib
import time

from types import SimpleNamespace
from datetime import datetime
from email.message import EmailMessage

from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

import firebase_admin
from firebase_admin import credentials, auth

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, jsonify, session, abort
)

from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user,
    logout_user, login_required, current_user
)

from sqlalchemy import text, inspect


# =========================================================
# CONFIGURATION
# =========================================================

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY", "change-this-before-deployment"
)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///suman_store.db"
)
# Render PostgreSQL URLs sometimes begin with postgres://
if app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgres://"):
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        app.config["SQLALCHEMY_DATABASE_URI"].replace(
            "postgres://", "postgresql://", 1
        )
    )

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = os.path.join(
    app.root_path, "static", "uploads"
)

UPLOAD_ROOT = app.config["UPLOAD_FOLDER"]
PRODUCT_FOLDER = os.path.join(UPLOAD_ROOT, "products")
VIDEO_FOLDER = os.path.join(UPLOAD_ROOT, "videos")
REVIEW_FOLDER = os.path.join(UPLOAD_ROOT, "reviews")
PAYMENT_FOLDER = os.path.join(UPLOAD_ROOT, "payment")

for folder in (PRODUCT_FOLDER, VIDEO_FOLDER, REVIEW_FOLDER, PAYMENT_FOLDER):
    os.makedirs(folder, exist_ok=True)

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
VIDEO_EXTENSIONS = {"mp4", "webm"}

MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

OTP_VALIDITY = 300
OTP_RESEND_WAIT = 60
OTP_MAX_ATTEMPTS = 5

STORE_UPI_ID = os.environ.get("STORE_UPI_ID", "")
STORE_SUPPORT_PHONE = os.environ.get("STORE_SUPPORT_PHONE", "+91 7905755831")
STORE_SUPPORT_EMAIL = os.environ.get("STORE_SUPPORT_EMAIL", "vipulhack01@gmail.com")

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


# =========================================================
# FIREBASE INITIALIZATION
# =========================================================

firebase_key = os.path.join(app.root_path, "serviceAccountKey.json")
FIREBASE_ENABLED = False

try:
    if os.path.exists(firebase_key):
        if not firebase_admin._apps:
            firebase_admin.initialize_app(
                credentials.Certificate(firebase_key)
            )
        FIREBASE_ENABLED = True
    else:
        app.logger.warning(
            "serviceAccountKey.json not found. Firebase endpoints are disabled."
        )
except Exception:
    app.logger.exception("Firebase initialization failed.")
    FIREBASE_ENABLED = False


# =========================================================
# DATABASE MODELS
# =========================================================

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="customer", nullable=False)
    firebase_uid = db.Column(db.String(128), unique=True)
    phone = db.Column(db.String(30))


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    specifications = db.Column(db.Text)
    price = db.Column(db.Float, nullable=False)
    image = db.Column(db.String(255))
    video = db.Column(db.String(255))
    stock = db.Column(db.Integer, default=0)
    seller_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    approved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    images = db.relationship(
        "ProductImage", backref="product", cascade="all, delete-orphan"
    )


class ProductImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id"), nullable=False
    )
    filename = db.Column(db.String(255), nullable=False)


class Cart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id"), nullable=False
    )
    quantity = db.Column(db.Integer, default=1)
    product = db.relationship("Product")


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(40), unique=True, nullable=False)
    customer_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )
    full_name = db.Column(db.String(100), nullable=False)
    mobile = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(150), nullable=False)
    house = db.Column(db.String(200), nullable=False)
    locality = db.Column(db.String(200), nullable=False)
    city = db.Column(db.String(100), nullable=False)
    district = db.Column(db.String(100), nullable=False)
    state = db.Column(db.String(100), nullable=False)
    pincode = db.Column(db.String(10), nullable=False)
    landmark = db.Column(db.String(200))
    instructions = db.Column(db.Text)
    total_amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(30), default="UPI")
    payment_status = db.Column(db.String(40), default="Pending")
    utr_number = db.Column(db.String(100), unique=True)
    order_status = db.Column(db.String(40), default="Pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    customer = db.relationship("User")
    items = db.relationship(
        "OrderItem", backref="order", cascade="all, delete-orphan"
    )


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer, db.ForeignKey("order.id"), nullable=False
    )
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"))
    seller_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    product_name = db.Column(db.String(150), nullable=False)
    price = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    product = db.relationship("Product")
    seller = db.relationship("User")


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id"), nullable=False
    )
    customer_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )
    order_id = db.Column(
        db.Integer, db.ForeignKey("order.id"), nullable=False
    )
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    approved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    customer = db.relationship("User")


class ReviewMedia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(
        db.Integer, db.ForeignKey("review.id"), nullable=False
    )
    filename = db.Column(db.String(255), nullable=False)
    media_type = db.Column(db.String(20))
    review = db.relationship(
        "Review",
        backref=db.backref("media", cascade="all, delete-orphan")
    )


class StoreSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    upi_id = db.Column(db.String(150))
    qr_filename = db.Column(db.String(255))
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class SavedAddress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    full_name = db.Column(db.String(100), nullable=False)
    mobile = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(150), nullable=False)
    house = db.Column(db.String(200), nullable=False)
    locality = db.Column(db.String(200), nullable=False)
    city = db.Column(db.String(100), nullable=False)
    district = db.Column(db.String(100), nullable=False)
    state = db.Column(db.String(100), nullable=False)
    pincode = db.Column(db.String(10), nullable=False)
    landmark = db.Column(db.String(200))
    address_type = db.Column(db.String(30), default="Home", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship("User")


class Wishlist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id"), nullable=False
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    product = db.relationship("Product")
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "product_id",
            name="unique_user_wishlist_product"
        ),
    )


# =========================================================
# DATABASE INITIALIZATION / LIGHT MIGRATIONS
# =========================================================

@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


with app.app_context():
    db.create_all()

    inspector = inspect(db.engine)

    # Add columns that may be missing in an older SQLite database.
    if "user" in inspector.get_table_names():
        user_columns = {c["name"] for c in inspector.get_columns("user")}
        if "firebase_uid" not in user_columns:
            db.session.execute(
                text("ALTER TABLE user ADD COLUMN firebase_uid VARCHAR(128)")
            )
        if "phone" not in user_columns:
            db.session.execute(
                text("ALTER TABLE user ADD COLUMN phone VARCHAR(30)")
            )

    if "product" in inspector.get_table_names():
        product_columns = {
            c["name"] for c in inspect(db.engine).get_columns("product")
        }
        if "specifications" not in product_columns:
            db.session.execute(
                text("ALTER TABLE product ADD COLUMN specifications TEXT")
            )
        if "video" not in product_columns:
            db.session.execute(
                text("ALTER TABLE product ADD COLUMN video VARCHAR(255)")
            )

    db.session.commit()


# =========================================================
# COMMON HELPERS
# =========================================================

def valid_email(email):
    return (
        bool(email)
        and len(email) <= 150
        and "@" in email
        and "." in email.rsplit("@", 1)[-1]
        and not any(char.isspace() for char in email)
    )


def allowed_file(filename, extensions):
    return (
        bool(filename)
        and "." in filename
        and filename.rsplit(".", 1)[1].lower() in extensions
    )


def save_upload(file, folder, extensions):
    if not file or not file.filename:
        return None

    if not allowed_file(file.filename, extensions):
        raise ValueError("Invalid file format.")

    safe_name = secure_filename(file.filename)
    if "." not in safe_name:
        raise ValueError("Invalid file name.")

    extension = safe_name.rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{extension}"
    file.save(os.path.join(folder, filename))
    return filename


def get_store_settings():
    settings = db.session.get(StoreSettings, 1)
    if not settings:
        settings = StoreSettings(id=1, upi_id=STORE_UPI_ID)
        db.session.add(settings)
        db.session.commit()
    return settings


def get_product_rating(product_id):
    reviews = Review.query.filter_by(
        product_id=product_id, approved=True
    ).all()
    if not reviews:
        return 0
    return round(sum(review.rating for review in reviews) / len(reviews), 1)


def safe_return_url(default_endpoint="home"):
    # Only allow a local path as the redirect destination.
    referrer = request.referrer
    if referrer:
        from urllib.parse import urlparse
        parsed = urlparse(referrer)
        if not parsed.netloc or parsed.netloc == request.host:
            return referrer
    return url_for(default_endpoint)


# =========================================================
# EMAIL OTP HELPERS
# =========================================================

def send_email_otp(email, otp, purpose):
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        raise RuntimeError("Gmail SMTP settings missing.")

    message = EmailMessage()
    message["Subject"] = f"Suman Store - {purpose.title()} OTP"
    message["From"] = MAIL_USERNAME
    message["To"] = email
    message.set_content(
        f"""Hello,

Your Suman Store OTP is: {otp}

This OTP is valid for 5 minutes.
Do not share this OTP with anyone.

Thank you,
Suman Store
"""
    )

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20) as server:
        server.starttls()
        server.login(MAIL_USERNAME, MAIL_PASSWORD)
        server.send_message(message)


def hash_otp(otp):
    return hashlib.sha256(otp.encode("utf-8")).hexdigest()


def create_and_send_otp(email, purpose):
    session_key = f"{purpose}_otp"
    pending = session.get(session_key)
    now = time.time()

    if pending:
        elapsed = now - pending.get("sent_at", 0)
        if elapsed < OTP_RESEND_WAIT:
            remaining = int(OTP_RESEND_WAIT - elapsed)
            return False, (
                f"Please wait {remaining} seconds before requesting another OTP."
            )

    otp = str(secrets.randbelow(900000) + 100000)

    try:
        send_email_otp(email, otp, purpose)
    except Exception:
        app.logger.exception("OTP email failed")
        return False, "OTP email send nahi hua. SMTP settings check karein."

    session[session_key] = {
        "email": email,
        "otp_hash": hash_otp(otp),
        "expires_at": now + OTP_VALIDITY,
        "sent_at": now,
        "attempts": 0
    }
    session.modified = True
    return True, "OTP sent successfully!"


def verify_email_otp(email, otp, purpose):
    session_key = f"{purpose}_otp"
    pending = session.get(session_key)

    if not pending:
        return False, "Please request OTP first."
    if pending.get("email") != email:
        return False, "Email does not match."
    if time.time() > pending.get("expires_at", 0):
        session.pop(session_key, None)
        return False, "OTP expired."
    if pending.get("attempts", 0) >= OTP_MAX_ATTEMPTS:
        session.pop(session_key, None)
        return False, "Too many incorrect attempts."
    if not otp.isdigit() or len(otp) != 6:
        pending["attempts"] += 1
        session.modified = True
        return False, "Enter a valid 6 digit OTP."
    if not hmac.compare_digest(pending["otp_hash"], hash_otp(otp)):
        pending["attempts"] += 1
        session.modified = True
        return False, "Incorrect OTP."

    session.pop(session_key, None)
    session.modified = True
    return True, "OTP verified successfully!"


# =========================================================
# FIREBASE HELPERS
# =========================================================

def get_firebase_user(data):
    if not FIREBASE_ENABLED:
        raise RuntimeError("Firebase is not configured on the server.")

    id_token = data.get("idToken")
    if not id_token:
        raise ValueError("Firebase ID token missing.")

    decoded = auth.verify_id_token(id_token)
    email = decoded.get("email")
    return {
        "uid": decoded["uid"],
        "email": email.lower() if email else None,
        "phone": decoded.get("phone_number"),
        "name": decoded.get("name"),
        "email_verified": decoded.get("email_verified", False)
    }


def save_firebase_user(firebase_data, requested_name=None, requested_role=None):
    uid = firebase_data["uid"]
    email = firebase_data["email"]
    phone = firebase_data["phone"]

    user = User.query.filter_by(firebase_uid=uid).first()
    if user:
        if phone:
            user.phone = phone
        if firebase_data["name"]:
            user.name = firebase_data["name"]
        db.session.commit()
        return user

    if email:
        existing = User.query.filter_by(email=email).first()
        if existing:
            if not firebase_data["email_verified"]:
                raise ValueError("Email verification required.")
            existing.firebase_uid = uid
            if phone:
                existing.phone = phone
            db.session.commit()
            return existing

    name = requested_name or firebase_data["name"] or phone or "Suman Store User"
    role = requested_role or "customer"
    if role not in ("customer", "seller"):
        raise ValueError("Invalid role.")

    if not email:
        email = f"firebase_{uid}@sumanstore.local"

    user = User(
        name=name,
        email=email,
        password=generate_password_hash(secrets.token_urlsafe(32)),
        role=role,
        firebase_uid=uid,
        phone=phone
    )
    db.session.add(user)
    db.session.commit()
    return user


# =========================================================
# HOME / PRODUCT DETAILS
# =========================================================

@app.route("/")
def home():
    search_query = request.args.get("q", "").strip()
    query = Product.query.filter_by(approved=True)

    if search_query:
        query = query.filter(Product.name.ilike(f"%{search_query}%"))

    products = query.order_by(Product.created_at.desc()).all()
    return render_template(
        "index.html",
        products=products,
        get_product_rating=get_product_rating,
        support_phone=STORE_SUPPORT_PHONE,
        support_email=STORE_SUPPORT_EMAIL,
        search_query=search_query
    )


@app.route("/product/<int:product_id>")
def product_detail(product_id):
    product = db.session.get(Product, product_id)
    if not product or not product.approved:
        abort(404)

    reviews = Review.query.filter_by(
        product_id=product_id, approved=True
    ).order_by(Review.created_at.desc()).all()

    return render_template(
        "product_detail.html",
        product=product,
        reviews=reviews,
        average_rating=get_product_rating(product_id)
    )


# =========================================================
# REGISTRATION / EMAIL OTP
# =========================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        flash("Please use the Email OTP registration form.")
        return redirect(url_for("register"))
    return render_template("register.html")


@app.route("/send-registration-otp", methods=["POST"])
def send_registration_otp():
    data = request.get_json(silent=True) or request.form
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    role = data.get("role", "customer")

    if not name or not email or not password:
        return jsonify(success=False, message="Please fill all fields."), 400
    if len(name) > 100 or not valid_email(email):
        return jsonify(success=False, message="Invalid name or email."), 400
    if role not in ("customer", "seller"):
        return jsonify(success=False, message="Invalid account type."), 400
    if len(password) < 8:
        return jsonify(success=False, message="Password must be at least 8 characters."), 400
    if User.query.filter_by(email=email).first():
        return jsonify(success=False, message="Email already registered."), 409

    session["pending_registration"] = {
        "name": name,
        "email": email,
        "password_hash": generate_password_hash(password),
        "role": role
    }

    success, message = create_and_send_otp(email, "registration")
    if not success:
        return jsonify(success=False, message=message), 429
    return jsonify(success=True, message=message)


@app.route("/resend-registration-otp", methods=["POST"])
def resend_registration_otp():
    pending = session.get("pending_registration")
    if not pending:
        return jsonify(success=False, message="Please start registration again."), 400

    success, message = create_and_send_otp(pending["email"], "registration")
    if not success:
        return jsonify(success=False, message=message), 429
    return jsonify(success=True, message=message)


@app.route("/verify-registration-otp", methods=["POST"])
def verify_registration_otp():
    data = request.get_json(silent=True) or request.form
    otp = data.get("otp", "").strip()
    pending = session.get("pending_registration")

    if not pending:
        return jsonify(success=False, message="Please start registration again."), 400

    email = pending["email"]
    success, message = verify_email_otp(email, otp, "registration")
    if not success:
        return jsonify(success=False, message=message), 400

    if User.query.filter_by(email=email).first():
        session.pop("pending_registration", None)
        return jsonify(success=False, message="Email already registered."), 409

    try:
        user = User(
            name=pending["name"],
            email=email,
            password=pending["password_hash"],
            role=pending["role"]
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        session.pop("pending_registration", None)
        return jsonify(
            success=True,
            message="Registration successful!",
            redirect=url_for("dashboard")
        )
    except Exception:
        db.session.rollback()
        app.logger.exception("Registration failed")
        return jsonify(success=False, message="Registration failed."), 500


# =========================================================
# LOGIN / EMAIL OTP
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.")

    return render_template("login.html")


@app.route("/send-login-otp", methods=["POST"])
def send_login_otp():
    data = request.get_json(silent=True) or request.form
    email = data.get("email", "").strip().lower()

    if not valid_email(email):
        return jsonify(success=False, message="Enter a valid email address."), 400
    if not User.query.filter_by(email=email).first():
        return jsonify(success=False, message="No account found."), 404

    success, message = create_and_send_otp(email, "login")
    if not success:
        return jsonify(success=False, message=message), 429
    return jsonify(success=True, message=message)


@app.route("/resend-login-otp", methods=["POST"])
def resend_login_otp():
    pending = session.get("login_otp")
    if not pending:
        return jsonify(success=False, message="Please request login OTP first."), 400

    email = pending["email"]
    if not User.query.filter_by(email=email).first():
        session.pop("login_otp", None)
        return jsonify(success=False, message="Account not found."), 404

    success, message = create_and_send_otp(email, "login")
    if not success:
        return jsonify(success=False, message=message), 429
    return jsonify(success=True, message=message)


@app.route("/verify-login-otp", methods=["POST"])
def verify_login_otp():
    data = request.get_json(silent=True) or request.form
    email = data.get("email", "").strip().lower()
    otp = data.get("otp", "").strip()

    success, message = verify_email_otp(email, otp, "login")
    if not success:
        return jsonify(success=False, message=message), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify(success=False, message="Account not found."), 404

    login_user(user)
    return jsonify(
        success=True,
        message="Login successful!",
        redirect=url_for("dashboard")
    )


# =========================================================
# FIREBASE LOGIN / REGISTER
# =========================================================

@app.route("/firebase-login", methods=["POST"])
def firebase_login():
    try:
        data = request.get_json(silent=True) or {}
        firebase_data = get_firebase_user(data)
        user = save_firebase_user(firebase_data)
        login_user(user)
        return jsonify(
            success=True,
            message="Login successful",
            redirect=url_for("dashboard")
        )
    except Exception:
        db.session.rollback()
        app.logger.exception("Firebase login failed")
        return jsonify(success=False, message="Firebase login failed."), 401


@app.route("/firebase-register", methods=["POST"])
def firebase_register():
    try:
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        role = data.get("role")

        if not name or len(name) > 100 or role not in ("customer", "seller"):
            return jsonify(success=False, message="Invalid registration details."), 400

        firebase_data = get_firebase_user(data)
        if not firebase_data["phone"] and not firebase_data["email_verified"]:
            return jsonify(success=False, message="Verified phone or email required."), 403

        user = save_firebase_user(
            firebase_data, requested_name=name, requested_role=role
        )
        login_user(user)
        return jsonify(
            success=True,
            message="Registration successful",
            redirect=url_for("dashboard")
        )
    except Exception:
        db.session.rollback()
        app.logger.exception("Firebase registration failed")
        return jsonify(success=False, message="Registration failed."), 400


# =========================================================
# DASHBOARDS
# =========================================================

@app.route("/dashboard")
@login_required
def dashboard():
    if current_user.role == "admin":
        return redirect(url_for("admin_dashboard"))

    if current_user.role == "seller":
        products = Product.query.filter_by(seller_id=current_user.id).all()
        orders = (
            Order.query.join(OrderItem)
            .filter(OrderItem.seller_id == current_user.id)
            .order_by(Order.created_at.desc()).all()
        )
        return render_template(
            "seller_dashboard.html", products=products, orders=orders
        )

    orders = Order.query.filter_by(
        customer_id=current_user.id
    ).order_by(Order.created_at.desc()).all()

    return render_template("customer_dashboard.html", orders=orders)


# =========================================================
# SELLER PRODUCT MANAGEMENT
# =========================================================

@app.route("/add-product", methods=["GET", "POST"])
@login_required
def add_product():
    if current_user.role != "seller":
        abort(403)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        specifications = request.form.get("specifications", "").strip()

        try:
            price = float(request.form.get("price", 0))
            stock = int(request.form.get("stock", 0))
        except (ValueError, TypeError):
            flash("Invalid price or stock.")
            return redirect(url_for("add_product"))

        if not name or len(name) > 150 or not description or price <= 0 or stock < 0:
            flash("Invalid product details.")
            return redirect(url_for("add_product"))

        uploaded_images = request.files.getlist("images")
        video_file = request.files.get("video")

        if len([f for f in uploaded_images if f and f.filename]) > 6:
            flash("Maximum 6 product photos allowed.")
            return redirect(url_for("add_product"))

        saved_images = []
        saved_video = None

        try:
            for image_file in uploaded_images:
                if image_file and image_file.filename:
                    saved_images.append(
                        save_upload(image_file, PRODUCT_FOLDER, IMAGE_EXTENSIONS)
                    )

            if video_file and video_file.filename:
                saved_video = save_upload(
                    video_file, VIDEO_FOLDER, VIDEO_EXTENSIONS
                )

            product = Product(
                name=name,
                description=description,
                specifications=specifications,
                price=price,
                stock=stock,
                seller_id=current_user.id,
                image=saved_images[0] if saved_images else None,
                video=saved_video,
                approved=False
            )
            db.session.add(product)
            db.session.flush()

            for filename in saved_images:
                db.session.add(
                    ProductImage(product_id=product.id, filename=filename)
                )

            db.session.commit()
            flash("Product submitted for admin approval!")
            return redirect(url_for("dashboard"))

        except ValueError as error:
            db.session.rollback()
            flash(str(error))
        except Exception:
            db.session.rollback()
            app.logger.exception("Product upload failed")
            flash("Product upload failed.")

        return redirect(url_for("add_product"))

    return render_template("add_product.html")


# =========================================================
# ADMIN DASHBOARD / PAYMENT SETTINGS
# =========================================================

@app.route("/admin")
@login_required
def admin_dashboard():
    if current_user.role != "admin":
        abort(403)

    products = Product.query.filter_by(approved=False).all()
    orders = Order.query.order_by(Order.created_at.desc()).all()
    pending_reviews = Review.query.filter_by(approved=False).all()
    store_settings = get_store_settings()

    return render_template(
        "admin_dashboard.html",
        products=products,
        orders=orders,
        pending_reviews=pending_reviews,
        store_settings=store_settings,
        qr_image_url=(
            url_for("static", filename="uploads/payment/" + store_settings.qr_filename)
            if store_settings.qr_filename else None
        )
    )


@app.route("/admin/payment-settings", methods=["POST"])
@login_required
def admin_payment_settings():
    if current_user.role != "admin":
        abort(403)

    settings = get_store_settings()
    upi_id = request.form.get("upi_id", "").strip()
    qr_file = request.files.get("qr_code")

    if not upi_id or len(upi_id) > 150:
        flash("Please enter a valid UPI ID.")
        return redirect(url_for("admin_dashboard"))

    old_qr = settings.qr_filename
    new_qr = None

    try:
        if qr_file and qr_file.filename:
            new_qr = save_upload(qr_file, PAYMENT_FOLDER, IMAGE_EXTENSIONS)

        settings.upi_id = upi_id
        if new_qr:
            settings.qr_filename = new_qr
        db.session.commit()

        if new_qr and old_qr and old_qr != new_qr:
            old_path = os.path.join(PAYMENT_FOLDER, old_qr)
            if os.path.exists(old_path):
                os.remove(old_path)

        flash("Payment settings saved successfully!")
    except ValueError as error:
        db.session.rollback()
        flash(str(error))
    except Exception:
        db.session.rollback()
        if new_qr:
            new_path = os.path.join(PAYMENT_FOLDER, new_qr)
            if os.path.exists(new_path):
                os.remove(new_path)
        app.logger.exception("Payment settings update failed")
        flash("Payment settings could not be saved.")

    return redirect(url_for("admin_dashboard"))


# =========================================================
# ADMIN PRODUCT APPROVAL
# =========================================================

@app.route("/approve-product/<int:product_id>", methods=["POST"])
@login_required
def approve_product(product_id):
    if current_user.role != "admin":
        abort(403)
    product = db.session.get(Product, product_id)
    if not product:
        abort(404)
    product.approved = True
    db.session.commit()
    flash("Product approved!")
    return redirect(url_for("admin_dashboard"))


@app.route("/reject-product/<int:product_id>", methods=["POST"])
@login_required
def reject_product(product_id):
    if current_user.role != "admin":
        abort(403)
    product = db.session.get(Product, product_id)
    if not product:
        abort(404)
    db.session.delete(product)
    db.session.commit()
    flash("Product rejected!")
    return redirect(url_for("admin_dashboard"))


# =========================================================
# CART
# =========================================================

@app.route("/add-to-cart/<int:product_id>", methods=["POST"])
@login_required
def add_to_cart(product_id):
    if current_user.role != "customer":
        abort(403)

    product = db.session.get(Product, product_id)
    if not product or not product.approved:
        abort(404)
    if product.stock < 1:
        flash("Product is out of stock.")
        return redirect(url_for("product_detail", product_id=product_id))

    item = Cart.query.filter_by(
        user_id=current_user.id, product_id=product_id
    ).first()

    if item:
        if item.quantity < product.stock:
            item.quantity += 1
        else:
            flash("Maximum available stock reached.")
    else:
        db.session.add(
            Cart(user_id=current_user.id, product_id=product_id, quantity=1)
        )

    db.session.commit()
    flash("Product added to cart!")
    return redirect(url_for("cart"))


@app.route("/update-cart/<int:item_id>", methods=["POST"])
@login_required
def update_cart(item_id):
    if current_user.role != "customer":
        abort(403)

    item = db.session.get(Cart, item_id)
    if not item or item.user_id != current_user.id:
        abort(404)

    try:
        quantity = int(request.form.get("quantity", 1))
    except (ValueError, TypeError):
        quantity = 1

    if quantity < 1:
        db.session.delete(item)
    elif quantity > item.product.stock:
        flash("Requested quantity is not available.")
    else:
        item.quantity = quantity

    db.session.commit()
    return redirect(url_for("cart"))


@app.route("/cart")
@login_required
def cart():
    if current_user.role != "customer":
        abort(403)

    items = Cart.query.filter_by(user_id=current_user.id).all()
    total = sum(item.product.price * item.quantity for item in items)
    return render_template("cart.html", cart_items=items, total=total)


@app.route("/remove-from-cart/<int:item_id>", methods=["POST"])
@login_required
def remove_from_cart(item_id):
    if current_user.role != "customer":
        abort(403)

    item = db.session.get(Cart, item_id)
    if not item or item.user_id != current_user.id:
        abort(404)

    db.session.delete(item)
    db.session.commit()
    flash("Product removed from cart.")
    return redirect(url_for("cart"))


# =========================================================
# WISHLIST
# =========================================================

@app.route("/wishlist")
@login_required
def wishlist():
    if current_user.role != "customer":
        abort(403)

    items = Wishlist.query.filter_by(
        user_id=current_user.id
    ).order_by(Wishlist.created_at.desc()).all()

    return render_template("wishlist.html", wishlist_items=items)


@app.route("/wishlist/add/<int:product_id>", methods=["POST"])
@login_required
def add_to_wishlist(product_id):
    if current_user.role != "customer":
        abort(403)

    product = db.session.get(Product, product_id)
    if not product or not product.approved:
        abort(404)

    existing = Wishlist.query.filter_by(
        user_id=current_user.id, product_id=product_id
    ).first()

    if existing:
        flash("Product is already in your wishlist.")
    else:
        db.session.add(
            Wishlist(user_id=current_user.id, product_id=product_id)
        )
        db.session.commit()
        flash("Product added to wishlist!")

    return redirect(safe_return_url())


@app.route("/wishlist/remove/<int:wishlist_id>", methods=["POST"])
@login_required
def remove_from_wishlist(wishlist_id):
    if current_user.role != "customer":
        abort(403)

    item = db.session.get(Wishlist, wishlist_id)
    if not item or item.user_id != current_user.id:
        abort(404)

    db.session.delete(item)
    db.session.commit()
    flash("Product removed from wishlist.")
    return redirect(url_for("wishlist"))


@app.route("/wishlist/move-to-cart/<int:wishlist_id>", methods=["POST"])
@login_required
def wishlist_move_to_cart(wishlist_id):
    if current_user.role != "customer":
        abort(403)

    item = db.session.get(Wishlist, wishlist_id)
    if not item or item.user_id != current_user.id:
        abort(404)

    product = item.product
    if not product or not product.approved:
        abort(404)
    if product.stock < 1:
        flash("Product is out of stock.")
        return redirect(url_for("wishlist"))

    cart_item = Cart.query.filter_by(
        user_id=current_user.id, product_id=product.id
    ).first()

    if cart_item:
        if cart_item.quantity >= product.stock:
            flash("Maximum available stock reached.")
            return redirect(url_for("wishlist"))
        cart_item.quantity += 1
    else:
        db.session.add(
            Cart(user_id=current_user.id, product_id=product.id, quantity=1)
        )

    db.session.delete(item)
    db.session.commit()
    flash("Product moved to cart!")
    return redirect(url_for("cart"))


@app.route("/save-for-later/<int:item_id>", methods=["POST"])
@login_required
def save_for_later(item_id):
    if current_user.role != "customer":
        abort(403)

    item = db.session.get(Cart, item_id)
    if not item or item.user_id != current_user.id:
        abort(404)

    existing = Wishlist.query.filter_by(
        user_id=current_user.id, product_id=item.product_id
    ).first()

    if not existing:
        db.session.add(
            Wishlist(user_id=current_user.id, product_id=item.product_id)
        )

    db.session.delete(item)
    db.session.commit()
    flash("Product saved for later!")
    return redirect(url_for("cart"))


# =========================================================
# BUY NOW
# =========================================================

@app.route("/buy-now/<int:product_id>", methods=["POST"])
@login_required
def buy_now(product_id):
    if current_user.role != "customer":
        abort(403)

    product = db.session.get(Product, product_id)
    if not product or not product.approved:
        abort(404)
    if product.stock < 1:
        flash("Product is out of stock.")
        return redirect(url_for("product_detail", product_id=product_id))

    # Buy Now checkout does not alter the customer's existing cart.
    session["buy_now_product_id"] = product.id
    return redirect(url_for("checkout"))


# =========================================================
# SINGLE CHECKOUT ROUTE: CART + BUY NOW
# =========================================================

@app.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    if current_user.role != "customer":
        abort(403)

    buy_now_id = session.get("buy_now_product_id")
    is_buy_now = False

    if buy_now_id:
        product = db.session.get(Product, buy_now_id)
        if product and product.approved and product.stock > 0:
            items = [SimpleNamespace(product=product, quantity=1, id=None)]
            is_buy_now = True
        else:
            session.pop("buy_now_product_id", None)
            flash("This product is no longer available.")
            return redirect(url_for("cart"))
    else:
        items = Cart.query.filter_by(user_id=current_user.id).all()

    if not items:
        flash("Your cart is empty.")
        return redirect(url_for("cart"))

    total = sum(item.product.price * item.quantity for item in items)
    store_settings = get_store_settings()
    saved_addresses = SavedAddress.query.filter_by(
        user_id=current_user.id
    ).order_by(SavedAddress.created_at.desc()).all()

    if request.method == "POST":
        fields = [
            "full_name", "mobile", "email", "house", "locality",
            "city", "district", "state", "pincode"
        ]
        selected_address_id = request.form.get("saved_address_id", "").strip()
        selected_address = None

        if selected_address_id:
            try:
                selected_address = SavedAddress.query.filter_by(
                    id=int(selected_address_id), user_id=current_user.id
                ).first()
            except (TypeError, ValueError):
                selected_address = None
            if not selected_address:
                flash("Selected saved address was not found.")
                return redirect(url_for("checkout"))
            data = {field: getattr(selected_address, field) for field in fields}
            landmark = selected_address.landmark or ""
        else:
            data = {
                field: request.form.get(field, "").strip()
                for field in fields
            }
            landmark = request.form.get("landmark", "").strip()

        for field, value in data.items():
            if not value:
                flash(f"Please fill {field.replace('_', ' ')}.")
                return redirect(url_for("checkout"))

        if not valid_email(data["email"]):
            flash("Invalid email address.")
            return redirect(url_for("checkout"))
        if not data["mobile"].isdigit() or len(data["mobile"]) != 10:
            flash("Enter a valid 10 digit mobile number.")
            return redirect(url_for("checkout"))
        if not data["pincode"].isdigit() or len(data["pincode"]) != 6:
            flash("Enter a valid 6 digit PIN code.")
            return redirect(url_for("checkout"))

        for item in items:
            if (
                not item.product or not item.product.approved
                or item.quantity < 1 or item.quantity > item.product.stock
            ):
                product_name = item.product.name if item.product else "product"
                flash(f"Insufficient stock or unavailable product: {product_name}.")
                return redirect(url_for("cart"))

        payment_method = request.form.get("payment_method", "UPI")
        if payment_method not in ("UPI", "COD"):
            flash("Invalid payment method.")
            return redirect(url_for("checkout"))
        if payment_method == "UPI" and not store_settings.upi_id:
            flash("UPI payment is currently unavailable.")
            return redirect(url_for("checkout"))

        order = Order(
            order_number="SS-" + uuid.uuid4().hex[:12].upper(),
            customer_id=current_user.id,
            full_name=data["full_name"], mobile=data["mobile"],
            email=data["email"], house=data["house"],
            locality=data["locality"], city=data["city"],
            district=data["district"], state=data["state"],
            pincode=data["pincode"], landmark=landmark,
            instructions=request.form.get("instructions", "").strip(),
            total_amount=total, payment_method=payment_method,
            payment_status="Pending" if payment_method == "UPI" else "COD",
            order_status="Pending"
        )

        try:
            db.session.add(order)
            db.session.flush()

            # Optionally save a newly entered address. Existing saved addresses
            # remain unchanged; each customer can keep multiple addresses.
            if not selected_address and request.form.get("save_address") == "1":
                address_type = request.form.get("address_type", "Home").strip()
                if address_type not in ("Home", "Work", "Other"):
                    address_type = "Home"
                duplicate = SavedAddress.query.filter_by(
                    user_id=current_user.id,
                    house=data["house"], locality=data["locality"],
                    city=data["city"], district=data["district"],
                    state=data["state"], pincode=data["pincode"]
                ).first()
                if not duplicate:
                    db.session.add(SavedAddress(
                        user_id=current_user.id,
                        **data,
                        landmark=landmark,
                        address_type=address_type
                    ))

            for item in items:
                product = item.product
                product.stock -= item.quantity
                db.session.add(OrderItem(
                    order_id=order.id, product_id=product.id,
                    seller_id=product.seller_id, product_name=product.name,
                    price=product.price, quantity=item.quantity
                ))
                if not is_buy_now:
                    db.session.delete(item)

            db.session.commit()
            if is_buy_now:
                session.pop("buy_now_product_id", None)
            return redirect(url_for("order_detail", order_id=order.id))

        except Exception:
            db.session.rollback()
            app.logger.exception("Order creation failed")
            flash("Order could not be placed. Please try again.")
            return redirect(url_for("cart"))

    qr_image_url = (
        url_for("static", filename="uploads/payment/" + store_settings.qr_filename)
        if store_settings.qr_filename else None
    )
    return render_template(
        "checkout.html", cart_items=items, total=total,
        upi_id=store_settings.upi_id, store_settings=store_settings,
        is_buy_now=is_buy_now, saved_addresses=saved_addresses,
        qr_image_url=qr_image_url
    )


# =========================================================
# SAVED ADDRESSES
# =========================================================

@app.route("/addresses", methods=["GET", "POST"])
@login_required
def addresses():
    if current_user.role != "customer":
        abort(403)

    if request.method == "POST":
        fields = ["full_name", "mobile", "email", "house", "locality",
                  "city", "district", "state", "pincode"]
        data = {field: request.form.get(field, "").strip() for field in fields}
        if any(not value for value in data.values()):
            flash("Please fill all required address fields.")
            return redirect(url_for("addresses"))
        if not valid_email(data["email"]) or not data["mobile"].isdigit() or len(data["mobile"]) != 10 or not data["pincode"].isdigit() or len(data["pincode"]) != 6:
            flash("Please check email, 10-digit mobile and 6-digit PIN code.")
            return redirect(url_for("addresses"))
        address_type = request.form.get("address_type", "Home").strip()
        if address_type not in ("Home", "Work", "Other"):
            address_type = "Home"
        address = SavedAddress(
            user_id=current_user.id, **data,
            landmark=request.form.get("landmark", "").strip(),
            address_type=address_type
        )
        db.session.add(address)
        db.session.commit()
        flash("Address saved successfully!")
        return redirect(url_for("addresses"))

    saved_addresses = SavedAddress.query.filter_by(
        user_id=current_user.id
    ).order_by(SavedAddress.created_at.desc()).all()
    return render_template("addresses.html", addresses=saved_addresses)


@app.route("/addresses/delete/<int:address_id>", methods=["POST"])
@login_required
def delete_address(address_id):
    if current_user.role != "customer":
        abort(403)
    address = SavedAddress.query.filter_by(
        id=address_id, user_id=current_user.id
    ).first_or_404()
    db.session.delete(address)
    db.session.commit()
    flash("Saved address deleted.")
    return redirect(url_for("addresses"))


# =========================================================
# HELP & SUPPORT
# =========================================================

@app.route("/help-support")
def help_support():
    phone = STORE_SUPPORT_PHONE.strip()
    email = STORE_SUPPORT_EMAIL.strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    whatsapp_url = "https://wa.me/" + digits if digits else None
    return render_template(
        "help_support.html",
        support_phone=phone,
        support_email=email,
        whatsapp_url=whatsapp_url
    )


# =========================================================
# CUSTOMER ORDERS / ORDER DETAILS
# =========================================================

@app.route("/my-orders")
@login_required
def my_orders():
    if current_user.role != "customer":
        abort(403)

    orders = Order.query.filter_by(
        customer_id=current_user.id
    ).order_by(Order.created_at.desc()).all()

    return render_template("my_orders.html", orders=orders)


@app.route("/order/<int:order_id>")
@login_required
def order_detail(order_id):
    order = db.session.get(Order, order_id)
    if not order:
        abort(404)

    if current_user.role != "admin" and order.customer_id != current_user.id:
        abort(403)

    store_settings = get_store_settings()
    return render_template(
        "order_detail.html",
        order=order,
        upi_id=store_settings.upi_id,
        store_settings=store_settings,
        qr_image_url=(
            url_for("static", filename="uploads/payment/" + store_settings.qr_filename)
            if store_settings.qr_filename else None
        )
    )


# =========================================================
# UPI UTR SUBMISSION
# =========================================================

@app.route("/submit-utr/<int:order_id>", methods=["POST"])
@login_required
def submit_utr(order_id):
    order = db.session.get(Order, order_id)
    if not order or order.customer_id != current_user.id:
        abort(404)

    if order.payment_method != "UPI":
        flash("This order does not use UPI.")
        return redirect(url_for("order_detail", order_id=order.id))
    if order.payment_status == "Verified":
        flash("Payment is already verified.")
        return redirect(url_for("order_detail", order_id=order.id))

    utr = request.form.get("utr_number", "").strip()
    if not utr or len(utr) < 8 or len(utr) > 100:
        flash("Enter a valid transaction reference number.")
        return redirect(url_for("order_detail", order_id=order.id))

    existing = Order.query.filter(
        Order.utr_number == utr, Order.id != order.id
    ).first()
    if existing:
        flash("This transaction reference has already been submitted.")
        return redirect(url_for("order_detail", order_id=order.id))

    order.utr_number = utr
    order.payment_status = "Verification Pending"
    db.session.commit()
    flash(
        "UTR submitted. Payment will be marked verified only after transaction confirmation."
    )
    return redirect(url_for("order_detail", order_id=order.id))


# =========================================================
# ADMIN PAYMENT VERIFICATION
# =========================================================

@app.route("/admin/verify-payment/<int:order_id>", methods=["POST"])
@login_required
def verify_payment(order_id):
    if current_user.role != "admin":
        abort(403)

    order = db.session.get(Order, order_id)
    if not order:
        abort(404)
    if order.payment_method != "UPI" or not order.utr_number:
        flash("No UPI UTR has been submitted.")
        return redirect(url_for("admin_dashboard"))

    order.payment_status = "Verified"
    db.session.commit()
    flash("Payment marked as verified.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/reject-payment/<int:order_id>", methods=["POST"])
@login_required
def reject_payment(order_id):
    if current_user.role != "admin":
        abort(403)

    order = db.session.get(Order, order_id)
    if not order:
        abort(404)

    order.payment_status = "Rejected"
    db.session.commit()
    flash("Payment rejected.")
    return redirect(url_for("admin_dashboard"))


# =========================================================
# ADMIN ORDER STATUS
# =========================================================

@app.route("/admin/update-order/<int:order_id>", methods=["POST"])
@login_required
def admin_update_order(order_id):
    if current_user.role != "admin":
        abort(403)

    order = db.session.get(Order, order_id)
    if not order:
        abort(404)

    new_status = request.form.get("order_status")
    allowed_statuses = [
        "Pending", "Confirmed", "Shipped", "Delivered", "Cancelled"
    ]
    if new_status not in allowed_statuses:
        flash("Invalid order status.")
        return redirect(url_for("admin_dashboard"))

    if new_status in ("Confirmed", "Shipped", "Delivered"):
        if order.payment_method == "UPI" and order.payment_status != "Verified":
            flash("UPI payment must be verified before processing this order.")
            return redirect(url_for("admin_dashboard"))

    # Restore inventory only on the first transition to Cancelled.
    if new_status == "Cancelled" and order.order_status != "Cancelled":
        for item in order.items:
            product = db.session.get(Product, item.product_id)
            if product:
                product.stock += item.quantity

    order.order_status = new_status
    db.session.commit()
    flash("Order status updated.")
    return redirect(url_for("admin_dashboard"))


# =========================================================
# SELLER ORDER STATUS
# =========================================================

@app.route("/seller/update-order/<int:order_id>", methods=["POST"])
@login_required
def seller_update_order(order_id):
    if current_user.role != "seller":
        abort(403)

    order = db.session.get(Order, order_id)
    if not order:
        abort(404)

    seller_items = [
        item for item in order.items
        if item.seller_id == current_user.id
    ]
    if not seller_items:
        abort(403)

    new_status = request.form.get("order_status")
    if new_status not in ("Confirmed", "Shipped"):
        flash("Invalid seller order status.")
        return redirect(url_for("dashboard"))

    if order.payment_method == "UPI" and order.payment_status != "Verified":
        flash("Payment must be verified first.")
        return redirect(url_for("dashboard"))

    order.order_status = new_status
    db.session.commit()
    flash("Order status updated.")
    return redirect(url_for("dashboard"))


# =========================================================
# CUSTOMER REVIEWS
# =========================================================

@app.route("/add-review/<int:product_id>", methods=["POST"])
@login_required
def add_review(product_id):
    if current_user.role != "customer":
        abort(403)

    product = db.session.get(Product, product_id)
    if not product or not product.approved:
        abort(404)

    try:
        rating = int(request.form.get("rating", 0))
    except (ValueError, TypeError):
        rating = 0

    comment = request.form.get("comment", "").strip()
    if rating < 1 or rating > 5:
        flash("Select a rating between 1 and 5.")
        return redirect(url_for("product_detail", product_id=product_id))

    delivered_items = (
        OrderItem.query.join(Order)
        .filter(
            Order.customer_id == current_user.id,
            Order.order_status == "Delivered",
            OrderItem.product_id == product_id
        ).all()
    )
    if not delivered_items:
        flash("Only customers with delivered orders can review this product.")
        return redirect(url_for("product_detail", product_id=product_id))

    order_id = delivered_items[0].order_id
    already_reviewed = Review.query.filter_by(
        product_id=product_id,
        customer_id=current_user.id,
        order_id=order_id
    ).first()
    if already_reviewed:
        flash("You have already reviewed this purchase.")
        return redirect(url_for("product_detail", product_id=product_id))

    uploaded_files = [
        file for file in request.files.getlist("media")
        if file and file.filename
    ]
    if len(uploaded_files) > 3:
        flash("Maximum 3 review photos/videos allowed.")
        return redirect(url_for("product_detail", product_id=product_id))

    saved_files = []
    try:
        review = Review(
            product_id=product_id,
            customer_id=current_user.id,
            order_id=order_id,
            rating=rating,
            comment=comment,
            approved=False
        )
        db.session.add(review)
        db.session.flush()

        for file in uploaded_files:
            extension = file.filename.rsplit(".", 1)[-1].lower()
            if extension in IMAGE_EXTENSIONS:
                filename = save_upload(file, REVIEW_FOLDER, IMAGE_EXTENSIONS)
                media_type = "image"
            elif extension in VIDEO_EXTENSIONS:
                filename = save_upload(file, REVIEW_FOLDER, VIDEO_EXTENSIONS)
                media_type = "video"
            else:
                raise ValueError("Unsupported review media format.")

            saved_files.append(filename)
            db.session.add(
                ReviewMedia(
                    review_id=review.id,
                    filename=filename,
                    media_type=media_type
                )
            )

        db.session.commit()
        flash("Review submitted for admin approval.")
    except Exception:
        db.session.rollback()
        for filename in saved_files:
            path = os.path.join(REVIEW_FOLDER, filename)
            if os.path.exists(path):
                os.remove(path)
        app.logger.exception("Review upload failed")
        flash("Review could not be submitted.")

    return redirect(url_for("product_detail", product_id=product_id))


# =========================================================
# ADMIN REVIEW MODERATION
# =========================================================

@app.route("/admin/approve-review/<int:review_id>", methods=["POST"])
@login_required
def approve_review(review_id):
    if current_user.role != "admin":
        abort(403)

    review = db.session.get(Review, review_id)
    if not review:
        abort(404)

    review.approved = True
    db.session.commit()
    flash("Review approved.")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/reject-review/<int:review_id>", methods=["POST"])
@login_required
def reject_review(review_id):
    if current_user.role != "admin":
        abort(403)

    review = db.session.get(Review, review_id)
    if not review:
        abort(404)

    db.session.delete(review)
    db.session.commit()
    flash("Review rejected.")
    return redirect(url_for("admin_dashboard"))


# =========================================================
# CREATE ADMIN COMMAND
# =========================================================

@app.cli.command("create-admin")
def create_admin():
    if User.query.filter_by(role="admin").first():
        click.echo("Admin already exists.")
        return

    name = click.prompt("Admin name").strip()
    email = click.prompt("Admin email").strip().lower()

    if not name or len(name) > 100 or not valid_email(email):
        click.echo("Invalid name or email.")
        return
    if User.query.filter_by(email=email).first():
        click.echo("Email already registered.")
        return

    password = click.prompt(
        "Admin password", hide_input=True, confirmation_prompt=True
    )
    if len(password) < 8:
        click.echo("Password must be at least 8 characters.")
        return

    admin = User(
        name=name,
        email=email,
        password=generate_password_hash(password),
        role="admin"
    )
    db.session.add(admin)
    db.session.commit()
    click.echo("Admin created successfully!")


# =========================================================
# LOGOUT / ERROR HANDLERS
# =========================================================

@app.route("/logout")
@login_required
def logout():
    logout_user()
    session.pop("buy_now_product_id", None)
    return redirect(url_for("home"))


@app.errorhandler(403)
def forbidden(error):
    return "Access Denied", 403


@app.errorhandler(404)
def not_found(error):
    return "Page Not Found", 404


@app.errorhandler(413)
def file_too_large(error):
    return "Uploaded file is too large. Maximum total upload size is 50 MB.", 413


# =========================================================
# RUN APP
# =========================================================

if __name__ == "__main__":
    app.run(debug=True)
