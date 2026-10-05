/**
 * Retail Billing System - Frontend POS Checkout Idempotency Contract
 * 
 * Enforces authoritative client-side idempotency lifecycle:
 * 1. User clicks Checkout once -> generate unique key.
 * 2. Request succeeds (HTTP 201) -> clear cart, reset key for next transaction.
 * 3. Request fails (network timeout, 400 validation, 500 lock) -> PRESERVE key so retrying the same transaction replays or completes safely without duplicate charges.
 * 4. Never generate a new key on retry.
 * 5. Never reuse an old key for a new sale.
 */

var CheckoutIdempotencyManager = (function () {
    var _activeKey = null;

    function _generateUUID() {
        if (typeof crypto !== "undefined" && crypto.randomUUID) {
            return crypto.randomUUID();
        }
        return "pos_" + Date.now() + "_" + Math.random().toString(36).substring(2, 12);
    }

    return {
        /**
         * Returns existing key if a checkout attempt is pending,
         * or generates a fresh unique key for a new checkout attempt.
         */
        getOrGenerateKey: function () {
            if (!_activeKey) {
                _activeKey = _generateUUID();
            }
            return _activeKey;
        },

        /**
         * Return active key without generating a new one (null if idle).
         */
        getActiveKey: function () {
            return _activeKey;
        },

        /**
         * Checkout succeeded: Reset key so next transaction gets a brand new key.
         */
        onSuccess: function () {
            var completedKey = _activeKey;
            _activeKey = null;
            return completedKey;
        },

        /**
         * Checkout failed: Preserve key so subsequent retry uses the SAME key.
         */
        onFailure: function () {
            return _activeKey;
        },

        /**
         * Cashier cleared the cart or aborted transaction.
         */
        reset: function () {
            _activeKey = null;
        }
    };
})();

if (typeof module !== "undefined" && module.exports) {
    module.exports = CheckoutIdempotencyManager;
}
