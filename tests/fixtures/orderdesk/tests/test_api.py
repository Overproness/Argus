def test_quote(client):
    resp = client.get("/quote", params={"skus": "SKU-1,SKU-2"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 10.3


def test_checkout_and_summary(client):
    resp = client.post(
        "/checkout",
        json={"user_id": "u1", "lines": [{"sku": "SKU-1", "qty": 2}, {"sku": "SKU-3", "qty": 1}], "coupon": "WELCOME-10"},
    )
    assert resp.status_code == 200
    order_id = resp.json()["order_id"]
    summary = client.get(f"/orders/{order_id}").json()
    assert [l["sku"] for l in summary["lines"]] == ["SKU-1", "SKU-3"]


def test_bad_coupon_rejected(client):
    resp = client.post(
        "/checkout",
        json={"user_id": "u1", "lines": [{"sku": "SKU-1", "qty": 1}], "coupon": "bad coupon!"},
    )
    assert resp.status_code == 400


def test_stock(client):
    assert client.get("/stock/SKU-4").json()["available"] == 7


def test_product_page(client):
    body = client.get("/products/SKU-5").json()
    assert body["available"] == 7
    assert body["reviews"]


def test_products_batch(client):
    body = client.get("/products", params={"skus": "SKU-1,SKU-2,SKU-9"}).json()
    assert len(body["products"]) == 3


def test_related(client):
    related = client.get("/products/SKU-0/related").json()["related"]
    assert len(related) == 5


def test_category_path(client):
    assert client.get("/categories/3/path").json()["path"] == ["All", "Kitchen", "Cookware"]


def test_search(client):
    assert client.get("/search", params={"q": "kettle"}).json()["results"]


def test_delivery_slots(client):
    assert 9 not in client.get("/delivery-slots").json()["sun"]


def test_login(client):
    ok = client.post("/login", json={"email": "demo@orderdesk.test", "password": "correct horse"})
    assert ok.status_code == 200
    bad = client.post("/login", json={"email": "demo@orderdesk.test", "password": "nope"})
    assert bad.status_code == 401


def test_export(client):
    assert isinstance(client.get("/admin/orders/export").json()["orders"], list)
