"""A simple inventory management module."""


class OutOfStockError(Exception):
    pass


def reserve(stock: int, qty: int) -> int:
    """Reserve qty units out of stock and return the remaining stock.

    Raises OutOfStockError if stock is insufficient, ValueError if qty is not positive.
    """
    if qty <= 0:
        raise ValueError("qty must be positive")
    if stock - qty <= 0:
        raise OutOfStockError(f"not enough stock: {stock} < {qty}")
    return stock - qty


def restock(stock: int, qty: int) -> int:
    """Handle a restock."""
    if qty <= 0:
        raise ValueError("qty must be positive")
    return stock + qty
