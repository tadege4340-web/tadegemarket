
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import (
    UniqueConstraint,
    CheckConstraint,
    Index
)

# =========================================================
# DATABASE INSTANCE
# =========================================================

db = SQLAlchemy()


def utc_now():
    """Return the current UTC date and time."""
    return datetime.now(timezone.utc)


# =========================================================
# USER MODEL
# =========================================================

class User(db.Model):

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="customer", nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    orders = db.relationship("Order", back_populates="user", lazy=True)

    cart_items = db.relationship(
        "CartItem",
        back_populates="user",
        lazy=True,
        cascade="all, delete-orphan"
    )

    support_conversations = db.relationship(
        "SupportConversation",
        back_populates="customer",
        foreign_keys="SupportConversation.customer_id",
        lazy=True,
        cascade="all, delete-orphan"
    )

    support_messages = db.relationship(
        "SupportMessage",
        back_populates="sender",
        foreign_keys="SupportMessage.sender_id",
        lazy=True
    )

    # NEW: Customer notifications
    notifications = db.relationship(
        "Notification",
        back_populates="user",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="Notification.created_at.desc()"
    )

    def __repr__(self):
        return f"<User {self.username}>"


# =========================================================
# CATEGORY MODEL
# =========================================================

class Category(db.Model):

    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=False)

    products = db.relationship(
        "Product",
        back_populates="category",
        lazy=True
    )

    def __repr__(self):
        return f"<Category {self.name}>"


# =========================================================
# PRODUCT MODEL
# =========================================================

class Product(db.Model):

    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, index=True)
    description = db.Column(db.Text)
    price = db.Column(db.Numeric(10, 2), nullable=False)

    category_id = db.Column(
        db.Integer,
        db.ForeignKey("categories.id"),
        nullable=True,
        index=True
    )

    icon = db.Column(db.String(50), default="📦")
    tag = db.Column(db.String(50))
    image = db.Column(db.String(255))
    stock = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    category = db.relationship("Category", back_populates="products")

    order_items = db.relationship(
        "OrderItem",
        back_populates="product",
        lazy=True
    )

    cart_items = db.relationship(
        "CartItem",
        back_populates="product",
        lazy=True,
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("price > 0", name="check_product_price_positive"),
        CheckConstraint("stock >= 0", name="check_product_stock_nonnegative"),
        Index("idx_product_active_created", "is_active", "created_at"),
    )

    @property
    def category_name(self):
        return self.category.name if self.category else "Uncategorized"

    def __repr__(self):
        return f"<Product {self.name}>"


# =========================================================
# CART MODEL
# =========================================================

class CartItem(db.Model):

    __tablename__ = "cart_items"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=False,
        index=True
    )

    quantity = db.Column(db.Integer, default=1, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    user = db.relationship("User", back_populates="cart_items")
    product = db.relationship("Product", back_populates="cart_items")

    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="unique_user_product_cart"),
        CheckConstraint("quantity > 0", name="check_cart_quantity_positive"),
    )

    @property
    def subtotal(self):
        if not self.product:
            return 0
        return self.product.price * self.quantity

    def __repr__(self):
        return f"<CartItem {self.id}>"


# =========================================================
# ORDER MODEL
# =========================================================

class Order(db.Model):

    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    total_amount = db.Column(db.Numeric(10, 2), nullable=False)

    status = db.Column(
        db.String(30),
        default="pending",
        nullable=False,
        index=True
    )

    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    user = db.relationship("User", back_populates="orders")

    items = db.relationship(
        "OrderItem",
        back_populates="order",
        lazy=True,
        cascade="all, delete-orphan"
    )

    payments = db.relationship(
        "Payment",
        back_populates="order",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="Payment.created_at.desc()"
    )

    __table_args__ = (
        CheckConstraint("total_amount >= 0", name="check_order_total_nonnegative"),
        Index("idx_order_user_created", "user_id", "created_at"),
    )

    def __repr__(self):
        return f"<Order {self.id}>"


# =========================================================
# ORDER ITEM MODEL
# =========================================================

class OrderItem(db.Model):

    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)

    order_id = db.Column(
        db.Integer,
        db.ForeignKey("orders.id"),
        nullable=False,
        index=True
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=False,
        index=True
    )

    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)

    order = db.relationship("Order", back_populates="items")
    product = db.relationship("Product", back_populates="order_items")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="check_order_item_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="check_order_item_price_nonnegative"),
    )

    @property
    def subtotal(self):
        return self.unit_price * self.quantity

    def __repr__(self):
        return f"<OrderItem {self.id}>"


