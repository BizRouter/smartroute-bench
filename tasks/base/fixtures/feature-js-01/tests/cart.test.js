const { test } = require('node:test');
const assert = require('assert');
const { total, applyCoupon } = require('../lib/cart.js');

test('total sums items', () => {
  const cart = { items: [{ name: 'a', price: 1000, qty: 2 }, { name: 'b', price: 500, qty: 1 }] };
  assert.strictEqual(total(cart), 2500);
});

test('total of empty cart is 0', () => {
  assert.strictEqual(total({ items: [] }), 0);
});

test('percent coupon discounts total', () => {
  const cart = { items: [{ name: 'a', price: 10000, qty: 1 }] };
  assert.strictEqual(applyCoupon(cart, { type: 'percent', value: 10 }), 9000);
});

test('fixed coupon discounts total', () => {
  const cart = { items: [{ name: 'a', price: 12000, qty: 1 }] };
  assert.strictEqual(applyCoupon(cart, { type: 'fixed', value: 5000 }), 7000);
});

test('fixed coupon floors at zero', () => {
  const cart = { items: [{ name: 'a', price: 3000, qty: 1 }] };
  assert.strictEqual(applyCoupon(cart, { type: 'fixed', value: 5000 }), 0);
});

test('percent 100 empties the bill', () => {
  const cart = { items: [{ name: 'a', price: 9900, qty: 3 }] };
  assert.strictEqual(applyCoupon(cart, { type: 'percent', value: 100 }), 0);
});

test('percent out of range throws', () => {
  const cart = { items: [{ name: 'a', price: 1000, qty: 1 }] };
  assert.throws(() => applyCoupon(cart, { type: 'percent', value: 0 }));
  assert.throws(() => applyCoupon(cart, { type: 'percent', value: 101 }));
});

test('negative fixed throws', () => {
  const cart = { items: [{ name: 'a', price: 1000, qty: 1 }] };
  assert.throws(() => applyCoupon(cart, { type: 'fixed', value: -1 }));
});
