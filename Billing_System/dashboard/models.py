from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from accounts.models import companyRegistration


class TelegramOwnerLink(models.Model):
    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="telegram_links"
    )
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="telegram_links"
    )
    pairing_code = models.CharField(max_length=16, db_index=True)
    pairing_code_expires_at = models.DateTimeField()
    telegram_chat_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    telegram_username = models.CharField(max_length=100, blank=True, default="")
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"TelegramLink: {self.company.company_name} - ChatID:{self.telegram_chat_id} (Verified:{self.is_verified})"


class TelegramOutboundMessage(models.Model):
    STATUS_CHOICES = [
        ("QUEUED", "Queued"),
        ("SENT", "Sent"),
        ("FAILED", "Failed"),
    ]
    MEDIA_TYPE_CHOICES = [
        ("TEXT", "Text"),
        ("DOCUMENT", "Document"),
        ("PHOTO", "Photo"),
    ]
    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="telegram_messages"
    )
    chat_id = models.CharField(max_length=64)
    message_text = models.TextField()
    media_type = models.CharField(max_length=20, choices=MEDIA_TYPE_CHOICES, default="TEXT")
    media_path = models.CharField(max_length=500, blank=True, default="")
    media_filename = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="QUEUED")
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"TelegramOutbound [{self.status}|{self.media_type}] to {self.chat_id} ({self.created_at})"


class RemoteSyncStatus(models.Model):
    """
    Authoritative state of shop-to-cloud synchronization per store.
    Tracks offline buffer, connectivity health, and last successful sync timestamp.
    """
    STATUS_CHOICES = [
        ("ONLINE", "Online"),
        ("OFFLINE", "Offline"),
        ("SYNCING", "Syncing"),
        ("ERROR", "Error"),
    ]
    company = models.OneToOneField(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="sync_status"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ONLINE")
    last_sync_success = models.DateTimeField(null=True, blank=True)
    last_sync_attempt = models.DateTimeField(null=True, blank=True)
    pending_count = models.IntegerField(default=0)
    total_synced_count = models.IntegerField(default=0)
    last_error_message = models.TextField(blank=True, default="")
    cloud_sync_api_key = models.CharField(max_length=64, blank=True, default="")
    remote_cloud_url = models.CharField(max_length=255, blank=True, default="")
    last_snapshot_time = models.DateTimeField(null=True, blank=True)
    checkpoint = models.BigIntegerField(default=0)
    consecutive_failures = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Remote Sync Status"
        verbose_name_plural = "Remote Sync Statuses"

    def __str__(self):
        return f"SyncStatus: {self.company.company_name} [{self.status}] Pending: {self.pending_count}"


class SyncQueueItem(models.Model):
    """
    Local queue buffer for transactions created offline or awaiting cloud sync.
    Ensures zero loss of sales, payments, returns, or purchases during shop ISP outages.
    """
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("SYNCED", "Synced"),
        ("FAILED", "Failed"),
    ]
    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="sync_queue"
    )
    entity_type = models.CharField(max_length=30)  # SALE, PAYMENT, RETURN, PURCHASE, CUSTOMER, STOCK
    entity_id = models.CharField(max_length=100)
    event_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    idempotency_key = models.CharField(max_length=128, db_index=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING", db_index=True)
    retry_count = models.IntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["idempotency_key"]),
        ]

    def __str__(self):
        return f"SyncQueueItem {self.entity_type} #{self.entity_id} [{self.status}]"


class RemoteSyncedRecord(models.Model):
    """
    Audit ledger on remote reporting database guaranteeing idempotent sync.
    Prevents duplicate sales, payments, returns, or purchases even under repeated retries.
    """
    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="remote_synced_records"
    )
    entity_type = models.CharField(max_length=30)
    idempotency_key = models.CharField(max_length=128, unique=True, db_index=True)
    entity_identifier = models.CharField(max_length=100)
    payload_summary = models.JSONField(default=dict)
    synced_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-synced_at"]

    def __str__(self):
        return f"RemoteSyncedRecord {self.entity_type} {self.entity_identifier} ({self.idempotency_key})"


class InternalNotification(models.Model):
    """
    In-app staff notification and communication workflow.
    Tenant-isolated, role-verified, auditable messaging between Owner, Manager, and Cashiers.
    """
    PRIORITY_CHOICES = [
        ("NORMAL", "Normal"),
        ("HIGH", "High"),
        ("URGENT", "Urgent"),
    ]
    STATUS_CHOICES = [
        ("QUEUED", "Queued"),
        ("DELIVERED", "Delivered"),
        ("READ", "Read"),
    ]

    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="internal_notifications"
    )
    sender = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="sent_internal_notifications"
    )
    sender_role = models.CharField(max_length=50, default="Staff")
    recipient = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="received_internal_notifications"
    )
    recipient_role = models.CharField(max_length=50, blank=True, default="")
    title = models.CharField(max_length=200, blank=True, default="Staff Notice")
    message = models.TextField()
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default="NORMAL")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="DELIVERED")
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "recipient", "is_read"]),
            models.Index(fields=["company", "recipient_role"]),
        ]

    def __str__(self):
        recip = self.recipient.username if self.recipient else f"Role:{self.recipient_role}"
        return f"InternalNotification [{self.sender_role} -> {recip}] ({self.status}): {self.message[:30]}"


