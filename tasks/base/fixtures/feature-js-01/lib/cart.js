// Shopping cart module
// cart shape: { items: [{ name, price, qty }] }

function total(cart) {
  return cart.items.reduce((sum, it) => sum + it.price * it.qty, 0);
}

module.exports = { total };
