import os
import uuid
import secrets
import click

import firebase_admin
from firebase_admin import credentials, auth

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash, jsonify
)

from flask_sqlalchemy import SQLAlchemy

from flask_login import (
    LoginManager, UserMixin, login_user,
    logout_user, login_required, current_user
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from sqlalchemy import text, inspect


# APP CONFIGURATION

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY", "change-this-before-deployment"
)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///suman_store.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.config["UPLOAD_FOLDER"] = os.path.join(
    app.root_path, "static", "uploads"
)

app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}


# FIREBASE ADMIN CONNECTION

firebase_key = os.path.join(
    app.root_path, "serviceAccountKey.json"
)

if not os.path.exists(firebase_key):
    raise FileNotFoundError(
        "serviceAccountKey.json project folder mein nahi mili."
    )

if not firebase_admin._apps:
    firebase_credential = credentials.Certificate(firebase_key)
    firebase_admin.initialize_app(firebase_credential)


# DATABASE

db = SQLAlchemy(app)


# LOGIN MANAGER

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


# USER MODEL

class User(UserMixin, db.Model):

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(100), nullable=False)

    email = db.Column(db.String(150), unique=True, nullable=False)

    password = db.Column(db.String(255), nullable=False)

    role = db.Column(db.String(20), default="customer")

    firebase_uid = db.Column(db.String(128), unique=True)

    phone = db.Column(db.String(30))


# PRODUCT MODEL

class Product(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(150), nullable=False)

    description = db.Column(db.Text)

    price = db.Column(db.Float, nullable=False)

    image = db.Column(db.String(255))

    stock = db.Column(db.Integer, default=0)

    seller_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id")
    )

    approved = db.Column(db.Boolean, default=False)


# CART MODEL

class Cart(db.Model):

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("product.id"),
        nullable=False
    )

    quantity = db.Column(db.Integer, default=1)

    product = db.relationship("Product")


# LOAD USER

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# DATABASE MIGRATION

with app.app_context():

    db.create_all()

    inspector = inspect(db.engine)

    columns = [
        column["name"]
        for column in inspector.get_columns("user")
    ]

    if "firebase_uid" not in columns:
        db.session.execute(
            text("ALTER TABLE user ADD COLUMN firebase_uid VARCHAR(128)")
        )

    if "phone" not in columns:
        db.session.execute(
            text("ALTER TABLE user ADD COLUMN phone VARCHAR(30)")
        )

    db.session.commit()


# IMAGE VALIDATION

def allowed_file(filename):

    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


# FIREBASE USER HELPER

def get_firebase_user(data):

    id_token = data.get("idToken")

    if not id_token:
        raise ValueError("Firebase ID token missing")

    decoded_token = auth.verify_id_token(id_token)

    firebase_uid = decoded_token["uid"]

    email = decoded_token.get("email")

    phone = decoded_token.get("phone_number")

    name = decoded_token.get("name")

    email_verified = decoded_token.get("email_verified", False)

    return {
        "uid": firebase_uid,
        "email": email.lower() if email else None,
        "phone": phone,
        "name": name,
        "email_verified": email_verified
    }


# FIREBASE ACCOUNT CREATE / UPDATE

def save_firebase_user(firebase_data, requested_name=None, requested_role=None):

    uid = firebase_data["uid"]
    email = firebase_data["email"]
    phone = firebase_data["phone"]

    user = User.query.filter_by(
        firebase_uid=uid
    ).first()

    # Existing account

    if user:

        if phone:
            user.phone = phone

        if firebase_data["name"]:
            user.name = firebase_data["name"]

        db.session.commit()

        return user

    # Find existing email account

    if email:

        existing_user = User.query.filter_by(
            email=email
        ).first()

        if existing_user:

            if not firebase_data["email_verified"]:
                raise ValueError("Email verification required")

            existing_user.firebase_uid = uid

            if phone:
                existing_user.phone = phone

            db.session.commit()

            return existing_user

    # New account details

    name = (
        requested_name
        or firebase_data["name"]
        or phone
        or "Suman Store User"
    )

    role = requested_role or "customer"

    if role not in ["customer", "seller"]:
        raise ValueError("Invalid account type")

    # Phone accounts may not have an email

    if not email:
        email = f"firebase_{uid}@sumanstore.local"

    user = User(
        name=name,
        email=email,
        password=generate_password_hash(
            secrets.token_urlsafe(32)
        ),
        role=role,
        firebase_uid=uid,
        phone=phone
    )

    db.session.add(user)
    db.session.commit()

    return user


