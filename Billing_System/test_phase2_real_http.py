import requests
import json
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = "http://127.0.0.1:8000"

def get_csrf(session, url):
    r = session.get(url)
    return session.cookies.get("csrftoken") or ""

def test_role_flow(username, password, expected_role, allowed_urls, forbidden_urls):
    print(f"\n=======================================================")
    print(f"TESTING ROLE: {expected_role} (User: {username})")
    print(f"=======================================================")
    s = requests.Session()
    
    # 1. Unauthenticated direct access check
    r_unauth = s.get(f"{BASE_URL}/cart/", allow_redirects=False)
    print(f"[*] Unauthenticated /cart/ access -> Status: {r_unauth.status_code} (Redirect: {r_unauth.headers.get('Location', 'None')})")
    assert r_unauth.status_code in [302, 403], f"Unauthenticated access not restricted! Status: {r_unauth.status_code}"

    # 2. Login Page
    login_page = s.get(f"{BASE_URL}/accounts/")
    assert login_page.status_code == 200, f"Login page failed: {login_page.status_code}"
    csrftoken = s.cookies.get("csrftoken")
    
    # 3. Perform Login
    login_resp = s.post(
        f"{BASE_URL}/accounts/",
        data={"username": username, "password": password, "csrfmiddlewaretoken": csrftoken},
        headers={"Referer": f"{BASE_URL}/accounts/"},
        allow_redirects=True
    )
    print(f"[*] Login POST -> Status: {login_resp.status_code}, Final URL: {login_resp.url}")
    assert login_resp.status_code == 200
    assert "Invalid username" not in login_resp.text, f"Login failed for {username}!"
    print(f"[✓] Login successful for {username}. Session cookie set: {'sessionid' in s.cookies}")

    # 4. Test Allowed Pages
    for path in allowed_urls:
        resp = s.get(f"{BASE_URL}{path}", allow_redirects=False)
        print(f"   -> Allowed Page {path}: Status {resp.status_code}")
        # Could be 200 or 302 to authorized destination
        if resp.status_code == 302:
            loc = resp.headers.get("Location")
            print(f"      Redirects to: {loc}")
            resp_dest = s.get(f"{BASE_URL}{loc}")
            assert resp_dest.status_code == 200, f"Allowed page {path} redirected to error {resp_dest.status_code}"
        else:
            assert resp.status_code == 200, f"Allowed page {path} returned {resp.status_code} instead of 200!"

    # 5. Test Forbidden Pages
    for path in forbidden_urls:
        resp = s.get(f"{BASE_URL}{path}", allow_redirects=False)
        print(f"   -> Forbidden Page {path}: Status {resp.status_code}")
        assert resp.status_code in [403, 302], f"Security failure! Role {expected_role} accessed forbidden page {path} with status {resp.status_code}!"
        if resp.status_code == 302:
            # Must not redirect to the forbidden page itself
            assert "/accounts/" in resp.headers.get("Location", "") or resp.headers.get("Location") != path

    # 6. Logout
    logout_resp = s.get(f"{BASE_URL}/accounts/logout/", allow_redirects=False)
    print(f"[*] Logout -> Status: {logout_resp.status_code}")
    # After logout, accessing allowed url should redirect or 403
    r_after = s.get(f"{BASE_URL}/cart/", allow_redirects=False)
    print(f"[*] Post-logout access to /cart/ -> Status: {r_after.status_code}")
    assert r_after.status_code in [302, 403], "Session not cleared after logout!"
    print(f"[✓] Role {expected_role} verified successfully!")

if __name__ == "__main__":
    # Test OWNER (audit_owner_a)
    test_role_flow(
        "audit_owner_a", "Password@123", "OWNER",
        allowed_urls=["/dashboard/", "/cart/", "/employee/", "/reports/", "/purchase/", "/product/", "/returns/", "/settings/scanner/", "/settings/telegram/", "/settings/company/"],
        forbidden_urls=[]
    )

    # Test MANAGER (audit_mgr_a)
    test_role_flow(
        "audit_mgr_a", "Password@123", "MANAGER",
        allowed_urls=["/manager/dashboard/", "/cart/", "/product/", "/returns/", "/purchase/", "/employee/", "/reports/"],
        forbidden_urls=["/settings/telegram/", "/settings/company/"]  # Company settings and Telegram pairing are Owner-only
    )

    # Test CASHIER (audit_cashier_a)
    test_role_flow(
        "audit_cashier_a", "Password@123", "CASHIER",
        allowed_urls=["/cashier/dashboard/", "/cart/", "/returns/"],
        forbidden_urls=["/dashboard/", "/employee/", "/reports/", "/settings/scanner/", "/settings/telegram/", "/purchase/", "/settings/company/"]
    )

    # Test WORKER (audit_worker_a)
    test_role_flow(
        "audit_worker_a", "Password@123", "WORKER",
        allowed_urls=["/product/"],
        forbidden_urls=["/product/add/", "/dashboard/", "/manager/dashboard/", "/cashier/dashboard/", "/employee/", "/reports/", "/cart/", "/settings/scanner/", "/purchase/", "/returns/"]
    )
    print("\n=======================================================")
    print("ALL 4 ROLES HTTP QA PASSED WITH STRICT ENFORCEMENT!")
    print("=======================================================")
