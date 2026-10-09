from django.db import models
from django.contrib.auth.models import User
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
    entity_type = models.CharField(max_length=30)  # SALE, PAYMENT, RETURN, PURCHASE, CUSTOMER
    entity_id = models.CharField(max_length=100)
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