# HOME PAGE

@app.route("/")
def home():

    products = Product.query.filter_by(
        approved=True
    ).all()

    return render_template(
        "index.html",
        products=products
    )


# REGISTER PAGE

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get("name", "").strip()

        email = request.form.get("email", "").strip().lower()

        password = request.form.get("password", "")

        role = request.form.get("role")

        if not name or not email or not password:
            flash("Please fill all fields")
            return redirect(url_for("register"))

        if role not in ["customer", "seller"]:
            flash("Invalid account type")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Password must be at least 8 characters")
            return redirect(url_for("register"))

        if User.query.filter_by(email=email).first():
            flash("Email already registered")
            return redirect(url_for("register"))

        user = User(
            name=name,
            email=email,
            password=generate_password_hash(password),
            role=role
        )

        db.session.add(user)
        db.session.commit()

        flash("Account created successfully!")

        return redirect(url_for("login"))

    return render_template("register.html")


# EMAIL PASSWORD LOGIN

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()

        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password, password):

            login_user(user)

            return redirect(url_for("dashboard"))

        flash("Invalid email or password")

    return render_template("login.html")


# FIREBASE PHONE / GOOGLE LOGIN

@app.route("/firebase-login", methods=["POST"])
def firebase_login():

    try:

        data = request.get_json(silent=True) or {}

        firebase_data = get_firebase_user(data)

        user = save_firebase_user(firebase_data)

        login_user(user)

        return jsonify({
            "success": True,
            "message": "Login successful",
            "redirect": url_for("dashboard")
        })

    except Exception:

        db.session.rollback()

        app.logger.exception("Firebase login failed")

        return jsonify({
            "success": False,
            "message": "Firebase login failed. Please try again."
        }), 401


# FIREBASE PHONE / GOOGLE REGISTRATION

@app.route("/firebase-register", methods=["POST"])
def firebase_register():

    try:

        data = request.get_json(silent=True) or {}

        name = data.get("name", "").strip()

        role = data.get("role")

        if not name:
            return jsonify({
                "success": False,
                "message": "Please enter your name"
            }), 400

        if role not in ["customer", "seller"]:
            return jsonify({
                "success": False,
                "message": "Invalid account type"
            }), 400

        firebase_data = get_firebase_user(data)

        # Registration requires verified phone or verified Google email

        if not firebase_data["phone"] and not firebase_data["email_verified"]:
            return jsonify({
                "success": False,
                "message": "Verified phone or email required"
            }), 403

        user = save_firebase_user(
            firebase_data,
            requested_name=name,
            requested_role=role
        )

        login_user(user)

        return jsonify({
            "success": True,
            "message": "Registration successful",
            "redirect": url_for("dashboard")
        })

    except Exception:

        db.session.rollback()

        app.logger.exception("Firebase registration failed")

        return jsonify({
            "success": False,
            "message": "Registration failed. Please try again."
        }), 400


# DASHBOARD

@app.route("/dashboard")
@login_required
def dashboard():

    if current_user.role == "admin":
        return redirect(url_for("admin_dashboard"))

    if current_user.role == "seller":
        return render_template("seller_dashboard.html")

    return render_template("customer_dashboard.html")


# ADD PRODUCT

