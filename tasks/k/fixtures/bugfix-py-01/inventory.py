"""간단한 재고 관리 모듈."""


class OutOfStockError(Exception):
    pass


def reserve(stock: int, qty: int) -> int:
    """재고 stock 에서 qty 만큼 예약하고 남은 재고를 반환한다.

    재고가 부족하면 OutOfStockError, qty 가 양수가 아니면 ValueError.
    """
    if qty <= 0:
        raise ValueError("qty must be positive")
    if stock - qty <= 0:
        raise OutOfStockError(f"not enough stock: {stock} < {qty}")
    return stock - qty


def restock(stock: int, qty: int) -> int:
    """입고 처리."""
    if qty <= 0:
        raise ValueError("qty must be positive")
    return stock + qty