class MerchantTelegramBot(models.Model):
    """
    Dedicated per-merchant Telegram bot configured via BotFather.
    Guarantees:
    - Encrypted token at rest (Fernet AES-128 + HMAC).
    - Strict tenant isolation: each store has its own bot instance.
    - One-time owner verification challenge.
    - Never exposes token to AI prompts, logs, templates, or URLs.
    """
    STATUS_CHOICES = [
        ("NOT_CONFIGURED", "Not Configured"),
        ("PENDING_VERIFICATION", "Pending Verification"),
        ("CONNECTED", "Connected"),
        ("INVALID_TOKEN", "Invalid Token"),
        ("DISCONNECTED", "Disconnected"),
        ("REAUTH_REQUIRED", "Re-authentication Required"),
    ]

    company = models.OneToOneField(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="custom_telegram_bot"
    )
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="custom_telegram_bots"
    )
    encrypted_token = models.TextField(blank=True, default="")
    bot_id = models.CharField(max_length=64, blank=True, default="")
    bot_username = models.CharField(max_length=100, blank=True, default="")
    bot_first_name = models.CharField(max_length=150, blank=True, default="")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="NOT_CONFIGURED")
    verification_challenge = models.CharField(max_length=64, blank=True, default="", db_index=True)
    verification_expires_at = models.DateTimeField(null=True, blank=True)
    verified_chat_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    verified_username = models.CharField(max_length=100, blank=True, default="")
    verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    last_health_check = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Merchant Telegram Bot"
        verbose_name_plural = "Merchant Telegram Bots"

    def __str__(self):
        return f"MerchantBot: {self.company.company_name} (@{self.bot_username or 'unset'}) [{self.status}]"

    def get_decrypted_token(self) -> str:
        from .token_encryption import decrypt_telegram_token
        return decrypt_telegram_token(self.encrypted_token)

    def set_and_encrypt_token(self, raw_token: str):
        from .token_encryption import encrypt_telegram_token
        self.encrypted_token = encrypt_telegram_token(raw_token)


class CompanyCloudSnapshot(models.Model):
    """
    Durable, tenant-isolated cloud snapshot of store business data.
    Allows 24/7 Telegram bot answering when the physical shop PC is closed or offline.
    Never exposes raw customer passwords or private employee payroll details.
    """
    FRESHNESS_CHOICES = [
        ("LIVE", "Live"),
        ("CACHED", "Cached (Store Offline)"),
        ("STALE", "Stale (Sync Overdue)"),
        ("SYNC_PENDING", "Sync Pending (Unsynced Sales)"),
    ]

    company = models.ForeignKey(
        companyRegistration,
        on_delete=models.CASCADE,
        related_name="cloud_snapshots"
    )
    snapshot_date = models.DateField(db_index=True)
    last_sync_time = models.DateTimeField(default=timezone.now)
    freshness_status = models.CharField(
        max_length=30,
        choices=FRESHNESS_CHOICES,
        default="LIVE"
    )
    pending_local_count = models.IntegerField(default=0)

    # Financial Aggregates
    total_revenue = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    net_revenue = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    invoice_count = models.IntegerField(default=0)

    # Tender Breakdown
    cash_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    upi_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    card_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    other_payment_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    # Returns & Refunds
    returns_count = models.IntegerField(default=0)
    refund_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))

    # Inventory Stock Intelligence
    active_products_count = models.IntegerField(default=0)
    low_stock_items = models.JSONField(default=list)
    out_of_stock_items = models.JSONField(default=list)
    top_selling_products = models.JSONField(default=list)

    # Customer Balances (Tenant isolated, no private PII)
    total_credit_receivable = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    customer_balances = models.JSONField(default=list)

    # Raw Payload JSON for forward compatibility
    raw_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-snapshot_date", "-last_sync_time"]
        unique_together = ("company", "snapshot_date")
        indexes = [
            models.Index(fields=["company", "snapshot_date"]),
            models.Index(fields=["company", "freshness_status"]),
        ]

    def __str__(self):
        return f"CloudSnapshot: {self.company.company_name} [{self.snapshot_date}] - {self.freshness_status} (Sync: {self.last_sync_time})"


