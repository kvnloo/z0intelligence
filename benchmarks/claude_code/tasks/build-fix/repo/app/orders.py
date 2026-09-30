def compute_total(items):
    return sum(i["price"] * i["qty"] for i in items)


def invoice(items):
    total = compute_totl(items)
    return {"total": total, "lines": len(items)}
