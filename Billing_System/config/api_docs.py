from django.http import JsonResponse, HttpResponse
from django.views import View


OPENAPI_SCHEMA = {
    "openapi": "3.0.3",
    "info": {
        "title": "Retail POS & Billing System API",
        "version": "2.0.0",
        "description": "Comprehensive REST API for Multi-Tenant Retail Point-of-Sale, Barcode Processing, Inventory Management, Returns & Refunds, AI Intelligence, and Telegram Reporting."
    },
    "servers": [
        {"url": "/", "description": "Current Server Environment"}
    ],
    "components": {
        "securitySchemes": {
            "SessionAuth": {
                "type": "apiKey",
                "in": "cookie",
                "name": "sessionid",
                "description": "Django session cookie authentication"
            },
            "CsrfToken": {
                "type": "apiKey",
                "in": "header",
                "name": "X-CSRFToken",
                "description": "Django CSRF header token for mutating requests"
            }
        }
    },
    "security": [
        {"SessionAuth": [], "CsrfToken": []}
    ],
    "paths": {
        "/api/auth/login/": {
            "post": {
                "summary": "Authenticate User",
                "tags": ["Authentication"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["username", "password"],
                                "properties": {
                                    "username": {"type": "string"},
                                    "password": {"type": "string", "format": "password"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {"description": "Authentication successful"},
                    "400": {"description": "Invalid credentials"}
                }
            }
        },
        "/api/auth/logout/": {
            "post": {
                "summary": "Logout Current Session",
                "tags": ["Authentication"],
                "responses": {"200": {"description": "Logged out successfully"}}
            }
        },
        "/api/auth/me/": {
            "get": {
                "summary": "Get Current Authenticated Profile & Role",
                "tags": ["Authentication"],
                "responses": {"200": {"description": "Current user, role, and company details"}}
            }
        },
        "/api/products/": {
            "get": {
                "summary": "List Products",
                "tags": ["Products & Catalog"],
                "responses": {"200": {"description": "List of active products for current tenant"}}
            },
            "post": {
                "summary": "Create Product",
                "tags": ["Products & Catalog"],
                "responses": {"201": {"description": "Product created"}}
            }
        },
        "/api/products/lookup/": {
            "get": {
                "summary": "Barcode & SKU Rapid POS Lookup",
                "tags": ["Products & Catalog"],
                "parameters": [
                    {
                        "name": "code",
                        "in": "query",
                        "required": True,
                        "description": "Barcode, SKU, or Product Name",
                        "schema": {"type": "string"}
                    }
                ],
                "responses": {
                    "200": {"description": "Product details with stock, pricing, and tax rate"},
                    "404": {"description": "Product not found or inactive"}
                }
            }
        },
        "/api/categories/": {
            "get": {
                "summary": "List Categories",
                "tags": ["Products & Catalog"],
                "responses": {"200": {"description": "List of item categories"}}
            }
        },
        "/api/suppliers/": {
            "get": {
                "summary": "List Suppliers",
                "tags": ["Inventory & Supply"],
                "responses": {"200": {"description": "List of registered suppliers"}}
            }
        },
        "/api/inventory/adjust/": {
            "post": {
                "summary": "Adjust Stock Level",
                "tags": ["Inventory & Supply"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["product_id", "quantity", "movement_type"],
                                "properties": {
                                    "product_id": {"type": "integer"},
                                    "quantity": {"type": "number"},
                                    "movement_type": {"type": "string", "enum": ["MANUAL_IN", "MANUAL_OUT", "DAMAGE", "CORRECTION"]},
                                    "notes": {"type": "string"}
                                }
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "Stock adjusted with audit trail"}}
            }
        },
        "/api/inventory/movements/": {
            "get": {
                "summary": "List Stock Audit Ledger Movements",
                "tags": ["Inventory & Supply"],
                "responses": {"200": {"description": "Audited movement history"}}
            }
        },
        "/api/sales/quote/": {
            "post": {
                "summary": "Calculate Quote / Cart Totals",
                "tags": ["Sales & POS"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["items"],
                                "properties": {
                                    "items": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "product_id": {"type": "integer"},
                                                "quantity": {"type": "number"}
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "Subtotal, tax breakdown, and total"}}
            }
        },
        "/api/sales/checkout/": {
            "post": {
                "summary": "POS Checkout & Invoice Generation",
                "tags": ["Sales & POS"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["items", "payment_method"],
                                "properties": {
                                    "items": {"type": "array"},
                                    "payment_method": {"type": "string", "enum": ["CASH", "CARD", "UPI"]},
                                    "customer_id": {"type": "integer"},
                                    "amount_paid": {"type": "number"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "200": {"description": "Sale completed, invoice generated, stock decremented atomically"},
                    "400": {"description": "Validation error (insufficient stock, invalid price)"}
                }
            }
        },
        "/api/sales/returns/": {
            "get": {
                "summary": "List Return Requests",
                "tags": ["Returns & Refunds"],
                "responses": {"200": {"description": "List of return requests for current tenant"}}
            },
            "post": {
                "summary": "Submit Return Request",
                "tags": ["Returns & Refunds"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["invoice_number", "items"],
                                "properties": {
                                    "invoice_number": {"type": "string"},
                                    "reason": {"type": "string", "enum": ["DAMAGED", "WRONG_PRODUCT", "EXPIRED", "QUALITY_ISSUE", "CUSTOMER_CHANGED_MIND", "OTHER"]},
                                    "notes": {"type": "string"},
                                    "items": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "sale_item_id": {"type": "integer"},
                                                "quantity": {"type": "number"}
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                },
                "responses": {"201": {"description": "Return request created in PENDING state"}}
            }
        },
        "/api/sales/returns/{id}/approve/": {
            "post": {
                "summary": "Approve Return Request (Manager Only)",
                "tags": ["Returns & Refunds"],
                "description": "Restores product stock via StockService, records refund ledger, and marks return COMPLETED. Preserves historical sale.",
                "responses": {"200": {"description": "Return approved and restocked"}}
            }
        },
        "/api/sales/returns/{id}/reject/": {
            "post": {
                "summary": "Reject Return Request (Manager Only)",
                "tags": ["Returns & Refunds"],
                "responses": {"200": {"description": "Return rejected. Stock remains unchanged."}}
            }
        },
        "/api/pos/scanner-session/create/": {
            "post": {
                "summary": "Create Mobile Phone Scanner Session",
                "tags": ["Phone Scanner"],
                "description": "Generates a temporary session token and pairing QR code for the cashier terminal.",
                "responses": {"200": {"description": "Session created with token, pairing code, and URL"}}
            }
        },
        "/api/pos/scanner-session/{token}/poll/": {
            "get": {
                "summary": "Poll Incoming Phone Scans",
                "tags": ["Phone Scanner"],
                "description": "POS polls this endpoint to consume pending barcode scans sent by the paired phone.",
                "responses": {"200": {"description": "Returns list of unconsumed scans and connection status"}}
            }
        },
        "/api/pos/scanner-session/{token}/scan/": {
            "post": {
                "summary": "Submit Scanned Barcode from Phone",
                "tags": ["Phone Scanner"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["barcode"],
                                "properties": {
                                    "barcode": {"type": "string"}
                                }
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "Barcode queued for POS terminal"}}
            }
        },
        "/api/ai/ask/": {
            "post": {
                "summary": "Ask Natural-Language AI Business Assistant",
                "tags": ["AI Business Intelligence"],
                "description": "Authoritative read-only business question answerer. Answers questions like 'How much did we sell today?', 'What are our top products?', 'Compare this week with last week.'",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["question"],
                                "properties": {
                                    "question": {"type": "string"}
                                }
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "Answer, intent classification, and structured data"}}
            }
        },
        "/api/ai/insights/": {
            "get": {
                "summary": "Fetch AI Sales Insights & Demand Forecasting",
                "tags": ["AI Business Intelligence"],
                "responses": {"200": {"description": "Automated sales trends, velocity indicators, and reorder runways"}}
            }
        },
        "/api/telegram/webhook/": {
            "post": {
                "summary": "Telegram Bot Webhook / Command Processing",
                "tags": ["Telegram Reporting"],
                "responses": {"200": {"description": "Command executed and response generated"}}
            }
        },
        "/api/reports/sales-summary/": {
            "get": {
                "summary": "Sales Summary Analytics Report",
                "tags": ["Reports & Analytics"],
                "responses": {"200": {"description": "Time-based revenue aggregations"}}
            }
        },
        "/api/reports/low-stock/": {
            "get": {
                "summary": "Low Stock Inventory Report",
                "tags": ["Reports & Analytics"],
                "responses": {"200": {"description": "Items below replenishment safety thresholds"}}
            }
        }
    }
}


def openapi_schema_view(request):
    """
    Returns OpenAPI 3.0 specification as JSON.
    """
    return JsonResponse(OPENAPI_SCHEMA, json_dumps_params={"indent": 2})


def swagger_docs_view(request):
    """
    Renders interactive Swagger UI documentation.
    """
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Retail POS & Billing System — API Documentation</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
    <style>
        html { box-sizing: border-box; overflow: -moz-scrollbars-vertical; overflow-y: scroll; }
        *, *:before, *:after { box-sizing: inherit; }
        body { margin: 0; background: #0b1120; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        .topbar-header {
            background: #111827;
            border-bottom: 1px solid #1f2937;
            padding: 14px 28px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .topbar-title {
            color: #f9fafb;
            font-size: 16px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .topbar-badge {
            background: rgba(16, 185, 129, 0.2);
            color: #10b981;
            border: 1px solid rgba(16, 185, 129, 0.4);
            font-size: 11px;
            font-weight: 700;
            padding: 2px 8px;
            border-radius: 9999px;
        }
        .swagger-ui {
            filter: invert(88%) hue-rotate(180deg);
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
        }
        .swagger-ui .topbar { display: none; }
    </style>
</head>
<body>
    <div class="topbar-header">
        <div class="topbar-title">
            <span>🏪 Retail POS & Billing System</span>
            <span class="topbar-badge">OpenAPI 3.0</span>
        </div>
        <div>
            <a href="/dashboard/" style="color: #9ca3af; text-decoration: none; font-size: 13px; margin-right: 16px;">← Back to POS Dashboard</a>
            <a href="/api/schema/" target="_blank" style="color: #3b82f6; text-decoration: none; font-size: 13px; font-weight: 600;">Download JSON Spec ↗</a>
        </div>
    </div>
    <div id="swagger-ui"></div>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
        window.onload = function() {
            window.ui = SwaggerUIBundle({
                url: "/api/schema/",
                dom_id: '#swagger-ui',
                deepLinking: true,
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIBundle.SwaggerUIStandalonePreset
                ],
                layout: "BaseLayout"
            });
        };
    </script>
</body>
</html>
"""
    return HttpResponse(html, content_type="text/html")
