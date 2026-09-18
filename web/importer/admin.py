from django.contrib import admin

from .models import (
    ApiArtifact,
    ApiMutation,
    ApiWorkflow,
    ImportSession,
    Payment,
    SourceFile,
)


class SourceFileInline(admin.TabularInline):
    model = SourceFile
    extra = 0
    readonly_fields = ("role", "original_name", "row_count", "uploaded_at")
    fields = ("role", "original_name", "row_count", "uploaded_at")
    can_delete = False


@admin.register(ImportSession)
class ImportSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "product_key",
        "status",
        "is_legacy",
        "created_at",
        "updated_at",
    )
    list_filter = ("status", "is_legacy", "created_at")
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "options",
        "summary",
        "error_message",
    )
    inlines = [SourceFileInline]

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.is_legacy:
            return tuple(field.name for field in self.model._meta.fields)
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SourceFile)
class SourceFileAdmin(admin.ModelAdmin):
    list_display = ("original_name", "role", "session", "row_count", "uploaded_at")
    list_filter = ("role",)
    search_fields = ("original_name",)

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.session.is_legacy:
            return tuple(field.name for field in self.model._meta.fields)
        return super().get_readonly_fields(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ApiWorkflow)
class ApiWorkflowAdmin(admin.ModelAdmin):
    list_display = ("run_id", "role", "status", "revision", "session", "updated_at")
    list_filter = ("role", "status")
    readonly_fields = ("projection", "projection_digest")

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ApiMutation)
class ApiMutationAdmin(admin.ModelAdmin):
    list_display = (
        "idempotency_key",
        "mutation_kind",
        "state",
        "session",
        "attempt_count",
        "updated_at",
    )
    list_filter = ("state", "mutation_kind")
    readonly_fields = (
        "idempotency_key",
        "request_json",
        "multipart_metadata",
        "request_digest",
        "response_json",
        "response_digest",
        "lease_token",
        "lease_expires_at",
    )

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ApiArtifact)
class ApiArtifactAdmin(admin.ModelAdmin):
    list_display = ("artifact_id", "workflow", "updated_at")
    readonly_fields = ("metadata",)

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "status",
        "plan_name",
        "amount_label",
        "wc_order_id",
        "created_at",
    )
    list_filter = ("status", "provider", "created_at")
    readonly_fields = ("id", "created_at", "updated_at")
    search_fields = ("email", "wc_order_id", "wc_order_key")
