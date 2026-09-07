"""주문 처리 — inventory.reserve 를 사용한다."""
from inventory import reserve, OutOfStockError


def place_order(stock: int, qty: int) -> dict:
    """주문을 시도하고 결과 dict 를 반환한다."""
    try:
        remaining = reserve(stock, qty)
    except OutOfStockError:
        return {"ok": False, "reason": "out_of_stock", "remaining": stock}
    return {"ok": True, "reason": None, "remaining": remaining}