# =========================================================
# PAYMENT MODEL
# =========================================================

class Payment(db.Model):

    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)

    order_id = db.Column(
        db.Integer,
        db.ForeignKey("orders.id"),
        nullable=False,
        index=True
    )

    provider = db.Column(
        db.String(30),
        default="chapa",
        nullable=False,
        index=True
    )

    tx_ref = db.Column(
        db.String(120),
        unique=True,
        nullable=False,
        index=True
    )

    provider_reference = db.Column(
        db.String(150),
        nullable=True
    )

    amount = db.Column(
        db.Numeric(10, 2),
        nullable=False
    )

    currency = db.Column(
        db.String(10),
        default="ETB",
        nullable=False
    )

    status = db.Column(
        db.String(30),
        default="initiated",
        nullable=False,
        index=True
    )

    checkout_url = db.Column(db.Text, nullable=True)

    recipient_name = db.Column(db.String(150), nullable=False)
    recipient_phone = db.Column(db.String(30), nullable=False)
    delivery_address = db.Column(db.Text, nullable=False)
    delivery_city = db.Column(db.String(100), nullable=False)
    delivery_notes = db.Column(db.Text, nullable=True)

    receipt_filename = db.Column(
        db.String(255),
        nullable=True
    )

    receipt_uploaded_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    admin_note = db.Column(
        db.Text,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=utc_now,
        nullable=False
    )

    verified_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    order = db.relationship("Order", back_populates="payments")

    __table_args__ = (
        CheckConstraint("amount > 0", name="check_payment_amount_positive"),
        Index("idx_payment_order_created", "order_id", "created_at"),
        Index("idx_payment_status_created", "status", "created_at"),
    )

    def __repr__(self):
        return f"<Payment {self.tx_ref}>"


# =========================================================
# CUSTOMER SUPPORT CONVERSATION MODEL
# =========================================================

class SupportConversation(db.Model):

    __tablename__ = "support_conversations"

    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    subject = db.Column(db.String(200), nullable=False)

    status = db.Column(
        db.String(20),
        default="open",
        nullable=False,
        index=True
    )

    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    customer = db.relationship(
        "User",
        back_populates="support_conversations",
        foreign_keys=[customer_id]
    )

    messages = db.relationship(
        "SupportMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="SupportMessage.created_at.asc()",
        lazy=True
    )

    __table_args__ = (
        Index("idx_support_customer_created", "customer_id", "created_at"),
    )

    def __repr__(self):
        return f"<SupportConversation {self.id}>"


# =========================================================
# CUSTOMER SUPPORT MESSAGE MODEL
# =========================================================

class SupportMessage(db.Model):

    __tablename__ = "support_messages"

    id = db.Column(db.Integer, primary_key=True)

    conversation_id = db.Column(
        db.Integer,
        db.ForeignKey("support_conversations.id"),
        nullable=False,
        index=True
    )

    sender_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    message = db.Column(db.Text, nullable=False)

    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    is_read = db.Column(db.Boolean, default=False, nullable=False)

    conversation = db.relationship(
        "SupportConversation",
        back_populates="messages"
    )

    sender = db.relationship(
        "User",
        back_populates="support_messages",
        foreign_keys=[sender_id]
    )

    __table_args__ = (
        Index(
            "idx_support_message_conversation_created",
            "conversation_id",
            "created_at"
        ),
    )

    def __repr__(self):
        return f"<SupportMessage {self.id}>"


# =========================================================
# NEW: CUSTOMER NOTIFICATION MODEL
# =========================================================

class Notification(db.Model):

    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    title = db.Column(
        db.String(150),
        nullable=False
    )

    message = db.Column(
        db.Text,
        nullable=False
    )

    # order_delivered, order_rejected, order_updated, payment_updated
    notification_type = db.Column(
        db.String(40),
        default="order_updated",
        nullable=False,
        index=True
    )

    # Optional link to the relevant order page
    link = db.Column(
        db.String(255),
        nullable=True
    )

    is_read = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
        index=True
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True
    )

    user = db.relationship(
        "User",
        back_populates="notifications"
    )

    __table_args__ = (
        Index(
            "idx_notification_user_read_created",
            "user_id",
            "is_read",
            "created_at"
        ),
    )

    def __repr__(self):
        return f"<Notification {self.id}: {self.title}>"
