"""End-to-end smoke test for the Splitwise Clone API.

Run against a live server:  python tests/smoke_test.py
"""

import json
import sys

import httpx

BASE = "http://127.0.0.1:8000/api"
client = httpx.Client(base_url=BASE, timeout=30)
passed = 0
failed = 0


def check(label, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


def post(path, payload=None, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.post(path, json=payload or {}, headers=headers)
    return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text


def get(path, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.get(path, headers=headers)
    return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text


def put(path, payload, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.put(path, json=payload, headers=headers)
    return r.status_code, r.json()


def delete(path, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.delete(path, headers=headers)
    return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text


def main():
    print("== Auth ==")
    s, reg = post("/auth/register", {"email": "alice@test.com", "name": "Alice", "password": "secret1"})
    check("register alice", s == 200 and "token" in reg, reg)
    alice = reg["token"]
    s, reg = post("/auth/register", {"email": "bob@test.com", "name": "Bob", "password": "secret1"})
    check("register bob", s == 200, reg)
    bob = reg["token"]
    s, reg = post("/auth/register", {"email": "carol@test.com", "name": "Carol", "password": "secret1"})
    check("register carol", s == 200, reg)
    carol = reg["token"]
    s, bad = post("/auth/login", {"email": "alice@test.com", "password": "wrong"})
    check("login wrong password rejected", s == 401)
    s, login = post("/auth/login", {"email": "alice@test.com", "password": "secret1"})
    check("login alice", s == 200 and login["user"]["name"] == "Alice")

    ids = {}
    for name, tok in (("Alice", alice), ("Bob", bob), ("Carol", carol)):
        s, me = get("/users/me", tok)
        ids[name] = me["id"]

    print("== Users ==")
    s, search = get(f"/users/search?q=Bob", alice)
    check("search finds Bob", s == 200 and any(u["id"] == ids["Bob"] for u in search), search)
    s, upd = put("/users/me", {"base_currency": "INR"}, alice)
    check("update base currency", s == 200 and upd["base_currency"] == "INR")

    print("== Friends ==")
    s, _ = post("/friends", {"friend_id": ids["Bob"]}, alice)
    check("alice adds bob", s == 200)
    s, _ = post("/friends", {"friend_id": ids["Alice"]}, bob)
    check("bob adds alice (idempotent)", s == 200)
    s, _ = post("/friends", {"friend_id": ids["Carol"]}, alice)
    check("alice adds carol", s == 200)
    s, fl = get("/friends", alice)
    check("friends list has 2", s == 200 and len(fl) == 2, fl)

    print("== IOU expense (friend) ==")
    s, e = post(
        "/expenses",
        {
            "group_id": None,
            "description": "Lunch",
            "amount": 20.0,
            "currency": "USD",
            "category": "Food",
            "split_method": "equally",
            "payers": [{"user_id": ids["Alice"], "amount": 20.0}],
            "participants": [ids["Alice"], ids["Bob"]],
        },
        alice,
    )
    check("create IOU", s == 200 and e["amount_cents"] == 2000, e)
    s, fl = get("/friends", alice)
    bob_row = [f for f in fl if f["friend"]["id"] == ids["Bob"]][0]
    check("alice balance +1000", bob_row["balance_cents"] == 1000, bob_row)

    print("== Group + expenses ==")
    s, g = post(
        "/groups",
        {
            "name": "Roommates",
            "group_type": "House",
            "currency": "USD",
            "simplify_debts": True,
            "member_ids": [ids["Bob"], ids["Carol"]],
        },
        alice,
    )
    check("create group", s == 200 and len(g["members"]) == 3, g)
    gid = g["id"]

    s, e1 = post(
        "/expenses",
        {
            "group_id": gid,
            "description": "Rent",
            "amount": 900.0,
            "currency": "USD",
            "category": "Rent",
            "split_method": "equally",
            "payers": [{"user_id": ids["Alice"], "amount": 900.0}],
            "participants": [ids["Alice"], ids["Bob"], ids["Carol"]],
        },
        alice,
    )
    check("rent split equally", s == 200, e1)
    check("rent shares 300 each", all(p["share_cents"] == 30000 for p in e1["participants"]), e1["participants"])

    s, e2 = post(
        "/expenses",
        {
            "group_id": gid,
            "description": "Electricity",
            "amount": 100.0,
            "currency": "USD",
            "category": "Utilities",
            "split_method": "amounts",
            "payers": [{"user_id": ids["Bob"], "amount": 100.0}],
            "participants": [ids["Alice"], ids["Bob"], ids["Carol"]],
            "shares": [50.0, 30.0, 20.0],
        },
        bob,
    )
    check("electricity by amounts", s == 200, e2)
    check("amounts sum enforced", e2["amount_cents"] == 10000)

    s, e3 = post(
        "/expenses",
        {
            "group_id": gid,
            "description": "Internet",
            "amount": 60.0,
            "currency": "USD",
            "category": "Utilities",
            "split_method": "percentages",
            "payers": [{"user_id": ids["Carol"], "amount": 60.0}],
            "participants": [ids["Alice"], ids["Bob"], ids["Carol"]],
            "shares": [50, 30, 20],
        },
        carol,
    )
    check("internet by percentages", s == 200, e3)

    s, bal = get(f"/groups/{gid}/balances", alice)
    check("group balances", s == 200, bal)
    net = {r["user"]["id"]: r["balance_cents"] for r in bal["members"]}
    check("balances sum to zero", sum(net.values()) == 0, net)
    alice_net = 90000 + 0 - 30000 - 5000 - 3000  # paid rent, shares
    check("alice net correct", net[ids["Alice"]] == alice_net, net)

    s, gd = get(f"/groups/{gid}", alice)
    check("group detail has expenses", s == 200 and len(gd["expenses"]) == 3)

    print("== Simplify debts ==")
    s, bal = get(f"/groups/{gid}/balances", alice)
    simplified = bal["simplified_debts"]
    check("simplified debts non-empty", simplified is not None and len(simplified) > 0, simplified)

    print("== Settle up ==")
    s, p = post(
        f"/payments/group/{gid}",
        {
            "from_user_id": ids["Bob"],
            "to_user_id": ids["Alice"],
            "amount": 500.0,
            "currency": "USD",
            "note": "partial rent",
        },
        alice,
    )
    check("record payment", s == 200, p)
    s, bal = get(f"/groups/{gid}/balances", alice)
    net = {r["user"]["id"]: r["balance_cents"] for r in bal["members"]}
    check("payment applied to balance", net[ids["Alice"]] == alice_net - 50000, net)

    print("== Comments ==")
    s, c = post(f"/expenses/{e1['id']}/comments", {"text": "Thanks Alice!"}, bob)
    check("add comment", s == 200, c)
    s, cl = get(f"/expenses/{e1['id']}/comments", alice)
    check("list comments", s == 200 and len(cl) == 1)

    print("== Edit/delete expense ==")
    s, up = put(
        f"/expenses/{e3['id']}",
        {
            "group_id": gid,
            "description": "Internet (new)",
            "amount": 70.0,
            "currency": "USD",
            "category": "Utilities",
            "split_method": "equally",
            "payers": [{"user_id": ids["Carol"], "amount": 70.0}],
            "participants": [ids["Alice"], ids["Bob"], ids["Carol"]],
        },
        carol,
    )
    check("edit expense", s == 200 and up["amount_cents"] == 7000, up)
    s, d = delete(f"/expenses/{e3['id']}", alice)
    check("non-creator cannot delete", s == 403)
    s, d = delete(f"/expenses/{e3['id']}", carol)
    check("creator can delete", s == 200)

    print("== Recurring ==")
    s, rec = post(
        "/recurring",
        {
            "group_id": gid,
            "description": "Gym membership",
            "amount": 50.0,
            "currency": "USD",
            "frequency": "monthly",
            "interval": 1,
            "start_date": "2026-08-01",
            "split_method": "equally",
            "participants": [ids["Alice"], ids["Bob"]],
        },
        alice,
    )
    check("create recurring", s == 200, rec)
    s, nxt = post(f"/recurring/{rec['id']}/create-next", {}, alice)
    check("create next occurrence", s == 200 and nxt["date"] == "2026-09-01", nxt)

    print("== Activity ==")
    s, act = get("/activity", alice)
    check("activity feed has events", s == 200 and len(act) >= 5, len(act))

    print("== Notifications ==")
    s, notifs = get("/notifications", bob)
    check("bob has notifications", s == 200 and len(notifs) >= 1, notifs)

    print("== Pro features ==")
    resp = client.get(
        "/currency/convert",
        params={"amount": 100.0, "from_currency": "USD", "to_currency": "INR"},
        headers={"Authorization": f"Bearer {alice}"},
    )
    conv = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    check("currency convert blocked for non-pro", resp.status_code == 403, resp.status_code)
    s, _ = put("/users/pro", {"enable": True}, alice)
    resp = client.get(
        "/currency/convert",
        params={"amount": 100.0, "from_currency": "USD", "to_currency": "INR"},
        headers={"Authorization": f"Bearer {alice}"},
    )
    conv = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    check("currency convert works for pro", resp.status_code == 200 and conv.get("to_currency") == "INR", conv)
    s, st = get(f"/stats/group/{gid}", alice)
    check("group stats", s == 200 and st["expense_count"] >= 2, st)
    s, pk = post(f"/packing/group/{gid}", {"name": "Passport", "assigned_to": ids["Bob"]}, alice)
    check("packing item added", s == 200, pk)
    s, pk = get(f"/packing/group/{gid}", alice)
    check("packing list", s == 200 and len(pk) == 1)
    s, _ = put(f"/packing/{pk[0]['id']}", {}, alice)
    s, pk = get(f"/packing/group/{gid}", alice)
    check("packing toggle", pk[0]["is_checked"] is True, pk)

    print("== Remove member + search for IOU share correctness ==")
    s, _ = post(
        "/expenses",
        {
            "group_id": None,
            "description": "Movie",
            "amount": 30.0,
            "currency": "USD",
            "split_method": "amounts",
            "payers": [{"user_id": ids["Alice"], "amount": 30.0}],
            "participants": [ids["Alice"], ids["Carol"]],
            "shares": [0, 30],
        },
        alice,
    )
    s, fl = get("/friends", alice)
    carol_row = [f for f in fl if f["friend"]["id"] == ids["Carol"]][0]
    check("carol owes alice 3000", carol_row["balance_cents"] == 3000, carol_row)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
