from django.contrib import admin
from .models_requests import Request, RequestItem, RequestComment, RequestHistory

class RequestItemInline(admin.TabularInline):
    model = RequestItem
    extra = 0

@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = ("number","title","status","initiator","assignee","created_at")
    list_filter = ("status","created_at")
    search_fields = ("number","title","initiator__username","counterparty__name")
    inlines = [RequestItemInline]

@admin.register(RequestComment)
class RequestCommentAdmin(admin.ModelAdmin):
    list_display = ("request","author","created_at")
    search_fields = ("request__number","author__username","text")

@admin.register(RequestHistory)
class RequestHistoryAdmin(admin.ModelAdmin):
    list_display = ("request","author","from_status","to_status","created_at")
    list_filter = ("to_status",)