@app.route("/add-product", methods=["GET", "POST"])
@login_required
def add_product():

    if current_user.role != "seller":
        return "Access Denied", 403

    if request.method == "POST":

        name = request.form.get("name", "").strip()

        description = request.form.get("description", "").strip()

        try:
            price = float(request.form.get("price", 0))
            stock = int(request.form.get("stock", 0))

        except (ValueError, TypeError):

            flash("Invalid price or stock")

            return redirect(url_for("add_product"))

        if not name or not description or price <= 0 or stock < 0:

            flash("Invalid product details")

            return redirect(url_for("add_product"))

        image_file = request.files.get("image")

        image_name = None

        if image_file and image_file.filename:

            if not allowed_file(image_file.filename):

                flash("Invalid image format")

                return redirect(url_for("add_product"))

            extension = image_file.filename.rsplit(".", 1)[1].lower()

            image_name = f"{uuid.uuid4().hex}.{extension}"

            image_file.save(
                os.path.join(
                    app.config["UPLOAD_FOLDER"],
                    image_name
                )
            )

        product = Product(
            name=name,
            description=description,
            price=price,
            stock=stock,
            image=image_name,
            seller_id=current_user.id,
            approved=False
        )

        db.session.add(product)

        db.session.commit()

        flash("Product submitted for approval!")

        return redirect(url_for("dashboard"))

    return render_template("add_product.html")


# ADMIN DASHBOARD

@app.route("/admin")
@login_required
def admin_dashboard():

    if current_user.role != "admin":
        return "Access Denied", 403

    products = Product.query.filter_by(
        approved=False
    ).all()

    return render_template(
        "admin_dashboard.html",
        products=products
    )


# APPROVE PRODUCT

@app.route("/approve-product/<int:product_id>", methods=["POST"])
@login_required
def approve_product(product_id):

    if current_user.role != "admin":
        return "Access Denied", 403

    product = db.session.get(Product, product_id)

    if product is None:
        return "Product not found", 404

    product.approved = True

    db.session.commit()

    flash("Product approved!")

    return redirect(url_for("admin_dashboard"))


# REJECT PRODUCT

@app.route("/reject-product/<int:product_id>", methods=["POST"])
@login_required
def reject_product(product_id):

    if current_user.role != "admin":
        return "Access Denied", 403

    product = db.session.get(Product, product_id)

    if product is None:
        return "Product not found", 404

    db.session.delete(product)

    db.session.commit()

    flash("Product rejected!")

    return redirect(url_for("admin_dashboard"))


# ADD TO CART

@app.route("/add-to-cart/<int:product_id>", methods=["POST"])
@login_required
def add_to_cart(product_id):

    if current_user.role != "customer":
        return "Only customers can use cart", 403

    product = db.session.get(Product, product_id)

    if product is None or not product.approved:
        return "Product unavailable", 404

    if product.stock < 1:

        flash("Product is out of stock")

        return redirect(url_for("home"))

    item = Cart.query.filter_by(
        user_id=current_user.id,
        product_id=product_id
    ).first()

    if item:

        if item.quantity < product.stock:
            item.quantity += 1
        else:
            flash("Maximum stock reached")

    else:

        item = Cart(
            user_id=current_user.id,
            product_id=product_id,
            quantity=1
        )

        db.session.add(item)

    db.session.commit()

    flash("Product added to cart!")

    return redirect(url_for("cart"))


# CART PAGE

@app.route("/cart")
@login_required
def cart():

    if current_user.role != "customer":
        return "Only customers can use cart", 403

    items = Cart.query.filter_by(
        user_id=current_user.id
    ).all()

    total = sum(
        item.product.price * item.quantity
        for item in items
    )

    return render_template(
        "cart.html",
        cart_items=items,
        total=total
    )


# REMOVE FROM CART

@app.route("/remove-from-cart/<int:item_id>", methods=["POST"])
@login_required
def remove_from_cart(item_id):

    item = db.session.get(Cart, item_id)

    if item is None or item.user_id != current_user.id:
        return "Cart item not found", 404

    db.session.delete(item)

    db.session.commit()

    flash("Product removed from cart!")

    return redirect(url_for("cart"))


# CREATE ADMIN

@app.cli.command("create-admin")
def create_admin():

    if User.query.filter_by(role="admin").first():

        click.echo("Admin already exists.")

        return

    name = click.prompt("Admin name")

    email = click.prompt("Admin email").strip().lower()

    if User.query.filter_by(email=email).first():

        click.echo("Email already registered.")

        return

    password = click.prompt(
        "Admin password",
        hide_input=True,
        confirmation_prompt=True
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


# LOGOUT

@app.route("/logout")
@login_required
def logout():

    logout_user()

    return redirect(url_for("home"))


# RUN WEBSITE

if __name__ == "__main__":

    app.run(debug=True)