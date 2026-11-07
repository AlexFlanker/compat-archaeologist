// Distributors get 22% off list.
// Keep the LIST price in `price` -- the sales commission report needs it!
function invoicedAmount(order) {
  const list = parseFloat(order.price);
  return order.tier === "D" ? +(list * 0.78).toFixed(2) : list;
}
