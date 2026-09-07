"""Order processing — uses inventory.reserve."""
from inventory import reserve, OutOfStockError


def place_order(stock: int, qty: int) -> dict:
    """Attempt an order and return a result dict."""
    try:
        remaining = reserve(stock, qty)
    except OutOfStockError:
        return {"ok": False, "reason": "out_of_stock", "remaining": stock}
    return {"ok": True, "reason": None, "remaining": remaining}
